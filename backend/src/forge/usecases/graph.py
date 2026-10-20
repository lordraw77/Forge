"""Use case graph v1 (ADR-010): semantic validation, templates, router/while conditions and the
compilation of a supervisor team into a graph. Pure functions with no I/O, so the Temporal
workflow can import them (rule: workflow code stays deterministic)."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from typing import Any

from forge.usecases.definition import Issue

SCHEMA_VERSION = 1
LOOP_CAP = 200
DEFAULT_ITERATIONS = 50
DEFAULT_LIMITS = {"max_steps": 200, "max_cost_usd": 5.0, "timeout_s": 1800}
EXECUTABLE = frozenset({"agent", "tool", "llm"})
LOOPS = frozenset({"for_each", "while"})
OPS = frozenset({"=", "!=", ">", "<", "contains"})
VAR = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)((?:\.[a-zA-Z0-9_]+|\[\d+\])*)\}")
PATH_PART = re.compile(r"\.([a-zA-Z0-9_]+)|\[(\d+)\]")
IDENT = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
MAX_VALUE_BYTES = 64 * 1024


class GraphError(Exception):
    def __init__(self, code: str, message: str, node: str | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.node = code, message, node


# ------------------------------------------------------------------ references and templates
def split_ref(ref: str) -> tuple[str, str | None]:
    node, _, port = ref.partition(".")
    return node, port or None


def templates_of(value: Any) -> list[str]:
    """Every string inside a nested value (dict/list/str)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in templates_of(v)]
    if isinstance(value, list):
        return [t for v in value for t in templates_of(v)]
    return []


def variables_in(template: str) -> list[str]:
    """Root names of the `{variable}` references in a template."""
    return [m.group(1) for m in VAR.finditer(template)]


def lookup(variables: dict[str, Any], root: str, path: str) -> Any:
    if root not in variables:
        raise GraphError("undefined_variable", f"The variable '{root}' has no value")
    value = variables[root]
    for m in PATH_PART.finditer(path):
        try:
            if m.group(1) is not None:
                if isinstance(value, str):
                    value = json.loads(value)
                value = value[m.group(1)]
            else:
                value = value[int(m.group(2))]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GraphError(
                "undefined_variable", f"'{root}{path}' does not exist in the value"
            ) from exc
    return value


def text_of(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def render(template: Any, variables: dict[str, Any]) -> Any:
    """A string that is exactly one `{variable}` keeps the value's type; any other string has
    its references replaced by text. Dicts and lists are rendered inside."""
    if isinstance(template, dict):
        return {k: render(v, variables) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, variables) for v in template]
    if not isinstance(template, str):
        return template
    whole = VAR.fullmatch(template.strip())
    if whole:
        return lookup(variables, whole.group(1), whole.group(2))
    return VAR.sub(lambda m: text_of(lookup(variables, m.group(1), m.group(2))), template)


def bounded(value: Any) -> Any:
    """Values kept in the run's variable map stay small (workflow history, step rows)."""
    raw = text_of(value)
    if len(raw.encode()) <= MAX_VALUE_BYTES:
        return value
    return raw.encode()[:MAX_VALUE_BYTES].decode(errors="ignore")


# ------------------------------------------------------------------ conditions
def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def compare(left: Any, op: str, right: Any) -> bool:
    if op == "contains":
        if isinstance(left, (list, tuple)):
            return any(text_of(i) == text_of(right) for i in left)
        return text_of(right).lower() in text_of(left).lower()
    a, b = _number(left), _number(right)
    if a is not None and b is not None:
        pair: tuple[Any, Any] = (a, b)
    else:
        pair = (text_of(left), text_of(right))
    if op == "=":
        return bool(pair[0] == pair[1])
    if op == "!=":
        return bool(pair[0] != pair[1])
    if a is None or b is None:
        raise GraphError("bad_comparison", f"'{op}' needs numbers, got {left!r} and {right!r}")
    return bool(a > b) if op == ">" else bool(a < b)


