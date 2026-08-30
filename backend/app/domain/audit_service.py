"""AuditService — hash-chained audit log writer.

This plane is the only DB writer. Operator privileged actions arrive via
POST /internal/audit/operator and are appended with a prev_hash/entry_hash chain.

Hash chain: every row stores ``prev_hash`` the previous row's entry_hash, or "GENESIS"
for the first row and ``entry_hash`` = sha256 over a canonical serialization of
(prev_hash + id + actor + action + target + detail + result + created_at). The chain is
maintained in application code only — there is NO database trigger and no DB-level append-only
enforcement — so it detects casual tampering through this plane, not a hostile DBA. Retention
pruning removes the oldest rows; the pruner records the surviving head's prev_hash in an
``audit.retention`` entry, which verify_chain accepts as the new anchor.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.internal import OperatorAuditEvent
from app.core import ids
from app.db.models import AuditLog, Membership, Project

GENESIS = "GENESIS"


def _canonical(payload: dict) -> str:
    """Deterministic JSON for hashing (sorted keys, no whitespace, str-coerced values)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _entry_hash(
    prev_hash: str,
    *,
    entry_id: str,
    actor: str,
    action: str,
    target: str | None,
    result: str | None,
    detail: dict,
    created_at: datetime,
) -> str:
    """sha256 over prev_hash + this row's canonical payload (chain semantics)."""
    body = prev_hash + "|" + _canonical(
        {
            "id": entry_id,
            "actor": actor,
            "action": action,
            "target": target or "",
            "result": result or "",
            "detail": detail or {},
            "created_at": created_at.isoformat(),
        }
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_operator_action(self, ev: OperatorAuditEvent) -> None:
        """Append an operator action to audit_log with hash chaining."""
        await self._append(
            actor=ev.actor,
            action=ev.action,
            target=ev.target,
            result=ev.result,
            detail=dict(ev.detail or {}),
            trace_id=ev.trace_id,
            created_at=ev.ts,
        )

    async def record(self, actor: str, action: str, target: str | None = None, **detail) -> None:
        """Generic audit append used by REST mutations."""
        result = detail.pop("result", None)
        trace_id = detail.pop("trace_id", None)
        # Hierarchy scope: an explicit value wins; otherwise derive the group and organization from
        # the actor's memberships.
        org_id = detail.pop("org_id", None)
        group_id = detail.pop("group_id", None)
        if org_id is None and group_id is None:
            group_id, org_id = await self._scope_for_actor(actor)
        await self._append(
            actor=actor,
            action=action,
            target=target,
            result=result,
            detail=detail,
            trace_id=trace_id,
            created_at=None,
            org_id=org_id,
            group_id=group_id,
        )

    async def _scope_for_actor(self, actor: str) -> tuple[str | None, str | None]:
        """Derive the (group_id, org_id) scope from an actor.

        A group membership wins, contributing both the group and its organization. Failing that, an
        organization membership (org_admin) contributes its org_id, so an org_admin's actions show
        up in their own organization's audit log. With neither, the result is (None, None) and only
        a super_admin can see the entry."""
        pid = await self.db.scalar(
            select(Membership.group_id)
            .where(Membership.user_id == actor, Membership.group_id.is_not(None))
            .limit(1)
        )
        if pid is not None:
            oid = await self.db.scalar(select(Project.org_id).where(Project.id == pid))
            return pid, oid
        # With no group membership — a pure org_admin, for instance — only the organization scope
        # is set.
        oid = await self.db.scalar(
            select(Membership.org_id)
            .where(Membership.user_id == actor, Membership.org_id.is_not(None))
            .limit(1)
        )
        return None, oid

    # ── internals ───────────────────────────────────────────────────────────────
    async def _append(
        self,
        *,
        actor: str,
        action: str,
        target: str | None,
        result: str | None,
        detail: dict,
        trace_id: str | None,
        created_at: datetime | None,
        org_id: str | None = None,
        group_id: str | None = None,
    ) -> AuditLog:
        """Insert one chained row. prev_hash = last row's entry_hash (or GENESIS)."""
        prev_hash = await self._last_entry_hash()
        entry_id = ids.new("audit")
        # Anchor the hashed timestamp deterministically; the DB server_default also stamps
        # created_at, but we hash an explicit value so the chain is reproducible.
        ts = created_at or datetime.now(UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        detail = detail or {}

        entry_hash = _entry_hash(
            prev_hash,
            entry_id=entry_id,
            actor=actor,
            action=action,
            target=target,
            result=result,
            detail=detail,
            created_at=ts,
        )
        row = AuditLog(
            id=entry_id,
            actor=actor,
            action=action,
            target=target,
            result=result,
            detail=detail,
            trace_id=trace_id,
            org_id=org_id,
            group_id=group_id,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            # The hashed timestamp MUST be the stored one: leaving created_at to the DB
            # server_default stamps a slightly different time than the one hashed above, which
            # made every chain verification fail with entry_hash_mismatch.
            created_at=ts,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def _last_entry_hash(self) -> str:
        """Return the most recent row's entry_hash, or GENESIS if the log is empty."""
        last = await self.db.scalar(
            select(AuditLog.entry_hash)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        return last or GENESIS

    async def verify_chain(self, limit: int | None = None) -> dict:
        """Walk the chain oldest->newest and recompute each entry_hash (integrity check).

        Returns ``{"ok": bool, "checked": int, "broken_at": id|None, "reason": str|None}``.
        A break means a row was tampered with (entry_hash mismatch) or unlinked (prev_hash !=
        predecessor's entry_hash). When retention has pruned the head, the surviving oldest row
        no longer starts at GENESIS; its prev_hash is accepted iff it matches the anchor the
        pruner recorded in the latest ``audit.retention`` entry.
        """
        stmt = select(AuditLog).order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = list(await self.db.scalars(stmt))

        expected_prev = GENESIS
        if rows and rows[0].prev_hash != GENESIS:
            anchor_row = (
                await self.db.scalars(
                    select(AuditLog)
                    .where(AuditLog.action == "audit.retention")
                    .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                    .limit(1)
                )
            ).first()
            anchor = (anchor_row.detail or {}).get("new_anchor_prev_hash") if anchor_row else None
            if anchor == rows[0].prev_hash:
                expected_prev = rows[0].prev_hash
        checked = 0
        for row in rows:
            created_at = row.created_at
            if created_at is not None and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)

            if row.prev_hash != expected_prev:
                return {
                    "ok": False,
                    "checked": checked,
                    "broken_at": row.id,
                    "reason": "prev_hash_mismatch",
                }
            recomputed = _entry_hash(
                row.prev_hash or GENESIS,
                entry_id=row.id,
                actor=row.actor,
                action=row.action,
                target=row.target,
                result=row.result,
                detail=row.detail or {},
                created_at=created_at or datetime.now(UTC),
            )
            if recomputed != row.entry_hash:
                return {
                    "ok": False,
                    "checked": checked,
                    "broken_at": row.id,
                    "reason": "entry_hash_mismatch",
                }
            expected_prev = row.entry_hash
            checked += 1

        return {"ok": True, "checked": checked, "broken_at": None, "reason": None}
