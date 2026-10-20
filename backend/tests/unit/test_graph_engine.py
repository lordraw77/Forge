"""Graph validation and the engine's scheduling with a fake executor (M2.3b)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from forge.usecases import graph as g
from forge.usecases.engine import Engine


def n(id: str, type: str, *after: str, **extra: Any) -> dict[str, Any]:
    node: dict[str, Any] = {"id": id, "type": type, **extra}
    if after:
        node["after"] = list(after)
    return node


def agent(id: str, *after: str, **extra: Any) -> dict[str, Any]:
    return n(id, "agent", *after, agent_ref="a@1", task="t", **extra)


def codes(nodes: list[dict[str, Any]], inputs: list[str] | None = None) -> list[str]:
    return [i.code for i in g.analyze({"nodes": nodes}, inputs or [])]


# ------------------------------------------------------------------ templates and conditions
def test_render_keeps_whole_values_and_stringifies_the_rest() -> None:
    v = {"items": [1, 2], "doc": {"title": "T", "tags": ["x"]}, "n": 3}
    assert g.render("{items}", v) == [1, 2]
    assert g.render("{doc.title}-{n}", v) == "T-3"
    assert g.render("{doc.tags[0]}", v) == "x"
    assert g.render({"a": ["{n}"]}, v) == {"a": [3]}
    assert g.render('{"json": 1}', v) == '{"json": 1}'
    with pytest.raises(g.GraphError) as e:
        g.render("{nope}", v)
    assert e.value.code == "undefined_variable"
    with pytest.raises(g.GraphError):
        g.render("{doc.missing}", v)


def test_conditions() -> None:
    v = {"score": "7", "text": "Hello World", "tags": ["a", "b"], "limit": 5}
    cond = lambda f, op, val, combine="and": {  # noqa: E731
        "conditions": [{"field": f, "op": op, "value": val}],
        "combine": combine,
    }
    assert g.evaluate(cond("score", ">", "{limit}"), v)
    assert not g.evaluate(cond("score", "<", 5), v)
    assert g.evaluate(cond("text", "contains", "world"), v)
    assert g.evaluate(cond("tags", "contains", "b"), v)
    assert g.evaluate(cond("text", "!=", "x"), v)
    assert g.evaluate(cond("score", "=", 7), v)
    both = {
        "conditions": [
            {"field": "score", "op": ">", "value": 100},
            {"field": "text", "op": "contains", "value": "hello"},
        ]
    }
    assert not g.evaluate(both, v)
    assert g.evaluate({**both, "combine": "or"}, v)
    assert not g.evaluate({"conditions": []}, v)
    with pytest.raises(g.GraphError):
        g.evaluate(cond("text", ">", 3), v)


def test_limits_are_clamped() -> None:
    assert g.limits_of({})["max_steps"] == 200
    assert g.limits_of({"limits": {"max_steps": 5, "timeout_s": -1}}) == {
        "max_steps": 5,
        "max_cost_usd": 5.0,
        "timeout_s": 1800,
    }
    assert g.limits_of({"limits": {"max_steps": 10**9}})["max_steps"] == 2000


# ------------------------------------------------------------------ validation
def test_valid_graph_with_branches_and_loop() -> None:
    nodes = [
        n("start", "start", output="doc"),
        agent("epics", "start", input="{topic}", output="epics_list"),
        n("each", "for_each", "epics", items="{epics_list}", max_iterations=10),
        agent("story", "each.loop", input="{item} #{index}", output="stories"),
        n(
            "route",
            "router",
            "each.done",
            sections=[
                {
                    "name": "many",
                    "conditions": [{"field": "stories", "op": "contains", "value": "x"}],
                }
            ],
        ),
        agent("deep", "route.many", input="{stories}"),
        agent("quick", "route.default", input="{stories}"),
        n("end", "end", "deep", "quick", after_mode="any", output="{stories}"),
    ]
    assert codes(nodes, ["topic"]) == []


@pytest.mark.parametrize(
    ("nodes", "expected"),
    [
        ([agent("a")], ["no_start", "no_dependencies"]),
        ([n("start", "start"), agent("a", "ghost")], ["unknown_node"]),
        ([n("start", "start"), agent("a", "start.x")], ["unknown_port"]),
        ([n("start", "start"), agent("a", "a")], ["self_dependency"]),
        ([n("start", "start"), agent("a", "start", "b"), agent("b", "a")], ["cycle"]),
        (
            [n("start", "start"), n("r", "router", "start", sections=[]), agent("a", "r")],
            ["empty_router", "port_required"],
        ),
        (
            [
                n("start", "start"),
                n(
                    "w",
                    "while",
                    "start",
                    max_iterations=0,
                    conditions=[{"field": "x", "op": "=", "value": 1}],
                ),
            ],
            ["unbounded_loop"],
        ),
        ([n("start", "start"), agent("a", "start", input="{nothing}")], ["undefined_variable"]),
        (
            [
                n("start", "start"),
                n("e", "for_each", "start", items="{x}"),
                agent("a", "e.loop"),
                agent("b", "e.done", input="{item}"),
            ],
            ["undefined_variable"],
        ),
    ],
)
def test_validation_errors(nodes: list[dict[str, Any]], expected: list[str]) -> None:
    got = codes(nodes, ["x"])
    assert sorted(got) == sorted(expected)


def test_variable_must_be_defined_on_every_path() -> None:
    nodes = [
        n("start", "start"),
        n(
            "r",
            "router",
            "start",
            sections=[{"name": "a", "conditions": [{"field": "q", "op": "=", "value": 1}]}],
        ),
        agent("left", "r.a", output="x"),
        agent("right", "r.default", output="y"),
        agent("join", "left", "right", input="{x}"),
    ]
    assert "undefined_variable" in codes(nodes, ["q"])
    nodes[-1]["after_mode"] = "any"
    assert "undefined_variable" in codes(nodes, ["q"])  # still not on every path
    nodes[-1]["input"] = "{q}"
    assert codes(nodes, ["q"]) == []


def test_loop_body_cannot_depend_on_unrelated_nodes() -> None:
    nodes = [
        n("start", "start"),
        agent("side", "start"),
        n("e", "for_each", "start", items=["a"]),
        agent("b", "e.loop", "side"),
    ]
    assert "loop_external_dependency" in codes(nodes)


def test_supervisor_compiles_to_a_valid_chain() -> None:
    team = {
        "agents": [{"agent_ref": "lead@1", "task": "Plan"}, {"agent_ref": "dev@2", "task": "Do"}]
    }
    graph = g.compile_supervisor(team, ["topic"])
    assert [x["id"] for x in graph["nodes"]] == ["start", "agent_1", "agent_2", "end"]
    assert graph["nodes"][1]["input"] == "topic: {topic}"
    assert "{agent_1}" in graph["nodes"][2]["input"]
    assert g.analyze(graph, ["topic"]) == []
    assert g.compile_supervisor({"agents": []}, [])["nodes"][0]["type"] == "start"


# ------------------------------------------------------------------ engine
class Fake:
    """Executor that records calls and answers `out:<node>` (or a scripted value)."""

    def __init__(self, script: dict[str, Any] | None = None, delay: float = 0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script = script or {}
        self.delay = delay
        self.running = 0
        self.peak = 0

    async def __call__(self, call: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(call)
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(self.delay)
        self.running -= 1
        out = self.script.get(call["key"], self.script.get(call["node"], f"out:{call['node']}"))
        if callable(out):
            out = out(call)
        return {"output": out, "cost": 0.5, "tokens_in": 3, "tokens_out": 4}


def run(
    graph_nodes: list[dict[str, Any]], inputs: dict[str, Any], fake: Fake, **limits: Any
) -> Any:
    graph = {"nodes": graph_nodes}
    engine = Engine(graph, inputs, fake, {**g.DEFAULT_LIMITS, **limits})
    return asyncio.run(engine.run())


def test_chain_passes_outputs_and_accounts() -> None:
    fake = Fake()
    res = run(
        [
            n("start", "start"),
            agent("a", "start", input="topic={topic}", output="draft"),
            agent("b", "a", input="review {draft}"),
            n("end", "end", "b", output="{draft}/{b}"),
        ],
        {"topic": "x"},
        fake,
    )
    assert [c["message"] for c in fake.calls] == ["t\n\ntopic=x", "t\n\nreview out:a"]
    assert res.output == "out:a/out:b"
    assert (res.steps, res.cost, res.tokens_in, res.tokens_out) == (2, 1.0, 6, 8)


def test_parallel_branches_and_join() -> None:
    fake = Fake(delay=0.05)
    res = run(
        [
            n("start", "start"),
            agent("a", "start"),
            agent("b", "start"),
            agent("join", "a", "b", input="{a}+{b}"),
        ],
        {},
        fake,
    )
    assert fake.peak == 2
    assert res.output == "out:join"
    assert fake.calls[-1]["message"].endswith("out:a+out:b")


def test_router_fan_out_default_and_dead_paths() -> None:
    sections = [
        {"name": "big", "conditions": [{"field": "n", "op": ">", "value": 10}]},
        {"name": "even", "conditions": [{"field": "n", "op": "contains", "value": "0"}]},
    ]
    nodes = [
        n("start", "start"),
        n("r", "router", "start", sections=sections),
        agent("big", "r.big"),
        agent("even", "r.even"),
        agent("other", "r.default"),
        agent("after_all", "big", "even", "other"),
        agent("after_both", "big", "even", after_mode="all"),
    ]
    fake = Fake()
    res = run(nodes, {"n": 100}, fake)  # both sections match
    assert sorted(c["node"] for c in fake.calls) == ["after_all", "after_both", "big", "even"]
    assert res.skipped == ["other"]
    fake = Fake()
    res = run(nodes, {"n": 5}, fake)  # nothing matches: default only
    assert sorted(c["node"] for c in fake.calls) == ["after_all", "other"]
    assert sorted(res.skipped) == ["after_both", "big", "even"]
    assert res.decisions[0]["paths"] == ["default"]
    fake = Fake()
    run(nodes, {"n": 50}, fake)  # only `even`... and big
    assert "after_both" in [c["node"] for c in fake.calls]


def test_any_mode_runs_on_first_completed_parent() -> None:
    nodes = [
        n("start", "start"),
        n(
            "r",
            "router",
            "start",
            sections=[{"name": "a", "conditions": [{"field": "q", "op": "=", "value": 1}]}],
        ),
        agent("left", "r.a"),
        agent("right", "r.default"),
        agent("join", "left", "right", after_mode="any"),
    ]
    fake = Fake()
    run(nodes, {"q": 1}, fake)
    assert [c["node"] for c in fake.calls] == ["left", "join"]


def test_for_each_iterates_collects_and_runs_done_nodes() -> None:
    nodes = [
        n("start", "start"),
        agent("epics", "start", output="epics_list"),
        n("each", "for_each", "epics", items="{epics_list}", max_iterations=5),
        agent("story", "each.loop", input="{item}@{index}", output="stories"),
        agent("review", "each.done", input="{stories}"),
    ]
    fake = Fake(
        {"epics": '["a", "b", "c"]', "story": lambda c: c["message"].split("\n\n")[-1].upper()}
    )
    res = run(nodes, {}, fake)
    assert [c["key"] for c in fake.calls] == [
        "epics",
        "each[0]/story",
        "each[1]/story",
        "each[2]/story",
        "review",
    ]
    assert fake.calls[-1]["message"].endswith('["A@0", "B@1", "C@2"]')
    assert res.steps == 5


def test_for_each_with_branches_in_the_body_and_nesting() -> None:
    nodes = [
        n("start", "start"),
        n("outer", "for_each", "start", items=["x", "y"], max_iterations=5),
        n("inner", "for_each", "outer.loop", items=[1, 2], max_iterations=5),
        agent("leaf", "inner.loop", input="{item}", output="leaf"),
        agent("tail", "inner.done", input="{leaf}", output="tail"),
        agent("final", "outer.done", input="{tail}"),
    ]
    fake = Fake()
    run(nodes, {}, fake)
    keys = [c["key"] for c in fake.calls]
    assert keys == [
        "outer[0]/inner[0]/leaf",
        "outer[0]/inner[1]/leaf",
        "outer[0]/tail",
        "outer[1]/inner[0]/leaf",
        "outer[1]/inner[1]/leaf",
        "outer[1]/tail",
        "final",
    ]
    assert fake.calls[0]["message"].endswith("1")  # inner item shadows the outer one
    assert fake.calls[2]["message"].endswith('["out:leaf", "out:leaf"]')


def test_for_each_limits_and_text_lists() -> None:
    nodes = [
        n("start", "start"),
        n("e", "for_each", "start", items="{t}", max_iterations=2),
        agent("b", "e.loop"),
    ]
    with pytest.raises(g.GraphError) as exc:
        run(nodes, {"t": "a\nb\nc"}, Fake())
    assert exc.value.code == "max_iterations_exceeded"
    fake = Fake()
    run(nodes, {"t": "a\nb"}, fake)
    assert len(fake.calls) == 2
    fake = Fake()
    run(nodes, {"t": "[]"}, fake)
    assert fake.calls == []
    with pytest.raises(g.GraphError) as exc:
        run(nodes, {"t": 5}, Fake())
    assert exc.value.code == "bad_items"


def test_while_repeats_until_the_condition_fails_and_is_bounded() -> None:
    cond = [{"field": "score", "op": "<", "value": 3}]
    nodes = [
        n("start", "start"),
        agent("seed", "start", output="score"),
        n("w", "while", "seed", conditions=cond, max_iterations=5),
        agent("improve", "w.loop", output="score"),
        agent("report", "w.done", input="{score}"),
    ]
    counter = {"n": 0}

    def improve(_: dict[str, Any]) -> int:
        counter["n"] += 1
        return counter["n"] + 1

    fake = Fake({"seed": 0, "improve": improve})
    res = run(nodes, {}, fake)
    assert [c["node"] for c in fake.calls] == ["seed", "improve", "improve", "report"]
    assert res.output == "out:report"
    with pytest.raises(g.GraphError) as exc:
        run(nodes, {}, Fake({"seed": 0, "improve": 0}))
    assert exc.value.code == "max_iterations_exceeded"


def test_limits_and_unavailable_approval() -> None:
    chain = [n("start", "start"), agent("a", "start"), agent("b", "a"), agent("c", "b")]
    with pytest.raises(g.GraphError) as exc:
        run(chain, {}, Fake(), max_steps=2)
    assert exc.value.code == "max_steps"
    with pytest.raises(g.GraphError) as exc:
        run(chain, {}, Fake(), max_cost_usd=0.7)
    assert exc.value.code == "max_cost"
    with pytest.raises(g.GraphError) as exc:
        run([n("start", "start"), n("h", "human_approval", "start")], {}, Fake())
    assert exc.value.code == "approval_not_available"
    with pytest.raises(g.GraphError) as exc:
        run([n("start", "start"), agent("a", "start", hitl=True)], {}, Fake())
    assert exc.value.code == "approval_not_available"
    assert exc.value.node == "a"


def test_undefined_variable_at_run_time_names_it() -> None:
    with pytest.raises(g.GraphError) as exc:
        run([n("start", "start"), agent("a", "start", input="{missing}")], {}, Fake())
    assert exc.value.code == "undefined_variable"
