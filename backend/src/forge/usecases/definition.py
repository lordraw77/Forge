"""Use case definitions: the input form, its JSON Schema, references to library assets and the
structural checks run on save. The graph semantic checks (variables, cycles, reachability) and
the engine arrive with M2.3b and extend `validate_definition`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

NODE_TYPES = frozenset(
    {
        "start",
        "agent",
        "tool",
        "llm",
        "code",
        "router",
        "for_each",
        "while",
        "human_approval",
        "end",
    }
)
NODE_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
REF = re.compile(r"^([^@\s][^@\n]{0,79})@([1-9][0-9]{0,5})$")  # names may hold "(imported)"
MAX_AGENTS = 20
MAX_NODES = 200
RefKind = Literal["agent", "tool", "prompt"]
REF_KEYS: dict[str, RefKind] = {"agent_ref": "agent", "tool_ref": "tool", "prompt_ref": "prompt"}


@dataclass(frozen=True)
class Ref:
    kind: RefKind
    name: str
    version: int
    node: str | None = None


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    node: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "node": self.node}


def _entries(pattern: str, definition: dict[str, Any]) -> list[tuple[str | None, dict[str, Any]]]:
    """The dicts of a definition that can hold references, with the node they belong to."""
    key = "agents" if pattern == "supervisor" else "nodes"
    raw = definition.get(key)
    if not isinstance(raw, list):
        return []
    out: list[tuple[str | None, dict[str, Any]]] = []
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            ident = item.get("id")
            out.append((str(ident) if ident else f"{key}[{i}]", item))
    return out


def extract_refs(pattern: str, definition: dict[str, Any]) -> tuple[list[Ref], list[Issue]]:
    refs: list[Ref] = []
    issues: list[Issue] = []
    for node, item in _entries(pattern, definition):
        for key, kind in REF_KEYS.items():
            raw = item.get(key)
            if raw is None:
                continue
            m = REF.match(str(raw))
            if not m:
                issues.append(Issue("bad_reference", f"{key} must look like name@version", node))
                continue
            refs.append(Ref(kind, m.group(1), int(m.group(2)), node))
    return refs, issues


def validate_definition(pattern: str, definition: dict[str, Any]) -> list[Issue]:
    """Structural checks. An empty definition is a valid draft (nothing built yet) but not
    publishable: `require_complete` is applied by the caller."""
    issues: list[Issue] = []
    if not isinstance(definition, dict):
        return [Issue("bad_definition", "The definition must be an object")]
    if definition.get("pattern", pattern) != pattern:
        issues.append(Issue("pattern_mismatch", f"The definition is not a {pattern} workflow"))
    if pattern == "supervisor":
        agents = definition.get("agents", [])
        if not isinstance(agents, list) or len(agents) > MAX_AGENTS:
            issues.append(Issue("bad_agents", f"agents must be a list of at most {MAX_AGENTS}"))
        else:
            for i, a in enumerate(agents):
                if not isinstance(a, dict) or not a.get("agent_ref"):
                    issues.append(
                        Issue("bad_agent", "Each team member needs an agent_ref", f"agents[{i}]")
                    )
    else:
        nodes = definition.get("nodes", [])
        if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
            return [*issues, Issue("bad_nodes", f"nodes must be a list of at most {MAX_NODES}")]
        seen: set[str] = set()
        for i, n in enumerate(nodes):
            if not isinstance(n, dict):
                issues.append(Issue("bad_node", "A node must be an object", f"nodes[{i}]"))
                continue
            ident = str(n.get("id", ""))
            if not NODE_ID.match(ident):
                issues.append(
                    Issue(
                        "bad_node_id", "Node ids look like [a-z][a-z0-9_]*", ident or f"nodes[{i}]"
                    )
                )
            elif ident in seen:
                issues.append(Issue("duplicate_node", "Node ids must be unique", ident))
            seen.add(ident)
            if n.get("type") not in NODE_TYPES:
                issues.append(Issue("bad_node_type", f"Unknown node type {n.get('type')!r}", ident))
        if sum(1 for n in nodes if isinstance(n, dict) and n.get("type") == "start") > 1:
            issues.append(Issue("multiple_start", "A graph has one start node"))
    _, ref_issues = extract_refs(pattern, definition)
    return issues + ref_issues


def completeness(pattern: str, definition: dict[str, Any]) -> list[Issue]:
    """What a definition needs before it can be tested or published."""
    if pattern == "supervisor":
        if not definition.get("agents"):
            return [Issue("empty_workflow", "Add at least one agent to the team")]
        return []
    nodes = [n for n in definition.get("nodes", []) if isinstance(n, dict)]
    issues: list[Issue] = []
    if not any(n.get("type") == "start" for n in nodes):
        issues.append(Issue("no_start", "The graph needs a start node"))
    if not any(n.get("type") in {"agent", "tool", "llm", "code"} for n in nodes):
        issues.append(Issue("empty_workflow", "Add at least one agent, tool or llm node"))
    return issues


FIELD_JSON: dict[str, dict[str, Any]] = {
    "text": {"type": "string", "maxLength": 1000},
    "long_text": {"type": "string", "maxLength": 20000},
    "number": {"type": "number"},
    "select": {"type": "string"},
    "file": {"type": "string", "format": "forge-file"},
    "collection": {"type": "string", "format": "forge-collection"},
}


def input_schema(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """JSON Schema of the run input, generated from the input form (US-2.5.1 #2)."""
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in fields:
        prop = dict(FIELD_JSON[f["type"]])
        prop["title"] = f.get("label") or f["name"]
        if f.get("help"):
            prop["description"] = f["help"]
        if f["type"] == "select":
            prop["enum"] = list(f.get("options") or [])
        if f["type"] == "number":
            if f.get("min") is not None:
                prop["minimum"] = f["min"]
            if f.get("max") is not None:
                prop["maximum"] = f["max"]
        if f.get("default") is not None:
            prop["default"] = f["default"]
        props[f["name"]] = prop
        if f.get("required"):
            required.append(f["name"])
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }
