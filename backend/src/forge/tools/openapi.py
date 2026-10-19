"""OpenAPI 3.x import (US-2.2.5): operations -> HTTP tool definitions. Local `$ref` only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from forge.tools.schemas import HttpConfig, ToolParam

METHODS = ("get", "post", "put", "patch", "delete")
MAX_REF_DEPTH = 12


class OpenApiError(ValueError):
    pass


@dataclass
class Operation:
    operation_id: str
    method: str
    path: str
    summary: str
    description: str
    parameters: list[dict[str, Any]]
    body_schema: dict[str, Any] | None
    name: str = ""


@dataclass
class Spec:
    title: str
    base_urls: list[str]
    security: list[str]
    operations: list[Operation] = field(default_factory=list)


def load(document: str) -> dict[str, Any]:
    try:
        data = (
            json.loads(document) if document.lstrip().startswith("{") else yaml.safe_load(document)
        )
    except (ValueError, yaml.YAMLError) as exc:
        raise OpenApiError(f"not valid JSON or YAML: {exc}") from exc
    if not isinstance(data, dict) or not str(data.get("openapi", "")).startswith("3."):
        raise OpenApiError("only OpenAPI 3.x documents are supported")
    return data


def _resolve(root: dict[str, Any], node: Any, depth: int = 0) -> Any:
    if depth > MAX_REF_DEPTH:
        raise OpenApiError("$ref nesting is too deep (cycle?)")
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#/"):
                raise OpenApiError(f"remote $ref refused: {ref}")
            target: Any = root
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or part not in target:
                    raise OpenApiError(f"unresolved $ref: {ref}")
                target = target[part]
            return _resolve(root, target, depth + 1)
        return {k: _resolve(root, v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(root, v, depth + 1) for v in node]
    return node


def _name(method: str, path: str, op_id: str | None) -> str:
    raw = op_id or f"{method}_{path}"
    snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", raw)
    snake = re.sub(r"[^A-Za-z0-9]+", "_", snake).strip("_").lower() or "operation"
    if not snake[0].isalpha():
        snake = "op_" + snake
    return snake[:48].rstrip("_")


def parse(document: str) -> Spec:
    root = load(document)
    info = root.get("info") or {}
    servers = [str(s.get("url", "")) for s in root.get("servers") or [] if isinstance(s, dict)]
    schemes = (root.get("components") or {}).get("securitySchemes") or {}
    security = []
    for key, scheme in schemes.items():
        scheme = _resolve(root, scheme)
        kind = scheme.get("type")
        if kind == "http" and scheme.get("scheme") in ("bearer", "basic"):
            security.append(f"{key}: {scheme['scheme']}")
        elif kind == "apiKey":
            security.append(f"{key}: {scheme.get('in')} {scheme.get('name')}")
        else:
            security.append(f"{key}: {kind} (not supported, add a credential by hand)")
    spec = Spec(str(info.get("title") or "API"), servers, security)
    used: set[str] = set()
    for path, item in (root.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters") or []
        for method in METHODS:
            raw = item.get(method)
            if not isinstance(raw, dict):
                continue
            op = _resolve(root, raw)
            params = [_resolve(root, p) for p in shared] + list(op.get("parameters") or [])
            body = None
            content = ((op.get("requestBody") or {}).get("content") or {}).get("application/json")
            if isinstance(content, dict):
                body = content.get("schema")
            base = _name(method, path, op.get("operationId"))
            name, n = base, 1
            while name in used:
                n += 1
                name = f"{base[:44]}_{n}"
            used.add(name)
            spec.operations.append(
                Operation(
                    operation_id=str(op.get("operationId") or f"{method.upper()} {path}"),
                    method=method.upper(),
                    path=path,
                    summary=str(op.get("summary") or ""),
                    description=str(op.get("description") or ""),
                    parameters=params,
                    body_schema=_resolve(root, body) if body else None,
                    name=name,
                )
            )
    if not spec.operations:
        raise OpenApiError("the document has no operations")
    return spec


def _type(schema: dict[str, Any]) -> str:
    t = schema.get("type")
    return t if t in ("string", "integer", "number", "boolean", "array", "object") else "string"


def to_http_config(op: Operation, base_url: str, credential: str | None) -> HttpConfig:
    params: list[ToolParam] = []
    seen: set[str] = set()
    for p in op.parameters:
        location = p.get("in")
        name = str(p.get("name", ""))
        if location not in ("path", "query", "header") or name.lower() in ("authorization",):
            continue
        safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
        if not safe or safe in seen or not (safe[0].isalpha() or safe[0] == "_"):
            continue
        seen.add(safe)
        schema = p.get("schema") or {}
        enum = schema.get("enum")
        params.append(
            ToolParam(
                name=safe,
                **{"in": location},
                type=_type(schema),
                description=str(p.get("description") or "")[:500],
                required=bool(p.get("required")) or location == "path",
                enum=[str(e) for e in enum][:100] if isinstance(enum, list) else None,
            )
        )
    if op.body_schema and op.body_schema.get("type") == "object":
        required = set(op.body_schema.get("required") or [])
        for key, schema in (op.body_schema.get("properties") or {}).items():
            safe = re.sub(r"[^A-Za-z0-9_]", "_", str(key))
            if not safe or safe in seen or not isinstance(schema, dict):
                continue
            seen.add(safe)
            params.append(
                ToolParam(
                    name=safe,
                    **{"in": "body"},
                    type=_type(schema),
                    description=str(schema.get("description") or "")[:500],
                    required=key in required,
                )
            )
    # Path placeholders must match the declared path parameters (names made safe).
    url = base_url.rstrip("/") + op.path
    for p in op.parameters:
        if p.get("in") == "path":
            url = url.replace(
                "{" + str(p["name"]) + "}",
                "{" + re.sub(r"[^A-Za-z0-9_]", "_", str(p["name"])) + "}",
            )
    return HttpConfig(
        method=op.method,
        url=url,
        params=params,
        credential=credential,
    )
