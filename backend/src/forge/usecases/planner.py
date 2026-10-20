"""Planner Agent (US-2.8.2): turns a use case's requirements and a chat into a workflow
definition in the ADR-010 schema. It knows the project's published agents, tools and prompts,
checks its own answer with the same validator the editor uses and repairs it (bounded retries).
It never creates or saves anything: the result is a proposal the builder accepts."""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from forge.core.context import Actor
from forge.core.errors import ProblemError
from forge.gateway.service import Caller, Gateway
from forge.usecases import definition as defs
from forge.usecases.files import UseCaseFiles
from forge.usecases.schemas import UseCaseIssueOut
from forge.usecases.service import UseCaseStudio

PLANNER_MODEL = "default-chat"
MAX_ATTEMPTS = 3
MAX_HISTORY = 12
FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

_SYSTEM = """You are the Planner of Forge, a platform that runs multi-agent use cases. You \
design the workflow of a use case from its requirements and refine it with the builder in chat.

You may use ONLY the assets listed under "Library", written exactly as name@version with the \
version shown. If the work needs a capability no listed asset provides, do not invent one: add it \
to "missing" with a short purpose, and plan around it as far as possible.

Workflow formats ("pattern" decides which one):
- supervisor: {{"agents": [{{"agent_ref": "name@version", "task": "what this agent must do"}}]}}. \
Agents run in order; the first is the Team Leader and receives the input form values; each next \
agent receives the previous output.
- graph: {{"nodes": [...]}}. Node types and fields:
  - start: {{"id": "start", "type": "start"}} (exactly one; the input form fields are variables)
  - agent: agent_ref, task, input (text with {{variables}}), output (variable name, default = id)
  - tool: tool_ref, args (object; values may use {{variables}}), output
  - llm: prompt_ref, variables (object name -> text with {{variables}}), output
  - router: sections [{{"name", "combine": "and|or", "conditions": [{{"field": variable, \
"op": "=|!=|>|<|contains", "value"}}]}}]; its outputs are "<id>.<section>" and "<id>.default"
  - for_each: items ("{{variable}}" holding a list or lines of text), max_iterations; outputs \
"<id>.loop" (once per item, with {{item}} and {{index}}) and "<id>.done" (a body node's output \
variable is then the list of all iterations)
  - while: conditions, combine, max_iterations; outputs "<id>.loop" and "<id>.done"
  - human_approval: pauses for a person (not available yet; avoid unless asked)
  - end: output (text with {{variables}}) = the final result
  Every node except start has "after": ["node", "router.section", "loop.loop", "loop.done"]; \
"after_mode" is "all" (default) or "any". Ids look like [a-z][a-z0-9_]*. Use only variables that \
are defined on every path before use: input form fields, outputs of earlier nodes, {{item}} and \
{{index}} inside loops. Keep graphs as small as the task allows.

Answer with ONE json object in a ```json fenced block and nothing else:
{{"reply": "short message to the builder, in the builder's language",
 "definition": <the complete workflow, or null when you only answer a question>,
 "missing": [{{"kind": "agent|tool|prompt", "name": "suggested name", "purpose": "..."}}]}}
Always return the COMPLETE definition, not a patch."""


class PlanMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=6000)


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=4000)
    history: list[PlanMessage] = Field(default_factory=list, max_length=40)
    definition: dict[str, Any] | None = Field(
        default=None, description="The definition as the builder has it now (default: the draft)"
    )


class MissingAsset(BaseModel):
    kind: str
    name: str
    purpose: str = ""


class PlanOut(BaseModel):
    reply: str
    definition: dict[str, Any] | None
    issues: list[UseCaseIssueOut]
    missing: list[MissingAsset]
    attempts: int
    cost: float


def extract_json(text: str) -> dict[str, Any] | None:
    """The first JSON object in the model's answer (fenced or bare)."""
    candidates = [m.group(1) for m in FENCE.finditer(text)] + [text]
    for raw in candidates:
        raw = raw.strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            value = json.loads(raw[start : end + 1])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


