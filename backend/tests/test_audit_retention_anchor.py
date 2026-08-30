"""Retention pruning must not brick chain verification: the pruner records the surviving head's
prev_hash as an anchor and verify_chain accepts it."""
from __future__ import annotations

import pytest

from app.domain.audit_service import AuditService


@pytest.mark.asyncio
async def test_verify_survives_retention_prune(db):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import delete, select

    from app.db.models import AuditLog

    svc = AuditService(db)

    # SQLite truncates microseconds on read, which would break hash recomputation for rows
    # stamped with a sub-second created_at; Postgres preserves them. Use whole-second
    # timestamps so the test exercises the chain logic, not the store's precision.
    base = datetime(2026, 1, 1, tzinfo=UTC)
    async with db.begin():
        for i in range(5):
            await svc._append(actor="u", action=f"act.{i}", target=None, result=None,
                              detail={}, trace_id=None, created_at=base + timedelta(seconds=i))
    ok = await svc.verify_chain()
    assert ok["ok"] is True, ok
    assert ok["checked"] == 5
    await db.commit()   # close the autobegun read tx before the next begin()

    # Simulate the pruner: delete the 2 oldest rows, then record the anchor like run() does.
    async with db.begin():
        rows = list(await db.scalars(select(AuditLog).order_by(AuditLog.created_at, AuditLog.id)))
        for r in rows[:2]:
            await db.execute(delete(AuditLog).where(AuditLog.id == r.id))
        head = rows[2]
        await svc._append(
            actor="system", action="audit.retention", target=None, result=None,
            detail={"pruned": 2, "new_anchor_prev_hash": head.prev_hash},
            trace_id=None, created_at=base + timedelta(seconds=10),
        )

    res = await svc.verify_chain()
    assert res["ok"] is True, res
    await db.commit()

    # Tampering after the anchor is still caught.
    async with db.begin():
        victim = (await db.scalars(select(AuditLog).order_by(AuditLog.created_at))).first()
        victim.actor = "attacker"
    res2 = await svc.verify_chain()
    assert res2["ok"] is False
