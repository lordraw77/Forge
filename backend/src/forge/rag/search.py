"""Client for the external BM25 engine, OpenSearch (ADR-016).

Forge talks to OpenSearch over its REST API with httpx: TLS verified against the deployment's
CA, basic auth as the `forge` user (role `forge_app`, indices `forge-*` only).

OpenSearch has no row-level security. **Every query is built by `bm25_query` in this module**:
it always filters on the project (term + routing), the knowledge bases and the caller's ACL
groups; no caller-supplied query DSL is accepted. The CI isolation test covers it.
"""

from __future__ import annotations

import json
import ssl
import uuid
from collections.abc import Sequence
from typing import Any

import httpx

from forge.core.settings import Settings

INDEX_VERSION = 1
PUBLIC = "__public__"  # ACL sentinel: an empty ACL indexes no term (spike E0.5b)

MAPPING: dict[str, Any] = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0, "refresh_interval": "1s"},
    "mappings": {
        "dynamic": "strict",
        "_routing": {"required": True},
        "properties": {
            "project_id": {"type": "keyword"},
            "kb_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "acl_groups": {"type": "keyword"},
            "heading_path": {"type": "text"},
            "content": {
                "type": "text",
                "fields": {
                    "it": {"type": "text", "analyzer": "italian"},
                    "en": {"type": "text", "analyzer": "english"},
                },
            },
            "page": {"type": "integer"},
        },
    },
}


def acl_terms(groups: Sequence[str]) -> list[str]:
    """Chunk ACL as indexed: the document's groups, or the public sentinel when empty."""
    return sorted(set(groups)) or [PUBLIC]


def reader_groups(groups: Sequence[str]) -> list[str]:
    """Groups a reader matches: their own plus project-wide documents."""
    return sorted({*groups, PUBLIC})


def bm25_query(
    text: str,
    project_id: uuid.UUID,
    kb_ids: Sequence[uuid.UUID],
    groups: Sequence[str],
    size: int = 40,
) -> dict[str, Any]:
    """The only place a search query is built: project, KB and ACL are always filters."""
    return {
        "size": size,
        "_source": False,
        "track_total_hits": False,
        "query": {
            "bool": {
                "must": {
                    "multi_match": {
                        "query": text,
                        "type": "most_fields",
                        "fields": ["content", "content.it", "content.en", "heading_path^0.5"],
                    }
                },
                "filter": [
                    {"term": {"project_id": str(project_id)}},
                    {"terms": {"kb_id": [str(k) for k in kb_ids]}},
                    {"terms": {"acl_groups": reader_groups(groups)}},
                ],
            }
        },
    }


class SearchClient:
    def __init__(self, settings: Settings, *, timeout_s: float = 10.0) -> None:
        verify: ssl.SSLContext | bool = True
        if settings.search_ca_file:
            verify = ssl.create_default_context(cafile=settings.search_ca_file)
        auth = None
        if settings.search_password is not None:
            auth = httpx.BasicAuth(
                settings.search_username, settings.search_password.get_secret_value()
            )
        self._http = httpx.AsyncClient(
            base_url=settings.search_url, auth=auth, verify=verify, timeout=timeout_s
        )
        self.alias = settings.search_index
        self.index = f"{settings.search_index}-v{INDEX_VERSION}"

    async def health(self) -> dict[str, Any]:
        """Cluster health; raises when the engine is unreachable or refuses the credentials."""
        resp = await self._http.get("/_cluster/health")
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        if data.get("status") not in ("green", "yellow"):
            raise RuntimeError(f"search cluster status {data.get('status')}")
        return data

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return await self._http.request(method, path, **kwargs)

    async def ensure_index(self) -> None:
        """Create the current index version and point the alias at it (idempotent).

        Index-scoped alias calls only: the `forge_app` role has no cluster-wide alias read.
        """
        concrete = await self._http.get(f"/{self.alias}/_mapping")
        if concrete.status_code == 200 and self.alias in concrete.json():
            raise RuntimeError(
                f"'{self.alias}' is an index, not an alias; delete it and restart (ADR-016)"
            )
        if (await self._http.head(f"/{self.index}")).status_code == 404:
            resp = await self._http.put(f"/{self.index}", json=MAPPING)
            if resp.status_code >= 400 and "resource_already_exists" not in resp.text:
                resp.raise_for_status()
        alias = await self._http.head(f"/{self.index}/_alias/{self.alias}")
        if alias.status_code == 404:
            actions = {"actions": [{"add": {"index": self.index, "alias": self.alias}}]}
            (await self._http.post("/_aliases", json=actions)).raise_for_status()
        elif alias.status_code >= 400:
            alias.raise_for_status()

    async def index_chunks(
        self, project_id: uuid.UUID, docs: list[dict[str, Any]], refresh: str = "wait_for"
    ) -> None:
        """Bulk index chunks with `_id` = chunk id, so retries are idempotent."""
        if not docs:
            return
        lines: list[str] = []
        for doc in docs:
            source = dict(doc)
            chunk_id = source.pop("id")
            lines.append(json.dumps({"index": {"_id": str(chunk_id), "routing": str(project_id)}}))
            lines.append(json.dumps(source))
        resp = await self._http.post(
            f"/{self.alias}/_bulk",
            content="\n".join(lines) + "\n",
            headers={"Content-Type": "application/x-ndjson"},
            params={"refresh": refresh},
            timeout=120,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            first = next(i for i in body["items"] if i["index"].get("error"))
            raise RuntimeError(f"bulk index error: {first['index']['error']}")

    async def refresh(self) -> None:
        """Make everything indexed so far searchable now (the end of a bulk build)."""
        (await self._http.post(f"/{self.alias}/_refresh")).raise_for_status()

    async def delete_ids(
        self, project_id: uuid.UUID, ids: Sequence[uuid.UUID], refresh: str = "wait_for"
    ) -> None:
        if not ids:
            return
        lines = [json.dumps({"delete": {"_id": str(i), "routing": str(project_id)}}) for i in ids]
        resp = await self._http.post(
            f"/{self.alias}/_bulk",
            content="\n".join(lines) + "\n",
            headers={"Content-Type": "application/x-ndjson"},
            params={"refresh": refresh},
            timeout=120,
        )
        resp.raise_for_status()

    async def delete_where(
        self, project_id: uuid.UUID, field: str, values: Sequence[uuid.UUID]
    ) -> None:
        """Delete every chunk of the given knowledge bases or documents of one project."""
        if field not in ("kb_id", "document_id") or not values:
            raise ValueError("delete by kb_id or document_id only")
        query = {
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"project_id": str(project_id)}},
                        {"terms": {field: [str(v) for v in values]}},
                    ]
                }
            }
        }
        resp = await self._http.post(
            f"/{self.alias}/_delete_by_query",
            json=query,
            params={"routing": str(project_id), "refresh": "true", "conflicts": "proceed"},
            timeout=120,
        )
        resp.raise_for_status()

    async def bm25(
        self,
        text: str,
        project_id: uuid.UUID,
        kb_ids: Sequence[uuid.UUID],
        groups: Sequence[str],
        size: int = 40,
    ) -> list[tuple[uuid.UUID, float]]:
        resp = await self._http.post(
            f"/{self.alias}/_search",
            params={"routing": str(project_id)},
            json=bm25_query(text, project_id, kb_ids, groups, size),
        )
        resp.raise_for_status()
        return [(uuid.UUID(h["_id"]), float(h["_score"])) for h in resp.json()["hits"]["hits"]]

    async def aclose(self) -> None:
        await self._http.aclose()
