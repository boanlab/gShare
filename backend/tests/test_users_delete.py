"""Deleting a user: the soft path terminates and settles their live sessions first (what the
console's delete screen promises); a hard delete still refuses while sessions exist."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.users_router import delete_user
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import DomainError
from app.db.models import CreditWallet, Image, Offering, User
from app.db.models import Session as SessionRow


def _admin() -> Principal:
    return Principal(user_id=ids.new("user"), global_roles=["super_admin"])


async def _user_with_sessions(db, statuses):
    user = User(id=ids.new("user"), email=f"{ids.new('user').lower()}@x.kr", name="U",
                password_hash="x", status="active")
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=user.id,
                          balance=Decimal("100"), reserved=Decimal("0"))
    offering = Offering(id=ids.new("offering"), name="o", resource_class="gpu",
                        gpu_model="A100", credit_per_hour=Decimal("60"))
    image = Image(id=ids.new("image"), name="img")
    sessions = [
        SessionRow(id=ids.new("session"), owner_user_id=user.id, cluster_id=ids.new("cluster"),
                   offering_id=offering.id, image_id=image.id, resource_class="gpu",
                   mode="fractional", status=st, gpu_mem_mb=4096, gpu_cores=25,
                   billing_wallet_id=wallet.id, credit_per_hour_snapshot=Decimal("60"))
        for st in statuses
    ]
    async with db.begin():
        db.add_all([user, wallet, offering, image, *sessions])
    # A real hold (ledger row + reserved), so settle-on-delete has something to release.
    from app.domain.credit_engine import CreditEngine

    await CreditEngine(db).hold(wallet.id, Decimal("10"), key=f"hold:{sessions[0].id}")
    return user, wallet, sessions


@pytest.mark.asyncio
async def test_soft_delete_terminates_live_sessions(db):
    user, wallet, sessions = await _user_with_sessions(db, ["running", "paused"])
    resp = await delete_user(user.id, hard=False, principal=_admin(), db=db)
    assert resp.status_code == 204

    db.expunge_all()
    got = await db.get(User, user.id)
    assert got.status == "suspended" and got.deleted_at is not None
    for s in sessions:
        row = await db.get(SessionRow, s.id)
        assert row.status in ("terminating", "terminated"), row.status
        assert row.status_reason == "admin_stopped"
    # settle released the hold: nothing stays reserved for a deactivated account.
    w = await db.get(CreditWallet, wallet.id)
    assert w.reserved == Decimal("0")


@pytest.mark.asyncio
async def test_hard_delete_still_refuses_live_sessions(db):
    user, _, _ = await _user_with_sessions(db, ["paused"])
    with pytest.raises(DomainError) as e:
        await delete_user(user.id, hard=True, principal=_admin(), db=db)
    assert e.value.http == 409


@pytest.mark.asyncio
async def test_soft_delete_also_cleans_error_wrecks(db):
    """error sessions are cleanable since error->terminating became legal; a user delete must
    sweep them up (they may still reference a CR/pod, and a create-path error may hold credit)."""
    user, wallet, sessions = await _user_with_sessions(db, ["error"])
    resp = await delete_user(user.id, hard=False, principal=_admin(), db=db)
    assert resp.status_code == 204
    db.expunge_all()
    row = await db.get(SessionRow, sessions[0].id)
    assert row.status == "terminated"
    w = await db.get(CreditWallet, wallet.id)
    assert w.reserved == Decimal("0")


@pytest.mark.asyncio
async def test_terminate_error_session_keeps_original_reason(db):
    from app.domain.session_service import SessionService

    user, wallet, sessions = await _user_with_sessions(db, ["error"])
    sid = sessions[0].id
    async with db.begin():
        row = await db.get(SessionRow, sid)
        row.status_reason = "quota_exceeded"   # why it errored — must survive the cleanup
    got = await SessionService(db).terminate(sid, forced=True, reason="admin_stopped")
    assert got.status == "terminated"
    db.expunge_all()
    row = await db.get(SessionRow, sid)
    assert row.status_reason == "quota_exceeded"
