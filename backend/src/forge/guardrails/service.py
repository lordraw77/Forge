"""Policy storage, evaluation and the intervention log (E2.11)."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.guardrails import ner
from forge.guardrails.engine import (
    ACTIONS,
    CHECK_TYPES,
    NER_ENTITIES,
    STAGES,
    Rule,
    Verdict,
    evaluate,
    parse_rules,
)
from forge.guardrails.guard import SAFE_LABELS, GuardResult, from_labels, judge_messages
from forge.guardrails.guard import parse as parse_guard
from forge.guardrails.models import GuardrailEvent, GuardrailPolicy

POLICY_TTL_S = 5.0
MAX_RULES = 50


class GuardrailRuleIn(BaseModel):
    type: str
    stage: list[str] = Field(default_factory=lambda: ["input", "output"])
    action: str = "log"
    params: dict[str, Any] = Field(default_factory=dict)
    fail_mode: str | None = None

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in CHECK_TYPES:
            raise ValueError(f"type must be one of {', '.join(CHECK_TYPES)}")
        return v

    @field_validator("stage")
    @classmethod
    def _stage(cls, v: list[str]) -> list[str]:
        if not v or any(s not in STAGES for s in v):
            raise ValueError(f"stage must be a non-empty list of {', '.join(STAGES)}")
        return v

    @field_validator("action")
    @classmethod
    def _action(cls, v: str) -> str:
        if v not in ACTIONS:
            raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
        return v

    @field_validator("fail_mode")
    @classmethod
    def _fail(cls, v: str | None) -> str | None:
        if v not in (None, "open", "closed"):
            raise ValueError("fail_mode must be open or closed")
        return v


class GuardrailPolicyIn(BaseModel):
    enabled: bool = True
    rules: list[GuardrailRuleIn] = Field(default_factory=list, max_length=MAX_RULES)


class GuardrailPolicyOut(BaseModel):
    enabled: bool
    rules: list[GuardrailRuleIn]
    unavailable: list[str]  # model-backed checks with no endpoint configured
    updated_at: datetime | None = None


class GuardrailEventOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    stage: str
    source: str
    rule_index: int
    check_type: str
    action: str
    label: str
    detail: str
    matches: int


class GuardrailTestIn(BaseModel):
    __test__ = False
    text: str = Field(max_length=100_000)
    stage: str = "input"
    rules: list[GuardrailRuleIn] | None = None  # try an unsaved policy; default: the saved one


class GuardrailTestOut(BaseModel):
    __test__ = False
    action: str
    text: str
    findings: list[dict[str, Any]]
    unavailable: list[str]


def validate_rules(rules: list[GuardrailRuleIn]) -> None:
    for i, r in enumerate(rules):
        if r.type == "json_schema" and not isinstance(r.params.get("schema"), dict):
            raise ProblemError(
                422, "invalid_rule", f"Rule {i + 1}: json_schema needs params.schema"
            )
        if r.type == "grounding" and list(r.stage) != ["output"]:
            raise ProblemError(
                422, "invalid_rule", f"Rule {i + 1}: grounding applies to the output stage only"
            )
        if r.type == "banned_topics" and not r.params.get("terms"):
            raise ProblemError(
                422, "invalid_rule", f"Rule {i + 1}: banned_topics needs params.terms"
            )


def _wants_ner(rule: Rule | GuardrailRuleIn) -> bool:
    return rule.type == "pii" and bool(set(rule.params.get("entities") or ()) & set(NER_ENTITIES))


def _uses_guard(rule: Rule | GuardrailRuleIn) -> bool:
    return rule.type in ("toxicity", "injection") and bool(rule.params.get("model"))


def _needs_threads(rules: list[Rule], stage: str) -> bool:
    """NER is CPU-bound and guard calls block on the network: both leave the event loop."""
    return any(
        stage in r.stage and (_wants_ner(r) or _uses_guard(r) or r.type == "grounding")
        for r in rules
    )


class Guardrails:
    """Gateway-facing: `check()` evaluates and logs; the rest is the policy screen's API."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._cache: dict[uuid.UUID, tuple[float, list[Rule]]] = {}
        # Model-backed checks by type (ADR-013); filled when a guard endpoint is configured.
        self.models: dict[str, Any] = {"ner": ner.detect}
        # Set by the gateway: ask a guard model, and check that a model name is a usable guard.
        self.classify: Callable[..., Coroutine[Any, Any, GuardResult | None]] | None = None
        self.guard_exists: Callable[[uuid.UUID, str, bool], Any] | None = None

    async def rules(self, project_id: uuid.UUID) -> list[Rule]:
        hit = self._cache.get(project_id)
        if hit and time.monotonic() - hit[0] < POLICY_TTL_S:
            return hit[1]
        async with project_scope(self._sessions, project_id) as s:
            row = await s.get(GuardrailPolicy, project_id)
        rules = parse_rules(row.rules) if row and row.enabled else []
        self._cache[project_id] = (time.monotonic(), rules)
        return rules

    async def check(
        self,
        project_id: uuid.UUID,
        stage: str,
        text: str,
        *,
        source: str,
        user_id: uuid.UUID | None = None,
        api_key_id: uuid.UUID | None = None,
        trace_id: str | None = None,
        context: str | None = None,
        extra_rules: Sequence[dict[str, Any]] = (),
    ) -> Verdict:
        """`context`: the source passages of the call, for the `grounding` check.
        `extra_rules`: an agent's own rules, added after the project's (never instead of them)."""
        rules = await self._effective(project_id, extra_rules)
        if _needs_threads(rules, stage):  # NER is CPU-bound, guard calls block on the network
            models = self._models_for(project_id, user_id, asyncio.get_running_loop(), context)
            verdict = await asyncio.to_thread(evaluate, text, stage, rules, models=models)
        else:
            verdict = evaluate(text, stage, rules, models=self.models)
        if verdict.findings:
            async with project_scope(self._sessions, project_id) as s:
                s.add_all(
                    GuardrailEvent(
                        project_id=project_id,
                        stage=stage,
                        source=source,
                        rule_index=f.rule,
                        check_type=f.type,
                        action=f.action,
                        label=f.label[:200],
                        detail=f.detail[:300],
                        matches=len(f.spans),
                        user_id=user_id,
                        api_key_id=api_key_id,
                        trace_id=trace_id,
                    )
                    for f in verdict.findings
                )
        return verdict

    async def check_arguments(
        self,
        project_id: uuid.UUID,
        args: dict[str, Any],
        *,
        tool: str,
        user_id: uuid.UUID | None = None,
        trace_id: str | None = None,
        extra_rules: Sequence[dict[str, Any]] = (),
    ) -> tuple[dict[str, Any], str | None]:
        """The `tool_args` stage: every string value of a tool call's arguments is checked on
        its own (so masking cannot break the JSON). Returns the arguments to use and, when a rule
        blocks, the reason (the call must not run)."""
        if not await self.has_stage(project_id, "tool_args", extra_rules):
            return args, None
        blocked: list[str] = []

        async def walk(value: Any) -> Any:
            if isinstance(value, str):
                verdict = await self.check(
                    project_id,
                    "tool_args",
                    value,
                    source=f"tool:{tool}",
                    user_id=user_id,
                    trace_id=trace_id,
                    extra_rules=extra_rules,
                )
                if verdict.blocked:
                    blocked.extend(
                        f.type for f in verdict.findings if f.action in ("block", "escalate")
                    )
                return verdict.text
            if isinstance(value, list):
                return [await walk(v) for v in value]
            if isinstance(value, dict):
                return {k: await walk(v) for k, v in value.items()}
            return value

        checked = await walk(args)
        if blocked:
            return args, ", ".join(sorted(set(blocked)))
        return checked, None

    async def _effective(
        self, project_id: uuid.UUID, extra_rules: Sequence[dict[str, Any]] = ()
    ) -> list[Rule]:
        rules = await self.rules(project_id)
        return rules + parse_rules(list(extra_rules)) if extra_rules else rules

    async def has_stage(
        self, project_id: uuid.UUID, stage: str, extra_rules: Sequence[dict[str, Any]] = ()
    ) -> bool:
        return any(stage in r.stage for r in await self._effective(project_id, extra_rules))

    # ---- policy screen
    def _unavailable(self, rules: list[GuardrailRuleIn]) -> list[str]:
        """What the screen reports as unable to run: guard checks without a model, grounding
        (no judge yet), and name detection without Presidio or its models."""
        out = {r.type for r in rules if r.type == "grounding" and not r.params.get("model")}
        out |= {r.type for r in rules if r.type == "toxicity" and not r.params.get("model")}
        if self.classify is None:
            out |= {r.type for r in rules if _uses_guard(r) or r.type == "grounding"}
        if any(_wants_ner(r) for r in rules) and not ner.available():
            out.add("ner")
        return sorted(out)

    def _models_for(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID | None,
        loop: asyncio.AbstractEventLoop,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Runners for one project. They are called from a worker thread, so the async guard call
        is handed back to the event loop and waited for."""

        def guard(text: str, rule: Rule) -> bool | None:
            name = rule.params.get("model")
            if not name or self.classify is None:
                return None
            threshold = float(rule.params.get("threshold", 0.5))
            future = asyncio.run_coroutine_threadsafe(
                self.classify(
                    project_id,
                    user_id,
                    str(name),
                    text,
                    threshold,
                    rule.type if rule.params.get("judge") else None,
                ),
                loop,
            )
            try:
                result = future.result(timeout=120)
            except Exception:  # timeout or provider failure: the check could not run
                return None
            if result is None:
                return None
            wanted = {c.lower() for c in rule.params.get("categories") or ()}
            if wanted and result.categories:
                return bool(result.unsafe and wanted & {c.lower() for c in result.categories})
            return bool(result.unsafe)

        def injection(text: str, rule: Rule) -> bool | None:
            return guard(text, rule) if rule.params.get("model") else False

        def grounding(text: str, rule: Rule) -> bool | None:
            if not context:  # nothing to ground the answer in: the check does not apply
                return False
            name = rule.params.get("model")
            if not name or self.classify is None:
                return None
            future = asyncio.run_coroutine_threadsafe(
                self.classify(project_id, user_id, str(name), text, 0.5, "grounding", context),
                loop,
            )
            try:
                result = future.result(timeout=120)
            except Exception:
                return None
            return None if result is None else bool(result.unsafe)

        return {**self.models, "toxicity": guard, "injection": injection, "grounding": grounding}

    async def get_policy(self, project_id: uuid.UUID) -> GuardrailPolicyOut:
        async with project_scope(self._sessions, project_id) as s:
            row = await s.get(GuardrailPolicy, project_id)
        rules = [GuardrailRuleIn(**r) for r in (row.rules if row else [])]
        return GuardrailPolicyOut(
            enabled=row.enabled if row else True,
            rules=rules,
            unavailable=self._unavailable(rules),
            updated_at=row.updated_at if row else None,
        )

    async def put_policy(
        self, project_id: uuid.UUID, user_id: uuid.UUID | None, body: GuardrailPolicyIn
    ) -> GuardrailPolicyOut:
        validate_rules(body.rules)
        self._check_models(project_id, body.rules)
        data = [r.model_dump() for r in body.rules]
        async with project_scope(self._sessions, project_id, user_id) as s:
            row = await s.get(GuardrailPolicy, project_id)
            if row is None:
                s.add(
                    GuardrailPolicy(
                        project_id=project_id, enabled=body.enabled, rules=data, updated_by=user_id
                    )
                )
            else:
                row.enabled, row.rules, row.updated_by = body.enabled, data, user_id
        self._cache.pop(project_id, None)
        return await self.get_policy(project_id)

    async def import_policy(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID | None,
        item: dict[str, Any],
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        """A bundle's policy replaces the project's (the same way `gateway_config` does). Rules
        are validated, and the guard models they name must exist here: a missing one is an
        error, so a policy never arrives half working."""
        body = GuardrailPolicyIn.model_validate(
            {"enabled": item.get("enabled", True), "rules": item.get("rules") or []}
        )
        if dry_run:
            validate_rules(body.rules)
            self._check_models(project_id, body.rules)
        else:
            await self.put_policy(project_id, user_id, body)
        return {
            "kind": "guardrail_policy",
            "name": str(item.get("name") or "guardrails"),
            "action": "updated",
            "warnings": ["replaces the project's current policy"],
        }

    def _check_models(self, project_id: uuid.UUID, rules: list[GuardrailRuleIn]) -> None:
        """A rule naming a guard model must name one the project can use."""
        for i, r in enumerate(rules):
            name = r.params.get("model")
            if (
                name
                and r.type in ("toxicity", "injection", "grounding")
                and self.guard_exists is not None
            ):
                try:
                    # Grounding is always judged by a chat model.
                    self.guard_exists(
                        project_id, str(name), bool(r.params.get("judge")) or r.type == "grounding"
                    )
                except ProblemError as exc:
                    raise ProblemError(
                        422, "invalid_rule", f"Rule {i + 1}: {exc.detail or exc.title}"
                    ) from exc

    async def test(self, project_id: uuid.UUID, body: GuardrailTestIn) -> GuardrailTestOut:
        if body.stage not in STAGES:
            raise ProblemError(422, "invalid_stage", f"stage must be one of {', '.join(STAGES)}")
        if body.rules is not None:
            validate_rules(body.rules)
            rules = parse_rules([r.model_dump() for r in body.rules])
        else:
            rules = await self.rules(project_id)
        models = self._models_for(project_id, None, asyncio.get_running_loop())
        v = await asyncio.to_thread(evaluate, body.text, body.stage, rules, models=models)
        return GuardrailTestOut(
            action=v.action,
            text=v.text,
            findings=[
                {
                    "rule": f.rule,
                    "type": f.type,
                    "action": f.action,
                    "label": f.label,
                    "detail": f.detail,
                    "matches": len(f.spans),
                }
                for f in v.findings
            ],
            unavailable=v.unavailable,
        )

    async def events(self, project_id: uuid.UUID, limit: int = 100) -> list[GuardrailEventOut]:
        async with project_scope(self._sessions, project_id) as s:
            rows = (
                await s.scalars(
                    select(GuardrailEvent).order_by(GuardrailEvent.created_at.desc()).limit(limit)
                )
            ).all()
        return [GuardrailEventOut.model_validate(r, from_attributes=True) for r in rows]


# What other domains (the gateway) may use: only `forge.guardrails.service` is importable.
__all__ = [
    "SAFE_LABELS",
    "GuardResult",
    "GuardrailRuleIn",
    "Guardrails",
    "from_labels",
    "judge_messages",
    "parse_guard",
    "validate_rules",
]
