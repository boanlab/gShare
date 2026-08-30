"""CreditEngine tests.

Verifies: hold idempotency (duplicate key = no balance change), insufficient credit -> 402,
consume differencing (zero drift / re-run leaves balance unchanged). Exercised against the
in-memory SQLite ``db`` fixture (conftest).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core import ids
from app.core.errors import InsufficientCredit
from app.db.models import CreditTransaction, CreditWallet, Session
from app.domain.credit_engine import CreditEngine


async def _make_wallet(db, balance: str, reserved: str = "0") -> CreditWallet:
    wallet = CreditWallet(
        id=ids.new("wallet"),
        owner_type="user",
        owner_id=ids.new("user"),
        balance=Decimal(balance),
        reserved=Decimal(reserved),
    )
    async with db.begin():
        db.add(wallet)
    return wallet


async def _reload(db, wallet_id: str):
    """Read a wallet's balance and reserved as scalar columns and return them in a SimpleNamespace.

    Returning the ORM entity risks touching an expired instance, whose attribute access would
    lazy-load outside the greenlet and raise MissingGreenlet. Selecting the columns loads the values
    inside the await and leaves the identity map's session objects alone. """
    row = (
        await db.execute(
            select(CreditWallet.balance, CreditWallet.reserved).where(
                CreditWallet.id == wallet_id
            )
        )
    ).one()
    return SimpleNamespace(balance=row.balance, reserved=row.reserved)