def evaluate(section: dict[str, Any], variables: dict[str, Any]) -> bool:
    """Conditions of a router section or a while node combined with `and` / `or`."""
    results: list[bool] = []
    for c in section.get("conditions") or []:
        field = str(c.get("field", "")).strip().strip("{}")
        m = VAR.fullmatch("{" + field + "}")
        if not m:
            raise GraphError("bad_condition", f"'{field}' is not a variable")
        left = lookup(variables, m.group(1), m.group(2))
        results.append(compare(left, str(c.get("op")), render(c.get("value"), variables)))
    if not results:
        return False
    return any(results) if section.get("combine") == "or" else all(results)


# ------------------------------------------------------------------ structure helpers
def nodes_of(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in graph.get("nodes", []) if isinstance(n, dict)]


def ports_of(node: dict[str, Any]) -> list[str]:
    t = node.get("type")
    if t == "router":
        return [str(s.get("name")) for s in node.get("sections") or []] + ["default"]
    if t in LOOPS:
        return ["loop", "done"]
    return []


def output_var(node: dict[str, Any]) -> str | None:
    t = node.get("type")
    if t in EXECUTABLE or t == "start":
        return str(node.get("output") or node.get("id"))
    return None


def descendants(
    children: dict[str, list[tuple[str, str | None]]], start: str, port: str | None
) -> set[str]:
    """Nodes reachable from `start` (through `port` only for the first hop)."""
    seen: set[str] = set()
    queue = deque(c for c, p in children.get(start, []) if port is None or p == port)
    while queue:
        n = queue.popleft()
        if n in seen:
            continue
        seen.add(n)
        queue.extend(c for c, _ in children.get(n, []))
    return seen


def children_map(graph: dict[str, Any]) -> dict[str, list[tuple[str, str | None]]]:
    out: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for n in nodes_of(graph):
        for ref in n.get("after") or []:
            parent, port = split_ref(str(ref))
            out[parent].append((str(n["id"]), port))
    return out


def loop_body(graph: dict[str, Any], loop_id: str) -> set[str]:
    """Nodes that run once per iteration: below `loop` and not below `done`."""
    kids = children_map(graph)
    below_done = descendants(kids, loop_id, "done")
    return descendants(kids, loop_id, "loop") - below_done - {loop_id}


# ------------------------------------------------------------------ supervisor
def compile_supervisor(definition: dict[str, Any], input_names: list[str]) -> dict[str, Any]:
    """A team in plan order is a chain of agent nodes: the Team Leader gets the form values, each
    next agent gets the previous output as context (US-2.7.1)."""
    nodes: list[dict[str, Any]] = [{"id": "start", "type": "start", "output": "input"}]
    form = "\n".join(f"{n}: {{{n}}}" for n in input_names)
    previous = "start"
    for i, member in enumerate(definition.get("agents") or [], 1):
        node_id = f"agent_{i}"
        if i == 1:
            body = form
        else:
            body = f"Previous agent output:\n{{agent_{i - 1}}}"
            if form:
                body = f"{form}\n\n{body}"
        nodes.append(
            {
                "id": node_id,
                "type": "agent",
                "agent_ref": member.get("agent_ref"),
                "task": member.get("task") or "",
                "input": body,
                "output": node_id,
                "after": [previous],
            }
        )
        previous = node_id
    count = len(definition.get("agents") or [])
    if count:
        nodes.append(
            {"id": "end", "type": "end", "output": f"{{agent_{count}}}", "after": [previous]}
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "pattern": "graph",
        "nodes": nodes,
        "limits": definition.get("limits") or {},
    }


def executable_graph(
    pattern: str, definition: dict[str, Any], input_names: list[str]
) -> dict[str, Any]:
    return compile_supervisor(definition, input_names) if pattern == "supervisor" else definition


def limits_of(graph: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULT_LIMITS)
    for k, v in (graph.get("limits") or {}).items():
        if k in out and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            out[k] = min(v, DEFAULT_LIMITS[k] * 10)
    return out


