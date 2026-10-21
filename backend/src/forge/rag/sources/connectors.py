"""Connectors: list what a source holds and hand over the bytes of one item (E2.13).

A connector never touches the database. It receives its configuration, its secrets and the
network policy of the project (`SourceContext`), yields `RemoteItem`s one at a time and writes the
content of the item it is asked for into a file. Every network connector applies the project's
egress allowlist and the private-address rule before it connects.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import html.parser
import json
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal
from urllib.parse import urldefrag, urljoin, urlsplit

import boto3
from botocore.config import Config as BotoConfig
from pydantic import BaseModel, ConfigDict, Field, field_validator

from forge.core import egress
from forge.core.egress import EgressError

SOURCE_TYPES = ("s3", "url", "rest", "sql", "git", "filesystem")
MAX_ITEM_BYTES = 100 * 1024 * 1024
PAGE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_ITEMS = 5000


class SourceError(Exception):
    """The source cannot be read (bad configuration, unreachable, refused by policy)."""


# ---- configuration (validated when a source is saved)
class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class S3Config(_Cfg):
    endpoint: str | None = Field(default=None, max_length=300)
    region: str = Field(default="us-east-1", max_length=40)
    bucket: str = Field(min_length=3, max_length=63)
    prefix: str = Field(default="", max_length=500)
    include: list[str] = Field(default_factory=list, max_length=20)
    max_items: int = Field(default=DEFAULT_MAX_ITEMS, ge=1, le=100_000)


class UrlConfig(_Cfg):
    start_urls: list[str] = Field(min_length=1, max_length=20)
    max_pages: int = Field(default=100, ge=1, le=500)
    max_depth: int = Field(default=2, ge=0, le=5)
    include_regex: str | None = Field(default=None, max_length=300)
    exclude_regex: str | None = Field(default=None, max_length=300)

    @field_validator("include_regex", "exclude_regex")
    @classmethod
    def _regex(cls, v: str | None) -> str | None:
        if v:
            try:
                re.compile(v)
            except re.error as exc:
                raise ValueError(f"not a valid regular expression: {exc}") from exc
        return v


class RestConfig(_Cfg):
    url: str = Field(max_length=1000)
    method: Literal["GET", "POST"] = "GET"
    body: dict[str, Any] | None = None
    items_path: str = Field(default="", max_length=200)
    id_field: str = Field(min_length=1, max_length=100)
    title_field: str | None = Field(default=None, max_length=100)
    content_fields: list[str] = Field(default_factory=list, max_length=30)
    version_field: str | None = Field(default=None, max_length=100)
    next_path: str | None = Field(default=None, max_length=200)
    max_pages: int = Field(default=20, ge=1, le=200)


class SqlSourceConfig(_Cfg):
    datasource: str = Field(min_length=1, max_length=80)
    query: str = Field(min_length=6, max_length=8000)
    id_column: str = Field(min_length=1, max_length=100)
    title_column: str | None = Field(default=None, max_length=100)
    content_columns: list[str] = Field(default_factory=list, max_length=50)
    version_column: str | None = Field(default=None, max_length=100)
    row_limit: int = Field(default=1000, ge=1, le=1000)


class GitConfig(_Cfg):
    repo_url: str = Field(max_length=500)
    ref: str | None = Field(default=None, max_length=200)
    subpath: str = Field(default="", max_length=300)
    include: list[str] = Field(default_factory=list, max_length=20)
    max_items: int = Field(default=2000, ge=1, le=20_000)


class FilesystemConfig(_Cfg):
    path: str = Field(max_length=500)
    recursive: bool = True
    include: list[str] = Field(default_factory=list, max_length=20)
    max_items: int = Field(default=DEFAULT_MAX_ITEMS, ge=1, le=100_000)


CONFIGS: dict[str, type[_Cfg]] = {
    "s3": S3Config,
    "url": UrlConfig,
    "rest": RestConfig,
    "sql": SqlSourceConfig,
    "git": GitConfig,
    "filesystem": FilesystemConfig,
}
# What each type accepts as secret (keys of the credentials object).
SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "s3": ("access_key", "secret_key"),
    "url": ("bearer_token", "headers"),
    "rest": ("bearer_token", "headers"),
    "sql": (),
    "git": ("token", "username", "password"),
    "filesystem": (),
}


def validate_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    cls = CONFIGS.get(kind)
    if cls is None:
        raise SourceError(f"Unknown source type '{kind}'")
    return cls.model_validate(config).model_dump()


# ---- contract
@dataclass
class RemoteItem:
    external_id: str
    name: str
    folder: str
    version: str
    size: int | None
    fetch: Callable[[IO[bytes]], Awaitable[None]]


@dataclass
class SourceContext:
    allowed_hosts: list[str]
    allow_private: bool
    secret: dict[str, Any] = field(default_factory=dict)
    roots: list[Path] = field(default_factory=list)
    query_rows: (
        Callable[[str, str, int], Awaitable[tuple[list[str], list[list[Any]], bool]]] | None
    ) = None

    async def check(self, url: str) -> None:
        try:
            await egress.check_url(url, self.allowed_hosts, self.allow_private)
        except EgressError as exc:
            raise SourceError(f"{exc.message} ({exc.code})") from exc

    def headers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if token := self.secret.get("bearer_token"):
            out["Authorization"] = f"Bearer {token}"
        out.update({str(k): str(v) for k, v in (self.secret.get("headers") or {}).items()})
        return out


class Connector:
    def __init__(self, config: Any, ctx: SourceContext) -> None:
        self.cfg = config
        self.ctx = ctx

    def items(self) -> AsyncIterator[RemoteItem]:
        raise NotImplementedError

    async def close(self) -> None:
        return None


def _matches(path: str, patterns: list[str]) -> bool:
    return not patterns or any(
        fnmatch.fnmatch(path, p) or fnmatch.fnmatch(path.rsplit("/", 1)[-1], p) for p in patterns
    )


def _slug(text: str, limit: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:limit] or "item"


def _short(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:10]


def _dotted(data: Any, path: str) -> Any:
    for part in [p for p in path.split(".") if p]:
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


# ---- S3 / S3-compatible
class S3Connector(Connector):
    def _client(self) -> Any:
        cfg: S3Config = self.cfg
        key, secret = self.ctx.secret.get("access_key"), self.ctx.secret.get("secret_key")
        if not key or not secret:
            raise SourceError("S3 sources need access_key and secret_key credentials")
        return boto3.client(
            "s3",
            endpoint_url=cfg.endpoint,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name=cfg.region,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,
                read_timeout=60,
            ),
        )

    async def items(self) -> AsyncIterator[RemoteItem]:
        cfg: S3Config = self.cfg
        await self.ctx.check(cfg.endpoint or f"https://s3.{cfg.region}.amazonaws.com/")
        client = self._client()
        token: str | None = None
        count = 0
        while True:
            args: dict[str, Any] = {"Bucket": cfg.bucket, "Prefix": cfg.prefix, "MaxKeys": 1000}
            if token:
                args["ContinuationToken"] = token
            try:
                page = await asyncio.to_thread(client.list_objects_v2, **args)
            except Exception as exc:
                raise SourceError(f"Cannot list the bucket: {type(exc).__name__}") from exc
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/") or not _matches(key, cfg.include):
                    continue
                count += 1
                if count > cfg.max_items:
                    raise SourceError(f"The bucket has more than {cfg.max_items} matching objects")
                rel = key[len(cfg.prefix) :].lstrip("/") if cfg.prefix else key
                folder, _, name = rel.rpartition("/")
                if obj["Size"] > MAX_ITEM_BYTES:
                    continue

                async def fetch(fh: IO[bytes], key: str = key) -> None:
                    await asyncio.to_thread(client.download_fileobj, cfg.bucket, key, fh)

                yield RemoteItem(
                    key,
                    name or rel,
                    folder,
                    f"{obj.get('ETag', '')}:{obj['Size']}",
                    obj["Size"],
                    fetch,
                )
            if not page.get("IsTruncated"):
                return
            token = page.get("NextContinuationToken")


# ---- web crawler
class _Links(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)


class UrlConnector(Connector):
    """Breadth-first crawl of the hosts of the start URLs. Pages are stored as HTML."""

    async def items(self) -> AsyncIterator[RemoteItem]:
        cfg: UrlConfig = self.cfg
        hosts = {(urlsplit(u).hostname or "").lower() for u in cfg.start_urls}
        include = re.compile(cfg.include_regex) if cfg.include_regex else None
        exclude = re.compile(cfg.exclude_regex) if cfg.exclude_regex else None
        queue: list[tuple[str, int]] = [(urldefrag(u)[0], 0) for u in cfg.start_urls]
        seen: set[str] = set(u for u, _ in queue)
        fetched = 0
        while queue and fetched < cfg.max_pages:
            url, depth = queue.pop(0)
            try:
                page = await egress.request(
                    "GET",
                    url,
                    self.ctx.allowed_hosts,
                    allow_private=self.ctx.allow_private,
                    headers=self.ctx.headers(),
                    timeout_s=30,
                    max_bytes=PAGE_BYTES,
                    accept="text/html, text/plain;q=0.8",
                )
            except EgressError as exc:
                if fetched == 0 and url in cfg.start_urls:
                    raise SourceError(f"Cannot read {url}: {exc.message} ({exc.code})") from exc
                continue  # a dead link or a non-text page: skip it
            if page.status >= 400 or not page.body.strip():
                continue
            fetched += 1
            if depth < cfg.max_depth and "html" in page.content_type:
                parser = _Links()
                parser.feed(page.body)
                for href in parser.links:
                    nxt = urldefrag(urljoin(page.url, href))[0]
                    parts = urlsplit(nxt)
                    if (
                        parts.scheme in ("http", "https")
                        and (parts.hostname or "").lower() in hosts
                        and nxt not in seen
                        and (include is None or include.search(nxt))
                        and (exclude is None or not exclude.search(nxt))
                    ):
                        seen.add(nxt)
                        queue.append((nxt, depth + 1))
            if exclude is not None and exclude.search(url):
                continue
            body = page.body.encode()
            parts = urlsplit(page.url)
            stem = _slug(parts.path.strip("/").replace("/", "-") or "index", 60)
            ext = "html" if "html" in page.content_type else "txt"

            async def fetch(fh: IO[bytes], body: bytes = body) -> None:
                fh.write(body)

            yield RemoteItem(
                page.url,
                f"{stem}-{_short(page.url)}.{ext}",
                parts.hostname or "",
                hashlib.sha256(body).hexdigest(),
                len(body),
                fetch,
            )
            await asyncio.sleep(0.1)


# ---- REST / JSON API
class RestConnector(Connector):
    async def items(self) -> AsyncIterator[RemoteItem]:
        cfg: RestConfig = self.cfg
        url: str | None = cfg.url
        seen_pages = 0
        while url and seen_pages < cfg.max_pages:
            try:
                page = await egress.request(
                    cfg.method,
                    url,
                    self.ctx.allowed_hosts,
                    allow_private=self.ctx.allow_private,
                    headers=self.ctx.headers(),
                    json_body=cfg.body if cfg.method == "POST" else None,
                    timeout_s=60,
                    max_bytes=8 * 1024 * 1024,
                    accept="application/json",
                )
            except EgressError as exc:
                raise SourceError(f"Cannot read {url}: {exc.message} ({exc.code})") from exc
            if page.status >= 400:
                raise SourceError(f"{url} answered HTTP {page.status}")
            if page.truncated:
                raise SourceError("The response is larger than 8 MB; use a smaller page size")
            try:
                data = json.loads(page.body)
            except ValueError as exc:
                raise SourceError("The response is not JSON") from exc
            rows = _dotted(data, cfg.items_path) if cfg.items_path else data
            if not isinstance(rows, list):
                raise SourceError(f"'{cfg.items_path or '(root)'}' is not a list in the response")
            for row in rows:
                if not isinstance(row, dict) or row.get(cfg.id_field) is None:
                    continue
                yield _record_item(
                    str(row[cfg.id_field]),
                    row,
                    cfg.title_field,
                    cfg.content_fields,
                    cfg.version_field,
                )
            seen_pages += 1
            nxt = _dotted(data, cfg.next_path) if cfg.next_path else None
            url = urljoin(url, nxt) if isinstance(nxt, str) and nxt else None


def _record_item(
    ident: str,
    row: dict[str, Any],
    title_field: str | None,
    content_fields: list[str],
    version_field: str | None,
) -> RemoteItem:
    """One record becomes one document: Markdown from the chosen fields, or the JSON itself."""
    title = str(row.get(title_field, ident)) if title_field else ident
    if content_fields:
        parts = [f"# {title}"]
        for f in content_fields:
            if row.get(f) not in (None, ""):
                value = row[f]
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                parts.append(f"## {f}\n{text}")
        body, ext = "\n\n".join(parts), "md"
    else:
        body, ext = json.dumps(row, ensure_ascii=False, indent=2, default=str), "txt"
    data = body.encode()
    version = (
        str(row.get(version_field)) + ":" + hashlib.sha256(data).hexdigest()[:8]
        if version_field and row.get(version_field) is not None
        else hashlib.sha256(data).hexdigest()
    )

    async def fetch(fh: IO[bytes]) -> None:
        fh.write(data)

    return RemoteItem(
        ident, f"{_slug(title, 60)}-{_short(ident)}.{ext}", "", version, len(data), fetch
    )


# ---- SQL
class SqlConnector(Connector):
    async def items(self) -> AsyncIterator[RemoteItem]:
        cfg: SqlSourceConfig = self.cfg
        if self.ctx.query_rows is None:
            raise SourceError("SQL sources are not available in this process")
        columns, rows, truncated = await self.ctx.query_rows(
            cfg.datasource, cfg.query, cfg.row_limit
        )
        for needed in [cfg.id_column, *cfg.content_columns, cfg.title_column, cfg.version_column]:
            if needed and needed not in columns:
                raise SourceError(f"The query has no column '{needed}'")
        if truncated:
            raise SourceError(
                f"The query returns more than {cfg.row_limit} rows; narrow it or use a view"
            )
        for values in rows:
            row = dict(zip(columns, values, strict=True))
            if row.get(cfg.id_column) is None:
                continue
            if not cfg.content_columns:
                row = {k: v for k, v in row.items()}
            yield _record_item(
                str(row[cfg.id_column]),
                row,
                cfg.title_column,
                cfg.content_columns,
                cfg.version_column,
            )


# ---- Git over HTTPS
class GitConnector(Connector):
    def __init__(self, config: Any, ctx: SourceContext) -> None:
        super().__init__(config, ctx)
        self._dir: str | None = None

    def _auth(self) -> list[str]:
        import base64

        s = self.ctx.secret
        if token := s.get("token"):
            raw = f"x-access-token:{token}"
        elif s.get("username") and s.get("password"):
            raw = f"{s['username']}:{s['password']}"
        else:
            return []
        return [
            "-c",
            "http.extraheader=Authorization: Basic " + base64.b64encode(raw.encode()).decode(),
        ]

    async def _check_repo(self, url: str) -> None:
        if urlsplit(url).scheme != "https":
            raise SourceError("Only https repositories are supported")
        await self.ctx.check(url)

    async def items(self) -> AsyncIterator[RemoteItem]:
        cfg: GitConfig = self.cfg
        await self._check_repo(cfg.repo_url)
        if shutil.which("git") is None:
            raise SourceError("git is not installed in this worker image")
        self._dir = tempfile.mkdtemp(prefix="forge-git-")
        cmd = ["git", *self._auth(), "clone", "--depth", "1", "--single-branch", "--quiet"]
        if cfg.ref:
            cmd += ["--branch", cfg.ref]
        cmd += ["--", cfg.repo_url, self._dir + "/repo"]
        env = {
            k: v
            for k, v in os.environ.items()
            if k.startswith(("PATH", "HOME", "HTTP", "http", "NO_", "no_", "SSL", "GIT_SSL"))
        }
        env |= {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, env=env
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=600)
        except TimeoutError as exc:
            proc.kill()
            raise SourceError("git clone took more than 10 minutes") from exc
        if proc.returncode != 0:
            msg = err.decode(errors="replace").strip().splitlines()[-1:] or ["clone failed"]
            token = self.ctx.secret.get("token") or self.ctx.secret.get("password") or ""
            raise SourceError(
                f"git clone failed: {msg[0].replace(token, '***') if token else msg[0]}"[:300]
            )
        found = await asyncio.to_thread(self._scan, Path(self._dir, "repo"), cfg)
        for item in found:
            yield item

    @staticmethod
    def _scan(root: Path, cfg: GitConfig) -> list[RemoteItem]:
        base = (root / cfg.subpath.strip("/")).resolve()
        if not base.is_dir() or root.resolve() not in (base, *base.parents):
            raise SourceError("The sub-path does not exist in the repository")
        return _walk(
            base,
            root,
            cfg.include,
            cfg.max_items,
            recursive=True,
            skip=(".git",),
            hash_content=True,
        )

    async def close(self) -> None:
        if self._dir:
            await asyncio.to_thread(shutil.rmtree, self._dir, True)
            self._dir = None


def _walk(
    base: Path,
    anchor: Path,
    include: list[str],
    max_items: int,
    *,
    recursive: bool,
    skip: tuple[str, ...] = (),
    hash_content: bool = False,
) -> list[RemoteItem]:
    out: list[RemoteItem] = []
    real_anchor = anchor.resolve()
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(
            d for d in dirnames if d not in skip and not Path(dirpath, d).is_symlink()
        )
        if not recursive:
            dirnames[:] = []
        for fn in sorted(filenames):
            path = Path(dirpath, fn)
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(anchor).as_posix()
            if not _matches(rel, include):
                continue
            stat = path.stat()
            if stat.st_size > MAX_ITEM_BYTES or stat.st_size == 0:
                continue
            if real_anchor not in path.resolve().parents:
                continue
            if len(out) >= max_items:
                raise SourceError(f"More than {max_items} matching files")

            async def fetch(fh: IO[bytes], path: Path = path) -> None:
                def copy() -> None:
                    with path.open("rb") as src:
                        shutil.copyfileobj(src, fh, 1024 * 1024)

                await asyncio.to_thread(copy)

            version = f"{stat.st_mtime_ns}:{stat.st_size}"
            if hash_content:  # a fresh checkout has new modification times every time
                with path.open("rb") as fh:
                    version = hashlib.file_digest(fh, "sha256").hexdigest()
            folder, _, name = rel.rpartition("/")
            out.append(RemoteItem(rel, name, folder, version, stat.st_size, fetch))
    return out


# ---- local or mounted directory (SMB, NFS and the like are mounted by the operator)
class FilesystemConnector(Connector):
    async def items(self) -> AsyncIterator[RemoteItem]:
        cfg: FilesystemConfig = self.cfg
        if not self.ctx.roots:
            raise SourceError("No directory is enabled for filesystem sources (FORGE_SOURCE_ROOTS)")
        found = await asyncio.to_thread(self._scan, cfg)
        for item in found:
            yield item

    def _scan(self, cfg: FilesystemConfig) -> list[RemoteItem]:
        resolved = Path(cfg.path).resolve()
        allowed = any(r.resolve() in (resolved, *resolved.parents) for r in self.ctx.roots)
        if not allowed:
            raise SourceError("The path is outside the directories enabled by the administrator")
        if not resolved.is_dir():
            raise SourceError("The path is not a directory (is the share mounted?)")
        return _walk(resolved, resolved, cfg.include, cfg.max_items, recursive=cfg.recursive)


CONNECTORS: dict[str, type[Connector]] = {
    "s3": S3Connector,
    "url": UrlConnector,
    "rest": RestConnector,
    "sql": SqlConnector,
    "git": GitConnector,
    "filesystem": FilesystemConnector,
}


def build(kind: str, config: dict[str, Any], ctx: SourceContext) -> Connector:
    return CONNECTORS[kind](CONFIGS[kind].model_validate(config), ctx)
