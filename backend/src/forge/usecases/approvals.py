"""Human in the loop (M2.4): approvals raised by a run, the worker that waits for them, and the
API side that records decisions (from the UI, the API or a one-time email link).

An approval row is the single source of truth. The workflow activity that waits for a person
polls the row, so no signal can be lost and a worker restart simply resumes the wait."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge.admin.service import audit
from forge.core import egress
from forge.core.context import Actor
from forge.core.crypto import random_token, sha256
from forge.core.db import project_scope
from forge.core.errors import ProblemError
from forge.core.logging import get_logger
from forge.core.models import utcnow
from forge.core.settings import Settings
from forge.tenancy.service import ProjectAccess, TenancyService
from forge.tools.service import ToolStudio
from forge.usecases.mail import Mailer
from forge.usecases.models import Approval, ApprovalToken, UseCaseRun, UseCaseRunStep
from forge.usecases.schemas import ApprovalDetail, ApprovalOut, DecisionIn

log = get_logger(__name__)
LINK_PREFIX = "fap"
POLL_S = 2.0
EDIT_BYTES = 64 * 1024
NOT_FOUND = ProblemError(404, "not_found", "Approval not found")
ADMIN_TYPES = ("project_admin", "super_admin")


def new_token(project_id: uuid.UUID) -> str:
    return f"{LINK_PREFIX}_{project_id.hex}_{random_token(32)}"


def _token_project(token: str) -> uuid.UUID | None:
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != LINK_PREFIX:
        return None
    try:
        return uuid.UUID(hex=parts[1])
    except ValueError:
        return None


def may_decide(row: Approval, access: ProjectAccess) -> bool:
    """`approval.decide`, narrowed to the node's assignees (Project Admins always may)."""
    if "approval.decide" not in access.permissions:
        return False
    if not row.assignees or access.role_type in ADMIN_TYPES:
        return True
    return access.role_name in row.assignees


def _json(value: Any) -> Any:
    raw = json.dumps(value, default=str, ensure_ascii=False)
    return json.loads(raw) if len(raw.encode()) <= EDIT_BYTES else {"truncated": raw[:EDIT_BYTES]}