# ------------------------------------------------------------------ semantic validation
def analyze(graph: dict[str, Any], input_names: list[str]) -> list[Issue]:
    """Checks beyond structure (ADR-010): dependencies, ports, cycles, loops, variables."""
    nodes = nodes_of(graph)
    issues: list[Issue] = []
    by_id = {str(n.get("id")): n for n in nodes}

    def bad(code: str, message: str, node: str | None = None) -> None:
        issues.append(Issue(code, message, node))

    starts = [n for n in nodes if n.get("type") == "start"]
    if not starts:
        bad("no_start", "The graph needs a start node")
    after_mode_ok = {"all", "any"}
    for n in nodes:
        nid = str(n.get("id"))
        if n.get("after_mode", "all") not in after_mode_ok:
            bad("bad_after_mode", "after_mode is 'all' or 'any'", nid)
        refs = [str(r) for r in n.get("after") or []]
        if n.get("type") == "start" and refs:
            bad("start_has_parents", "The start node has no dependencies", nid)
        if n.get("type") != "start" and not refs:
            bad("no_dependencies", "This node runs after nothing: set 'after'", nid)
        for ref in refs:
            parent, port = split_ref(ref)
            p = by_id.get(parent)
            if p is None:
                bad("unknown_node", f"'after' refers to unknown node '{parent}'", nid)
                continue
            if parent == nid:
                bad("self_dependency", "A node cannot run after itself", nid)
            valid = ports_of(p)
            if port is None and valid:
                bad("port_required", f"'{parent}' has outputs ({', '.join(valid)}): name one", nid)
            elif port is not None and port not in valid:
                bad("unknown_port", f"'{parent}' has no output '{port}'", nid)
    for n in nodes:
        nid = str(n.get("id"))
        t = n.get("type")
        if t == "agent" and not n.get("agent_ref"):
            bad("missing_ref", "An agent node needs agent_ref", nid)
        if t == "tool" and not n.get("tool_ref"):
            bad("missing_ref", "A tool node needs tool_ref", nid)
        if t == "llm" and not n.get("prompt_ref"):
            bad("missing_ref", "An llm node needs prompt_ref", nid)
        if t == "router":
            names = [str(s.get("name")) for s in n.get("sections") or []]
            if not names:
                bad("empty_router", "A router needs at least one section", nid)
            if len(set(names)) != len(names) or "default" in names:
                bad("bad_sections", "Section names must be unique and not 'default'", nid)
            for s in n.get("sections") or []:
                if not IDENT.match(str(s.get("name"))):
                    bad("bad_sections", "Section names look like [a-z][a-z0-9_]*", nid)
                _check_conditions(s, nid, bad)
        if t in LOOPS:
            it = n.get("max_iterations", DEFAULT_ITERATIONS)
            if not isinstance(it, int) or isinstance(it, bool) or not 1 <= it <= LOOP_CAP:
                bad("unbounded_loop", f"max_iterations must be between 1 and {LOOP_CAP}", nid)
            if t == "while":
                _check_conditions(n, nid, bad)
            elif "items" not in n:
                bad("missing_items", "A for_each node needs items", nid)
    if any(i.code in {"unknown_node", "unknown_port", "self_dependency"} for i in issues):
        return issues
    order = _topological(nodes, by_id)
    if order is None:
        bad("cycle", "The graph has a cycle that does not go through a loop node")
        return issues
    issues += _loop_scopes(graph, by_id)
    issues += _variables(graph, order, by_id, input_names)
    return issues


def _check_conditions(section: dict[str, Any], node: str, bad: Any) -> None:
    conds = section.get("conditions")
    if not isinstance(conds, list) or not conds:
        bad("no_conditions", "At least one condition is needed", node)
        return
    for c in conds:
        if not isinstance(c, dict) or c.get("op") not in OPS or not c.get("field"):
            bad(
                "bad_condition",
                f"Each condition needs a field and an operator in {sorted(OPS)}",
                node,
            )
    if section.get("combine", "and") not in {"and", "or"}:
        bad("bad_condition", "combine is 'and' or 'or'", node)


def _topological(nodes: list[dict[str, Any]], by_id: dict[str, Any]) -> list[str] | None:
    indeg: dict[str, int] = {}
    kids: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        nid = str(n["id"])
        parents = {split_ref(str(r))[0] for r in n.get("after") or []}
        indeg[nid] = len(parents)
        for p in parents:
            kids[p].append(nid)
    queue = deque(sorted(k for k, d in indeg.items() if d == 0))
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for c in kids[current]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    return order if len(order) == len(by_id) else None