class Planner:
    def __init__(self, gateway: Gateway, studio: UseCaseStudio, files: UseCaseFiles) -> None:
        self._gateway = gateway
        self._studio = studio
        self._files = files

    @staticmethod
    def _library(assets: dict[str, list[dict[str, Any]]]) -> str:
        lines: list[str] = []
        for kind in ("agent", "tool", "prompt"):
            lines.append(f"{kind}s:")
            items = assets.get(kind) or []
            lines += [
                f"- {a['name']}@{a['version']}: {(a['description'] or '').strip()[:200]}"
                for a in items
            ] or ["- (none)"]
        return "\n".join(lines)

    async def plan(
        self, actor: Actor, project_id: uuid.UUID, use_case_id: uuid.UUID, body: PlanIn
    ) -> PlanOut:
        uc = await self._studio.get(actor, project_id, use_case_id)
        assets = await self._studio.assets(actor, project_id)
        current = body.definition if body.definition is not None else uc.definition
        names = [str(f["name"]) for f in uc.input_fields]
        attachment = await self._files.requirements_text(actor, project_id, use_case_id)
        attached = (
            f"Attached requirements document (excerpt):\n{attachment}\n\n" if attachment else ""
        )
        context = (
            f"Pattern: {uc.pattern}\n"
            f"Use case: {uc.name}\n"
            f"Requirements:\n{uc.requirements or uc.description or '(none written yet)'}\n\n"
            f"{attached}"
            f"Input form fields (variables): {', '.join(names) or '(none)'}\n"
            f"Output type: {uc.output_type}\n\n"
            f"Library\n{self._library(assets)}\n\n"
            f"Current definition:\n```json\n{json.dumps(current, ensure_ascii=False)[:12000]}\n```"
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": context},
            {"role": "assistant", "content": "Understood. What should I plan or change?"},
            *({"role": m.role, "content": m.content} for m in body.history[-MAX_HISTORY:]),
            {"role": "user", "content": body.message},
        ]
        caller = Caller(project_id=project_id, user_id=actor.id, trace_id=f"planner:{use_case_id}")
        cost = Decimal(0)
        reply, definition, missing = "", None, []
        issues: list[defs.Issue] = []
        attempts = 0
        for attempts in range(1, MAX_ATTEMPTS + 1):  # noqa: B007 - the last value is reported
            try:
                response = await self._gateway.service.chat(
                    caller, PLANNER_MODEL, {"messages": messages, "temperature": 0.2}
                )
            except ProblemError as exc:
                raise ProblemError(
                    502, "planner_unavailable", "The Planner could not reach the model", exc.title
                ) from exc
            if response.info:
                cost += response.info.cost
            choice = ((response.body or {}).get("choices") or [{}])[0]
            text = str((choice.get("message") or {}).get("content") or "")
            parsed = extract_json(text)
            if parsed is None:
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "Reply with the json object in a ```json block."},
                ]
                issues = [defs.Issue("planner_format", "The Planner did not return JSON")]
                continue
            reply = str(parsed.get("reply") or "")
            raw_def = parsed.get("definition")
            definition = raw_def if isinstance(raw_def, dict) else None
            missing = self._missing(parsed.get("missing"))
            if definition is None:
                issues = []
                break
            issues = await self._check(actor, project_id, uc.pattern, definition, uc.input_fields)
            if not issues:
                break
            messages += [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": "The definition has problems. Return the corrected complete "
                    "definition in the same json format.\n"
                    + "\n".join(
                        f"- {i.node + ': ' if i.node else ''}{i.message}" for i in issues[:15]
                    ),
                },
            ]
        return PlanOut(
            reply=reply
            or "I could not produce a valid plan; try describing the steps in more detail.",
            definition=definition,
            issues=[UseCaseIssueOut(**i.as_dict()) for i in issues],
            missing=missing,
            attempts=attempts,
            cost=float(cost),
        )

    async def _check(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        pattern: str,
        definition: dict[str, Any],
        input_fields: list[dict[str, Any]],
    ) -> list[defs.Issue]:
        issues = self._studio.semantic_issues(pattern, definition, input_fields)
        issues += defs.completeness(pattern, definition)
        if not issues:
            issues += await self._studio.reference_issues(actor, project_id, pattern, definition)
        return issues

    @staticmethod
    def _missing(raw: Any) -> list[MissingAsset]:
        out: list[MissingAsset] = []
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, dict) and item.get("kind") in {"agent", "tool", "prompt"}:
                out.append(
                    MissingAsset(
                        kind=str(item["kind"]),
                        name=str(item.get("name") or "")[:80],
                        purpose=str(item.get("purpose") or "")[:300],
                    )
                )
        return out[:10]