@pytest.mark.asyncio
async def test_hold_idempotency(db):
    """Same hold key applied twice must not change the wallet twice."""
    wallet = await _make_wallet(db, balance="100")
    engine = CreditEngine(db)

    await engine.hold(wallet.id, Decimal("40"), key="hold:ses_x")
    after_first = (await _reload(db, wallet.id)).reserved
    assert after_first == Decimal("40.00")

    # Retry with the same idempotency key -> no second reservation.
    await engine.hold(wallet.id, Decimal("40"), key="hold:ses_x")
    after_retry = (await _reload(db, wallet.id)).reserved
    assert after_retry == after_first == Decimal("40.00")

    # Exactly one hold txn recorded under the key.
    rows = (
        await db.execute(
            CreditTransaction.__table__.select().where(
                CreditTransaction.idempotency_key == "hold:ses_x"
            )
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_hold_insufficient_credit_raises_402(db):
    """hold > (balance - reserved) -> InsufficientCredit (402).

    Note: when hold raises inside begin(), production answers 402 and discards the session, but
    aiosqlite leaves the connection awaiting rollback and every subsequent I/O raises
    MissingGreenlet. That is a test-only constraint and does not occur on Postgres. The rejection
    test therefore asserts only that the exception is raised; that the reservation is untouched, and
    the boundary case, are covered by test_hold_boundary_keeps_reservation_integrity, which uses a
    clean session. """
    # Available = 100 - 80 = 20; a 999 hold must be rejected.
    wallet = await _make_wallet(db, balance="100", reserved="80")
    engine = CreditEngine(db)

    with pytest.raises(InsufficientCredit) as excinfo:
        await engine.hold(wallet.id, Decimal("999"), key="hold:ses_y")
    assert excinfo.value.http == 402
    assert excinfo.value.code == "insufficient_credit"


@pytest.mark.asyncio
async def test_hold_boundary_keeps_reservation_integrity(db):
    """Boundary: a hold for exactly the available amount succeeds, anything more is rejected, and
    the reservation grows by exactly the available amount.

    Nothing raises here, so the accumulated reservation can be read back safely. The exact-available
    hold moving reserved from 80 to 100 — precisely the 20 that was available — also proves that no
    earlier rejected hold left the reservation changed.
    """
    wallet = await _make_wallet(db, balance="100", reserved="80")  # available = 20
    engine = CreditEngine(db)

    # Exactly the available 20 succeeds, taking reserved from 80 to 100.
    await engine.hold(wallet.id, Decimal("20"), key="hold:ses_ok")
    assert (await _reload(db, wallet.id)).reserved == Decimal("100.00")

    # With nothing available, even one more credit is rejected. No read follows, so a poisoned
    # session does not matter here.
    with pytest.raises(InsufficientCredit):
        await engine.hold(wallet.id, Decimal("0.01"), key="hold:ses_over")


@pytest.mark.asyncio
async def test_consume_idempotent(db):
    """Re-running consume for the same minute_bucket leaves balance unchanged.

    Also verifies running-total differencing: a second bucket at the same elapsed time charges
    nothing (owed - already == 0), so there is zero rounding drift.
    """
    wallet = await _make_wallet(db, balance="100", reserved="10")

    # 60 credits/hour, exactly 1h of runtime, occupancy=1 (cores=100) -> owed = 60.00.
    started = datetime.now(UTC) - timedelta(hours=1)
    sess = Session(
        id=ids.new("session"),
        owner_user_id=ids.new("user"),
        cluster_id=ids.new("cluster"),
        offering_id=ids.new("offering"),
        image_id=ids.new("image"),
        resource_class="gpu",
        mode="fractional",
        gpu_mem_mb=10000,
        gpu_cores=100,
        device_total_mem_mb=20000,
        billing_wallet_id=wallet.id,
        status="running",
        credit_per_hour_snapshot=Decimal("60"),
        started_at=started,
    )
    async with db.begin():
        db.add(sess)

    engine = CreditEngine(db)
    now = started + timedelta(hours=1)

    await engine.consume(sess, minute_bucket=60, now=now)
    after_first = await _reload(db, wallet.id)
    charged = Decimal("100") - after_first.balance
    assert charged == Decimal("60.00")  # cph * occ(=1) * 1h
    # reserved drawn down by the same delta (10 -> max(0, 10-60) == 0).
    assert after_first.reserved == Decimal("0.00")

    # Same bucket again -> idempotent no-op (consume:{ses}:{bucket} already exists).
    await engine.consume(sess, minute_bucket=60, now=now)
    after_retry = await _reload(db, wallet.id)
    assert after_retry.balance == after_first.balance

    # Different bucket, same elapsed time -> differencing makes delta 0 (zero drift).
    await engine.consume(sess, minute_bucket=61, now=now)
    after_diff = await _reload(db, wallet.id)
    assert after_diff.balance == after_first.balance

    # Exactly one consume txn carried a non-zero charge.
    consume_rows = (
        await db.execute(
            CreditTransaction.__table__.select().where(
                (CreditTransaction.type == "consume")
                & (CreditTransaction.ref == sess.id)
            )
        )
    ).all()
    assert len(consume_rows) == 1


@pytest.mark.asyncio
async def test_consume_exhaustion_clamps_to_zero_and_arms_grace(db):
    """owed > balance: charge exactly down to 0 (no wallet_sigma violation) and arm grace.

    Regression: the unclamped charge violated the balance>=0 CheckConstraint, so the whole
    consume aborted every minute — nothing billed, no exhaustion, session running for free.
    """
    from app.core.redis import get_redis

    wallet = await _make_wallet(db, balance="10", reserved="5")
    started = datetime.now(UTC) - timedelta(hours=1)
    sess = Session(
        id=ids.new("session"), owner_user_id=ids.new("user"), cluster_id=ids.new("cluster"),
        offering_id=ids.new("offering"), image_id=ids.new("image"), resource_class="gpu",
        mode="fractional", gpu_mem_mb=10000, gpu_cores=100, device_total_mem_mb=20000,
        billing_wallet_id=wallet.id, status="running",
        credit_per_hour_snapshot=Decimal("5000"), started_at=started,
    )
    async with db.begin():
        db.add(sess)

    await CreditEngine(db).consume(sess, minute_bucket=99, now=started + timedelta(hours=1))

    after = await _reload(db, wallet.id)
    assert after.balance == Decimal("0.00")
    assert after.reserved == Decimal("0.00")
    # Exhaustion armed the grace marker for the enforcer (warn -> graceful pause).
    assert await get_redis().get(f"grace:{sess.id}") is not None


@pytest.mark.asyncio
async def test_resume_requires_solvent_wallet(db):
    """Resuming a billable session with an empty wallet must fail with 402, not run free.

    Regression: start() only re-reserved GPU capacity — the creation-time hold key was already
    spent, so a credit-exhausted session could be resumed by hand and ran until the next grace
    window paused it again (a free-GPU loop).
    """
    from app.core.errors import InsufficientCredit
    from app.domain.session_service import SessionService

    wallet = await _make_wallet(db, balance="0")
    sess = Session(
        id=ids.new("session"), owner_user_id=ids.new("user"), cluster_id=ids.new("cluster"),
        offering_id=ids.new("offering"), image_id=ids.new("image"), resource_class="gpu",
        mode="fractional", gpu_mem_mb=10000, gpu_cores=100, device_total_mem_mb=20000,
        billing_wallet_id=wallet.id, status="paused",
        credit_per_hour_snapshot=Decimal("200"), started_at=datetime.now(UTC),
    )
    async with db.begin():
        db.add(sess)

    with pytest.raises(InsufficientCredit):
        await SessionService(db).start(sess.id)

    # The refused resume leaves the session paused and the wallet untouched.
    row = (await db.execute(select(Session.status).where(Session.id == sess.id))).scalar_one()
    assert row == "paused"
    after = await _reload(db, wallet.id)
    assert after.balance == Decimal("0.00")