def _loop_scopes(graph: dict[str, Any], by_id: dict[str, Any]) -> list[Issue]:
    """A loop body may depend only on itself, on the loop node and on its ancestors: anything
    else may still be unresolved when the iterations run."""
    issues: list[Issue] = []
    parents: dict[str, set[str]] = {
        str(n["id"]): {split_ref(str(r))[0] for r in n.get("after") or []} for n in nodes_of(graph)
    }

    def ancestors(node: str) -> set[str]:
        seen: set[str] = set()
        stack = list(parents.get(node, ()))
        while stack:
            p = stack.pop()
            if p not in seen:
                seen.add(p)
                stack.extend(parents.get(p, ()))
        return seen

    for n in nodes_of(graph):
        if n.get("type") not in LOOPS:
            continue
        loop = str(n["id"])
        body = loop_body(graph, loop)
        allowed = body | {loop} | ancestors(loop)
        for b in sorted(body):
            for p in parents[b]:
                if p not in allowed:
                    issues.append(
                        Issue(
                            "loop_external_dependency",
                            f"'{b}' is inside the loop '{loop}' but runs after '{p}', which is not",
                            b,
                        )
                    )
    return issues


def _variables(
    graph: dict[str, Any], order: list[str], by_id: dict[str, Any], input_names: list[str]
) -> list[Issue]:
    """Every `{variable}` must be defined on all paths before use."""
    issues: list[Issue] = []
    base = set(input_names)
    body_outputs: dict[str, set[str]] = {}
    for n in nodes_of(graph):
        if n.get("type") in LOOPS:
            body_outputs[str(n["id"])] = {
                v for b in loop_body(graph, str(n["id"])) if (v := output_var(by_id[b])) is not None
            }
    defined_in: dict[str, set[str]] = {}

    def out_of(ref: str) -> set[str]:
        parent, port = split_ref(ref)
        p = by_id[parent]
        have = defined_in[parent]
        t = p.get("type")
        if t == "for_each":
            return have | ({"item", "index"} if port == "loop" else body_outputs[parent])
        if t == "while":
            return have | ({"index"} if port == "loop" else set())
        mine = output_var(p)
        return have | ({mine} if mine else set())

    for nid in order:
        n = by_id[nid]
        refs = [str(r) for r in n.get("after") or []]
        have = set(base) if not refs else set.intersection(*(out_of(r) for r in refs))
        defined_in[nid] = have
        for template in _templates(n):
            for name in variables_in(template):
                if name not in have:
                    issues.append(
                        Issue(
                            "undefined_variable",
                            f"The variable '{name}' is not defined on every path to this node",
                            nid,
                        )
                    )
        for c in _condition_fields(n):
            root = c.split(".")[0].split("[")[0]
            if root not in have:
                issues.append(
                    Issue("undefined_variable", f"The variable '{root}' is not defined here", nid)
                )
    return issues


def _templates(node: dict[str, Any]) -> list[str]:
    t = node.get("type")
    if t == "agent":
        return templates_of([node.get("task"), node.get("input")])
    if t == "tool":
        return templates_of(node.get("args"))
    if t == "llm":
        return templates_of(node.get("variables"))
    if t == "for_each":
        return templates_of(node.get("items")) if isinstance(node.get("items"), str) else []
    if t == "end":
        return templates_of(node.get("output"))
    if t == "router":
        return [
            v
            for s in node.get("sections") or []
            for c in s.get("conditions") or []
            for v in templates_of(c.get("value"))
        ]
    if t == "while":
        return [v for c in node.get("conditions") or [] for v in templates_of(c.get("value"))]
    return []


def _condition_fields(node: dict[str, Any]) -> list[str]:
    if node.get("type") not in {"router", "while"}:
        return []
    sections = (node.get("sections") or []) if node.get("type") == "router" else [node]
    return [
        str(c.get("field", "")).strip().strip("{}")
        for s in sections
        for c in s.get("conditions") or []
    ]
