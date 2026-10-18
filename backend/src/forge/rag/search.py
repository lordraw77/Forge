"""Client for the external BM25 engine, OpenSearch (ADR-016).

Forge talks to OpenSearch over its REST API with httpx: TLS verified against the deployment's
CA, basic auth as the `forge` user (role `forge_app`, indices `forge-*` only). Indexing and
queries arrive with the RAG pipeline (E1.15, E1.16); every query will carry the project, KB and
ACL filters built in one place, because OpenSearch has no row-level security.
"""

from __future__ import annotations

import ssl
from typing import Any

import httpx

from forge.core.settings import Settings


class SearchClient:
    def __init__(self, settings: Settings, *, timeout_s: float = 5.0) -> None:
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

    async def aclose(self) -> None:
        await self._http.aclose()
