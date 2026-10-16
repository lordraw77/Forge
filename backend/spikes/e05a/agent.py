"""LangGraph agent used inside one Temporal activity (ADR-004).

The "LLM" is scripted so the spike is deterministic and needs no model endpoint:
step 1 calls `lookup`, step 2 calls `send_email` (requires approval), step 3 answers.
Every node execution is journaled, so re-executions after a crash are visible.
"""

from __future__ import annotations

import asyncio
import operator
import os
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from spikes.e05a import store

# Window after the lookup side effect during which the runner kills the worker.
LOOKUP_POST_PROCESSING_S = float(os.environ.get("SPIKE_LOOKUP_DELAY_S", "6"))


class AgentState(TypedDict):
    task: str
    step: int
    pending: dict[str, Any] | None
    results: Annotated[list[str], operator.add]
    answer: str | None


def _thread(config: RunnableConfig) -> str:
    return str(config["configurable"]["thread_id"])


async def planner(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    step = state["step"] + 1
    await store.record(_thread(config), "llm_step", str(step))
    if step == 1:
        call = {"id": "call-1", "tool": "lookup", "args": {"q": state["task"]}}
        return {"step": step, "pending": call}
    if step == 2:
        call = {
            "id": "call-2",
            "tool": "send_email",
            "args": {"to": "ops@example.com", "subject": "Report", "body": state["results"][0]},
        }
        return {"step": step, "pending": call}
    return {"step": step, "pending": None, "answer": " | ".join(state["results"])}


async def tools(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    thread = _thread(config)
    call = state["pending"]
    assert call is not None
    key = f"{thread}:{call['id']}"
    await store.record(thread, "tool_node", call["tool"])

    if call["tool"] == "lookup":

        async def lookup_effect() -> str:
            await store.record(thread, "tool_effect", "lookup")
            return f"lookup({call['args']['q']})=42"

        result, _ = await store.idempotent(key, lookup_effect)
        await asyncio.sleep(LOOKUP_POST_PROCESSING_S)  # post-processing: the kill window
        return {"pending": None, "results": [result]}

    # send_email: code before interrupt() runs again on resume, so it must stay side-effect free.
    decision = interrupt({"action": "send_email", **call["args"]})
    if decision.get("action") != "approve":
        return {"pending": None, "results": ["email cancelled"]}
    subject = decision.get("subject", call["args"]["subject"])

    async def send_effect() -> str:
        await store.record(thread, "tool_effect", f"send_email:{subject}")
        return f"email sent: {subject}"

    result, _ = await store.idempotent(key, send_effect)
    return {"pending": None, "results": [result]}


def _route(state: AgentState) -> str:
    return "tools" if state["pending"] else END


def build_graph(checkpointer: AsyncPostgresSaver) -> CompiledStateGraph:  # type: ignore[type-arg]
    builder = StateGraph(AgentState)
    builder.add_node("planner", planner)
    builder.add_node("tools", tools)
    builder.add_edge(START, "planner")
    builder.add_conditional_edges("planner", _route, ["tools", END])
    builder.add_edge("tools", "planner")
    return builder.compile(checkpointer=checkpointer)


@dataclass
class NodeOutcome:
    status: str  # "completed" | "interrupted"
    answer: str | None = None
    payload: dict[str, Any] | None = None
    start_mode: str = ""  # how this invocation started: fresh | continue | resume | finished


async def run_agent(
    checkpointer: AsyncPostgresSaver, thread_id: str, task: str, resume: dict[str, Any] | None
) -> NodeOutcome:
    """Run or continue the agent for one Temporal activity attempt.

    The start mode is chosen from the checkpoint, so a retried activity never re-submits
    the initial input (which would start a second run on the same thread).
    """
    graph = build_graph(checkpointer)
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)

    inp: Any
    if resume is not None:
        inp, mode = Command(resume=resume), "resume"
    elif not snapshot.values:
        inp, mode = (
            {"task": task, "step": 0, "pending": None, "results": [], "answer": None},
            "fresh",
        )
    elif snapshot.next:
        inp, mode = None, "continue"  # crashed mid-run: continue from the last checkpoint
    else:
        return NodeOutcome("completed", answer=snapshot.values.get("answer"), start_mode="finished")

    await store.record(thread_id, "invoke", mode)
    result = await graph.ainvoke(inp, config)
    if result.get("__interrupt__"):
        return NodeOutcome("interrupted", payload=result["__interrupt__"][0].value, start_mode=mode)
    return NodeOutcome("completed", answer=result["answer"], start_mode=mode)
