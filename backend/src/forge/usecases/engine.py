"""The Forge graph engine (ADR-010, ADR-004): schedules the nodes of a use case graph.

Pure Python with no I/O: node work is delegated to an `execute` coroutine (a Temporal activity
in production, a fake in unit tests), so the same code runs inside the deterministic workflow.

Semantics:
- a node runs when its dependencies are resolved (`after_mode` all/any); nodes whose
  dependencies were all skipped are skipped (dead-path elimination);
- nodes that become ready together run in parallel;
- a loop runs its body (the nodes below its `loop` port and not below `done`) once per
  iteration, sequentially, and its `done` port opens afterwards. After a `for_each`, each output
  variable of the body holds the list of its per-iteration values;
- limits (`max_steps`, `max_cost_usd`) are checked before each step.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from forge.usecases import graph as g

Execute = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
# Waits for a person: gets the approval to raise, returns {"decision": "approve" | "reject",
# "edited": any | None, "comment": str}.
Approve = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
MAX_REJECTIONS = 3  # a `hitl` agent is re-run this many times after a rejection


@dataclass
class EngineResult:
    output: Any
    steps: int
    cost: float
    tokens_in: int
    tokens_out: int
    skipped: list[str] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)


class Engine:
    def __init__(
        self,
        graph: dict[str, Any],
        inputs: dict[str, Any],
        execute: Execute,
        limits: dict[str, Any] | None = None,
        approve: Approve | None = None,
    ) -> None:
        self._approve = approve
        self._graph = graph
        self._nodes = {str(n["id"]): n for n in g.nodes_of(graph)}
        self._order = [str(n["id"]) for n in g.nodes_of(graph)]
        self._execute = execute
        self._limits = {**g.DEFAULT_LIMITS, **(limits or g.limits_of(graph))}
        self._inputs = inputs
        self.vars: dict[str, Any] = dict(inputs)
        self._edge: dict[str, str] = {}
        self._bodies = {
            nid: g.loop_body(graph, nid) for nid, n in self._nodes.items() if n["type"] in g.LOOPS
        }
        self._steps = 0
        self._cost = 0.0
        self._tokens = [0, 0]
        self._final: Any = None
        self._has_final = False
        self._last: Any = None
        self._skipped: list[str] = []
        self._decisions: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ public
    async def run(self) -> EngineResult:
        inner = set().union(*self._bodies.values()) if self._bodies else set()
        await self._region(set(self._nodes) - inner, "")
        return EngineResult(
            output=self._final if self._has_final else self._last,
            steps=self._steps,
            cost=self._cost,
            tokens_in=self._tokens[0],
            tokens_out=self._tokens[1],
            skipped=self._skipped,
            decisions=self._decisions,
        )

    def snapshot(self) -> dict[str, Any]:
        """Totals so far, for a run that stopped early."""
        return {
            "steps": self._steps,
            "cost": self._cost,
            "tokens_in": self._tokens[0],
            "tokens_out": self._tokens[1],
            "skipped": list(self._skipped),
            "decisions": list(self._decisions),
        }

    # ------------------------------------------------------------------ scheduling
    def _members(self, region: set[str]) -> set[str]:
        """Nodes of a region that run directly in it (not inside a nested loop body)."""
        nested = set().union(*(self._bodies[n] for n in region if n in self._bodies), set())
        return region - nested

    def _decide(self, node: dict[str, Any]) -> str:
        refs = [str(r) for r in node.get("after") or []]
        if not refs:
            return "run"
        states = [self._edge.get(r) for r in refs]
        done = "completed" in states
        pending = None in states
        if node.get("after_mode", "all") == "any":
            return "run" if done else ("wait" if pending else "skip")
        if pending:
            return "wait"
        return "run" if done else "skip"

    async def _region(self, region: set[str], prefix: str) -> None:
        todo = [n for n in self._order if n in self._members(region)]
        finished: set[str] = set()
        while len(finished) < len(todo):
            ready: list[str] = []
            progressed = False
            for nid in todo:
                if nid in finished:
                    continue
                decision = self._decide(self._nodes[nid])
                if decision == "run":
                    ready.append(nid)
                elif decision == "skip":
                    self._skip(nid, prefix)
                    finished.add(nid)
                    progressed = True
            if ready:
                await asyncio.gather(*(self._run_node(self._nodes[n], prefix) for n in ready))
                finished.update(ready)
                progressed = True
            if not progressed:
                stuck = ", ".join(n for n in todo if n not in finished)
                raise g.GraphError("deadlock", f"Nodes never became ready: {stuck}")

    def _skip(self, nid: str, prefix: str) -> None:
        self._edge[nid] = "skipped"
        for port in g.ports_of(self._nodes[nid]):
            self._edge[f"{nid}.{port}"] = "skipped"
        self._skipped.append(f"{prefix}{nid}")

    def _reset(self, body: set[str]) -> None:
        for nid in body:
            self._edge.pop(nid, None)
            for port in g.ports_of(self._nodes[nid]):
                self._edge.pop(f"{nid}.{port}", None)

    def _check_limits(self, node: str) -> None:
        if self._steps >= int(self._limits["max_steps"]):
            raise g.GraphError(
                "max_steps", f"The run reached {self._limits['max_steps']} steps", node
            )
        if self._cost >= float(self._limits["max_cost_usd"]):
            raise g.GraphError(
                "max_cost",
                f"The run reached its cost limit of {self._limits['max_cost_usd']} USD",
                node,
            )

    # ------------------------------------------------------------------ nodes
    async def _run_node(self, node: dict[str, Any], prefix: str) -> None:
        nid, kind = str(node["id"]), node["type"]
        key = f"{prefix}{nid}"
        try:
            if kind == "start":
                self.vars[g.output_var(node) or "start"] = dict(self._inputs)
            elif kind == "end":
                if node.get("output") is not None:
                    self._final = g.bounded(g.render(node["output"], self.vars))
                    self._has_final = True
            elif kind == "human_approval":
                await self._review(node, key)
            elif kind in g.EXECUTABLE:
                await self._step(node, key)
            elif kind == "router":
                self._route(node, key)
            elif kind == "for_each":
                await self._for_each(node, prefix)
            elif kind == "while":
                await self._while(node, prefix)
            else:
                raise g.GraphError("bad_node_type", f"Unknown node type {kind!r}", nid)
        except g.GraphError as exc:
            if exc.node is None:
                exc.node = nid
            raise
        self._edge[nid] = "completed"

    async def _ask(self, call: dict[str, Any], node: str) -> dict[str, Any]:
        if self._approve is None:
            raise g.GraphError("approval_not_available", "Human approval is not available", node)
        return await self._approve(call)

    @staticmethod
    def _rejected(decision: dict[str, Any], node: str) -> g.GraphError:
        comment = str(decision.get("comment") or "").strip()
        return g.GraphError(
            "approval_rejected",
            f"Rejected by the reviewer: {comment}" if comment else "Rejected",
            node,
        )

    def _approval_call(
        self, node: dict[str, Any], key: str, kind: str, content: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "key": key,
            "node": str(node["id"]),
            "kind": kind,
            "content": content,
            "instructions": g.text_of(g.render(node.get("instructions") or "", self.vars)).strip(),
            "assignees": list(node.get("assignees") or []),
            "timeout_hours": node.get("timeout_hours"),
        }

    async def _review(self, node: dict[str, Any], key: str) -> None:
        """`human_approval`: a person confirms or edits `input`; that becomes the output."""
        nid = str(node["id"])
        text = g.text_of(g.render(node.get("input") or "", self.vars))
        call = self._approval_call(node, f"{key}~approval", "review", {"text": g.bounded(text)})
        decision = await self._ask(call, nid)
        if decision["decision"] != "approve":
            raise self._rejected(decision, nid)
        edited = decision.get("edited")
        value = g.bounded(text if edited is None else edited)
        self.vars[g.output_var(node) or nid] = value
        self._last = value

    async def _step(self, node: dict[str, Any], key: str) -> None:
        nid, kind = str(node["id"]), node["type"]
        feedback = ""
        previous: Any = None
        for attempt in range(MAX_REJECTIONS + 1):
            self._check_limits(nid)
            step_key = key if attempt == 0 else f"{key}~r{attempt}"
            if kind == "agent":
                parts = [
                    g.text_of(g.render(node.get("task") or "", self.vars)).strip(),
                    g.text_of(g.render(node.get("input") or "", self.vars)).strip(),
                ]
                if feedback:
                    parts.append(
                        "A reviewer rejected your previous answer.\n"
                        f"Previous answer:\n{g.text_of(previous)}\nFeedback: {feedback}\n"
                        "Write a new answer that addresses the feedback."
                    )
                payload: dict[str, Any] = {
                    "ref": node["agent_ref"],
                    "message": "\n\n".join(p for p in parts if p),
                }
            elif kind == "tool":
                payload = {
                    "ref": node["tool_ref"],
                    "args": g.render(node.get("args") or {}, self.vars),
                }
            elif kind == "code":
                payload = {
                    "ref": f"code:{nid}",
                    "code": node["code"],
                    "memory_mb": node.get("memory_mb", 128),
                    "args": g.render(node.get("args") or {}, self.vars),
                }
            else:
                payload = {
                    "ref": node["prompt_ref"],
                    "variables": {
                        k: g.text_of(v)
                        for k, v in g.render(node.get("variables") or {}, self.vars).items()
                    },
                }
            call = {
                "key": step_key,
                "node": nid,
                "kind": kind,
                "timeout_s": node.get("timeout_s"),
                **payload,
            }
            self._steps += 1
            result = await self._execute(call)
            if result.get("needs_approval"):  # a tool that requires approval, before the call
                ask = self._approval_call(
                    node,
                    f"{step_key}~approval",
                    "tool_call",
                    {"tool": node["tool_ref"], "args": result.get("planned_args", {})},
                )
                decision = await self._ask(ask, nid)
                if decision["decision"] != "approve":
                    raise self._rejected(decision, nid)
                edited = decision.get("edited")
                args = result.get("planned_args", {}) if edited is None else edited
                result = await self._execute({**call, "args": args, "approved": True})
            self._cost += float(result.get("cost") or 0)
            self._tokens[0] += int(result.get("tokens_in") or 0)
            self._tokens[1] += int(result.get("tokens_out") or 0)
            value = g.bounded(result.get("output"))
            if node.get("hitl"):
                ask = self._approval_call(
                    node,
                    f"{step_key}~approval",
                    "review",
                    {"text": g.text_of(value)},
                )
                decision = await self._ask(ask, nid)
                if decision["decision"] != "approve":
                    # Only an agent can act on feedback; other nodes would repeat themselves.
                    if kind != "agent" or attempt >= MAX_REJECTIONS:
                        raise self._rejected(decision, nid)
                    feedback, previous = str(decision.get("comment") or "").strip(), value
                    continue
                if decision.get("edited") is not None:
                    value = g.bounded(decision["edited"])
            self.vars[g.output_var(node) or nid] = value
            self._last = value
            return

    def _route(self, node: dict[str, Any], key: str) -> None:
        nid = str(node["id"])
        matched = [str(s["name"]) for s in node.get("sections") or [] if g.evaluate(s, self.vars)]
        for name in g.ports_of(node):
            hit = not matched if name == "default" else name in matched
            self._edge[f"{nid}.{name}"] = "completed" if hit else "skipped"
        self._decisions.append({"key": key, "node": nid, "paths": matched or ["default"]})

    def _items(self, node: dict[str, Any]) -> list[Any]:
        raw = g.render(node.get("items"), self.vars)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = [line.strip() for line in raw.splitlines() if line.strip()]
        if not isinstance(raw, list):
            raise g.GraphError("bad_items", "for_each needs a list", str(node["id"]))
        return raw

    def _max_iterations(self, node: dict[str, Any]) -> int:
        return int(node.get("max_iterations", g.DEFAULT_ITERATIONS))

    async def _for_each(self, node: dict[str, Any], prefix: str) -> None:
        nid = str(node["id"])
        items = self._items(node)
        if len(items) > self._max_iterations(node):
            raise g.GraphError(
                "max_iterations_exceeded",
                f"{len(items)} items but the loop allows {self._max_iterations(node)}",
                nid,
            )
        body = self._bodies[nid]
        outvars = [v for b in sorted(body) if (v := g.output_var(self._nodes[b]))]
        saved: dict[str, Any] = {k: self.vars[k] for k in ("item", "index") if k in self.vars}
        collected: dict[str, list[Any]] = {v: [] for v in outvars}
        for i, item in enumerate(items):
            self._reset(body)
            for v in outvars:
                self.vars.pop(v, None)
            self.vars["item"], self.vars["index"] = item, i
            self._edge[f"{nid}.loop"] = "completed"
            await self._region(body, f"{prefix}{nid}[{i}]/")
            for v in outvars:
                if v in self.vars:
                    collected[v].append(self.vars[v])
        self.vars.pop("item", None)
        self.vars.pop("index", None)
        self.vars.update(saved)
        for v, values in collected.items():
            self.vars[v] = g.bounded(values)
        self._edge[f"{nid}.loop"] = "completed"
        self._edge[f"{nid}.done"] = "completed"

    async def _while(self, node: dict[str, Any], prefix: str) -> None:
        nid = str(node["id"])
        body = self._bodies[nid]
        saved = self.vars.get("index")
        i = 0
        while g.evaluate(node, self.vars):
            if i >= self._max_iterations(node):
                raise g.GraphError(
                    "max_iterations_exceeded",
                    f"The loop is still going after {self._max_iterations(node)} iterations",
                    nid,
                )
            self._reset(body)
            self.vars["index"] = i
            self._edge[f"{nid}.loop"] = "completed"
            await self._region(body, f"{prefix}{nid}[{i}]/")
            i += 1
        if saved is None:
            self.vars.pop("index", None)
        else:
            self.vars["index"] = saved
        self._edge[f"{nid}.loop"] = "completed"
        self._edge[f"{nid}.done"] = "completed"