class ApprovalRuntime:
    """Worker side: raise the approval, tell people, wait."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        tenancy: TenancyService,
        mailer: Mailer,
        settings: Settings,
        tools: ToolStudio,
    ) -> None:
        self._sessions = sessions
        self._tenancy = tenancy
        self._mailer = mailer
        self._settings = settings
        self._tools = tools

    async def wait(
        self, project_id: uuid.UUID, run_id: uuid.UUID, call: dict[str, Any]
    ) -> dict[str, Any]:
        key = str(call["key"])
        hours = int(call.get("timeout_hours") or self._settings.approval_default_hours)
        async with project_scope(self._sessions, project_id) as s:
            run = await s.get(UseCaseRun, run_id)
            if run is None:
                return {"decision": "cancelled", "edited": None, "comment": ""}
            row = (
                await s.execute(
                    select(Approval).where(Approval.run_id == run_id, Approval.step_key == key)
                )
            ).scalar_one_or_none()
            if row is None:
                row = Approval(
                    project_id=project_id,
                    run_id=run_id,
                    step_key=key,
                    node_id=str(call["node"]),
                    kind=str(call["kind"]),
                    instructions=str(call.get("instructions") or "")[:4000],
                    content=_json(call["content"]),
                    assignees=[str(a) for a in call.get("assignees") or []],
                    requested_by=run.created_by,
                    expires_at=utcnow() + timedelta(hours=hours),
                )
                s.add(row)
                await s.flush()
            if await s.get(UseCaseRunStep, (run_id, key)) is None:
                s.add(
                    UseCaseRunStep(
                        run_id=run_id,
                        step_key=key,
                        project_id=project_id,
                        node_id=row.node_id,
                        node_type="human_approval",
                        status="running",
                        input=_json(call["content"]),
                    )
                )
            if run.status == "running":
                run.status = "waiting_approval"
            approval_id, fresh = row.id, row.notified_at is None
        if fresh:
            await self._notify(project_id, approval_id)
        while True:
            done = await self._poll(project_id, approval_id)
            if done is not None:
                return done
            await asyncio.sleep(POLL_S)

    async def _poll(self, project_id: uuid.UUID, approval_id: uuid.UUID) -> dict[str, Any] | None:
        async with project_scope(self._sessions, project_id) as s:
            row = await s.get(Approval, approval_id)
            assert row is not None
            if row.status == "pending" and utcnow() >= row.expires_at:
                row.status, row.decided_at = "expired", utcnow()
            if row.status == "pending":
                return None
            step = await s.get(UseCaseRunStep, (row.run_id, row.step_key))
            if step is not None and step.status == "running":
                step.finished_at = utcnow()
                step.latency_ms = int((step.finished_at - step.started_at).total_seconds() * 1000)
                step.status = "succeeded" if row.status == "approved" else "failed"
                if row.status == "approved":
                    step.output = _json({"value": row.edited if row.edited is not None else None})
                else:
                    step.error_code = f"approval_{row.status}"
                    step.error = (row.comment or "")[:500]
            pending = (
                await s.execute(
                    select(func.count())
                    .select_from(Approval)
                    .where(
                        Approval.run_id == row.run_id,
                        Approval.status == "pending",
                        Approval.id != row.id,
                    )
                )
            ).scalar_one()
            run = await s.get(UseCaseRun, row.run_id)
            if run is not None and run.status == "waiting_approval" and not pending:
                run.status = "running"
            decision = {"approved": "approve", "rejected": "reject"}.get(row.status, row.status)
            return {"decision": decision, "edited": row.edited, "comment": row.comment or ""}

    # ------------------------------------------------------------------ notifications
    async def _notify(self, project_id: uuid.UUID, approval_id: uuid.UUID) -> None:
        async with project_scope(self._sessions, project_id) as s:
            row = await s.get(Approval, approval_id)
            assert row is not None
            run = await s.get(UseCaseRun, row.run_id)
            assert run is not None
            name, node, assignees = run.use_case_name, row.node_id, list(row.assignees)
            expires, webhook, run_id = row.expires_at, run.webhook_url, run.id
        base = self._settings.public_url.rstrip("/")
        review_url = f"{base}/approvals/{approval_id}"
        if self._mailer.enabled:
            for who in await self._tenancy.approvers(project_id, assignees):
                if not who.email:
                    continue
                token = new_token(project_id)
                async with project_scope(self._sessions, project_id) as s:
                    s.add(
                        ApprovalToken(
                            token_hash=sha256(token).hex(),
                            project_id=project_id,
                            approval_id=approval_id,
                            user_id=who.user_id,
                            expires_at=expires,
                        )
                    )
                body = (
                    f"Hello {who.full_name or ''},\n\n"
                    f'The use case "{name}" is waiting for your approval (step "{node}").\n\n'
                    f"Review and decide without signing in (one-time link, valid until "
                    f"{expires:%Y-%m-%d %H:%M} UTC):\n{base}/approve/{token}\n\n"
                    f"Or open it in Forge:\n{review_url}\n"
                )
                await self._mailer.send(who.email, f"Approval needed: {name}", body)
        if webhook:
            await self._webhook(project_id, run_id, webhook, approval_id, name, node, review_url)
        async with project_scope(self._sessions, project_id) as s:
            row = await s.get(Approval, approval_id)
            if row is not None:
                row.notified_at = utcnow()

    async def _webhook(
        self,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        url: str,
        approval_id: uuid.UUID,
        name: str,
        node: str,
        review_url: str,
    ) -> None:
        body = {
            "event": "use_case_run.approval_requested",
            "run_id": str(run_id),
            "approval_id": str(approval_id),
            "use_case_name": name,
            "node": node,
            "review_url": review_url,
        }
        try:
            hosts, allow_private = await self._tools.get_policy(
                Actor(id=None, label="approval"), project_id
            )
            await egress.request(
                "POST", url, hosts, allow_private=allow_private, json_body=body, timeout_s=10
            )
        except Exception as exc:  # a webhook problem never blocks the approval
            log.warning("approval.webhook_failed", run=str(run_id), error=str(exc)[:200])


class Approvals:
    """API side: list, read and decide."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], tenancy: TenancyService) -> None:
        self._sessions = sessions
        self._tenancy = tenancy

    @asynccontextmanager
    async def _session(self, actor: Actor, project_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        async with project_scope(self._sessions, project_id, actor.id) as s:
            yield s

    @staticmethod
    def _out(row: Approval, run: UseCaseRun) -> dict[str, Any]:
        return {
            **{
                k: getattr(row, k)
                for k in (
                    "id",
                    "run_id",
                    "node_id",
                    "kind",
                    "status",
                    "requested_at",
                    "expires_at",
                    "decided_by",
                    "decided_at",
                    "decided_via",
                    "comment",
                )
            },
            "use_case_id": run.use_case_id,
            "use_case_name": run.use_case_name,
        }

    async def list(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        access: ProjectAccess,
        *,
        pending_only: bool = True,
        limit: int = 100,
    ) -> list[ApprovalOut]:
        stmt = (
            select(Approval, UseCaseRun)
            .join(UseCaseRun, UseCaseRun.id == Approval.run_id)
            .order_by(Approval.requested_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        if pending_only:
            stmt = stmt.where(Approval.status == "pending")
        async with self._session(actor, project_id) as s:
            rows = (await s.execute(stmt)).all()
            return [
                ApprovalOut.model_validate(self._out(a, r))
                for a, r in rows
                if may_decide(a, access)
            ]

    async def get(
        self, actor: Actor, project_id: uuid.UUID, access: ProjectAccess, approval_id: uuid.UUID
    ) -> ApprovalDetail:
        async with self._session(actor, project_id) as s:
            return await self._detail(s, access, approval_id)

    async def _detail(
        self, s: AsyncSession, access: ProjectAccess, approval_id: uuid.UUID
    ) -> ApprovalDetail:
        row = (
            await s.execute(
                select(Approval, UseCaseRun)
                .join(UseCaseRun, UseCaseRun.id == Approval.run_id)
                .where(Approval.id == approval_id)
            )
        ).first()
        if row is None or not may_decide(row[0], access):
            raise NOT_FOUND
        a, run = row
        return ApprovalDetail.model_validate(
            {
                **self._out(a, run),
                "instructions": a.instructions,
                "content": a.content,
                "edited": a.edited,
                "assignees": list(a.assignees),
            }
        )

    async def decide(
        self,
        actor: Actor,
        project_id: uuid.UUID,
        access: ProjectAccess,
        approval_id: uuid.UUID,
        body: DecisionIn,
        *,
        via: str,
    ) -> ApprovalDetail:
        async with self._session(actor, project_id) as s:
            row = (
                await s.execute(
                    select(Approval).where(Approval.id == approval_id).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or not may_decide(row, access):
                raise NOT_FOUND
            if row.status != "pending":
                raise ProblemError(
                    409, "approval_decided", f"This approval is already {row.status}"
                )
            if utcnow() >= row.expires_at:
                row.status, row.decided_at = "expired", utcnow()
                raise ProblemError(409, "approval_expired", "This approval has expired")
            if body.decision == "reject" and not body.comment.strip():
                raise ProblemError(422, "feedback_required", "Say why you reject it")
            edited = body.edited if body.decision == "approve" else None
            if edited is not None:
                self._check_edit(row.kind, edited)
            row.status = "approved" if body.decision == "approve" else "rejected"
            row.decided_by, row.decided_at, row.decided_via = actor.id, utcnow(), via
            row.edited, row.comment = edited, body.comment.strip() or None
            await s.execute(
                update(ApprovalToken)
                .where(ApprovalToken.approval_id == row.id, ApprovalToken.used_at.is_(None))
                .values(used_at=utcnow())
            )
            await audit.record(
                s,
                actor,
                "approval.decide",
                "approval",
                row.id,
                {"decision": body.decision, "edited": edited is not None, "via": via},
                project_id=project_id,
            )
            await s.flush()
            return await self._detail(s, access, approval_id)

    @staticmethod
    def _check_edit(kind: str, edited: Any) -> None:
        if len(json.dumps(edited, default=str).encode()) > EDIT_BYTES:
            raise ProblemError(422, "edit_too_large", "The edit is too large")
        if kind == "review" and not isinstance(edited, str):
            raise ProblemError(422, "invalid_edit", "Edit the text as a string")
        if kind == "tool_call" and not isinstance(edited, dict):
            raise ProblemError(422, "invalid_edit", "Edit the arguments as a JSON object")

    # ------------------------------------------------------------------ email links
    async def _token(
        self, token: str, *, consume_check: bool = True
    ) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        """(project, approval, user) of a valid, unused, unexpired token; 404 otherwise."""
        project_id = _token_project(token)
        if project_id is None:
            raise NOT_FOUND
        async with project_scope(self._sessions, project_id) as s:
            row = (
                await s.execute(
                    select(ApprovalToken).where(ApprovalToken.token_hash == sha256(token).hex())
                )
            ).scalar_one_or_none()
            if row is None or row.used_at is not None or utcnow() >= row.expires_at:
                raise NOT_FOUND
            return project_id, row.approval_id, row.user_id

    async def link_view(self, token: str) -> ApprovalDetail:
        project_id, approval_id, user_id = await self._token(token)
        actor = Actor(id=user_id, label="approval_link")
        access = await self._tenancy.access(actor, project_id)
        return await self.get(actor, project_id, access, approval_id)

    async def link_decide(self, token: str, body: DecisionIn) -> ApprovalDetail:
        project_id, approval_id, user_id = await self._token(token)
        actor = Actor(id=user_id, label="approval_link")
        access = await self._tenancy.access(actor, project_id)
        return await self.decide(actor, project_id, access, approval_id, body, via="link")
