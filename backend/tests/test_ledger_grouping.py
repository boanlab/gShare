"""Wallet ledger grouping: a session's per-minute consume rows fold into one line, while discrete
events (topup, settle) stay separate."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.api.credits_router import _grouped_transactions
from app.api.deps import Pagination
from app.core import ids
from app.db.models import CreditTransaction, CreditWallet, Image, Offering
from app.db.models import Session as SessionRow


@pytest.mark.asyncio
async def test_consume_rows_fold_per_session(db):
    user_id = ids.new("user")
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=user_id,
                          balance=Decimal("900"), reserved=Decimal("0"))
    offering = Offering(id=ids.new("offering"), name="o", resource_class="gpu",
                        gpu_model="A100", credit_per_hour=Decimal("100"))
    image = Image(id=ids.new("image"), name="img")
    sess = SessionRow(id=ids.new("session"), owner_user_id=user_id, cluster_id=ids.new("cluster"),
                      offering_id=offering.id, image_id=image.id, resource_class="gpu",
                      mode="fractional", status="running", name="gpu-lab-01")
    base = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    rows = [
        CreditTransaction(id=ids.new("txn"), wallet_id=wallet.id, type="topup",
                          amount=Decimal("1000"), balance_after=Decimal("1000"),
                          ref=None, idempotency_key="t0", created_at=base),
    ]
    # ten minutes of billing on one session
    for i in range(10):
        rows.append(CreditTransaction(
            id=ids.new("txn"), wallet_id=wallet.id, type="consume",
            amount=Decimal("-1.67"), balance_after=Decimal("1000") - Decimal("1.67") * (i + 1),
            ref=sess.id, idempotency_key=f"c{i}", created_at=base + timedelta(minutes=i + 1),
        ))
    async with db.begin():
        db.add_all([wallet, offering, image, sess, *rows])

    out = await _grouped_transactions(db, wallet.id, Pagination(page=1, size=50))
    assert len(out) == 2, [r.type for r in out]           # one rollup + the topup

    rollup = next(r for r in out if r.type == "consume")
    assert rollup.live is True                    # session is running: still accruing
    assert rollup.entry_count == 10
    assert rollup.amount == Decimal("-16.70")
    assert rollup.ref_name == "gpu-lab-01"
    # SQLite drops tzinfo on read where Postgres keeps it, so compare the wall-clock values.
    def naive(d):
        return d.replace(tzinfo=None)
    assert naive(rollup.period_start) == naive(base + timedelta(minutes=1))
    assert naive(rollup.period_end) == naive(base + timedelta(minutes=10))

    topup = next(r for r in out if r.type == "topup")
    assert topup.entry_count == 1 and topup.amount == Decimal("1000")


@pytest.mark.asyncio
async def test_two_sessions_stay_separate(db):
    user_id = ids.new("user")
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=user_id,
                          balance=Decimal("100"), reserved=Decimal("0"))
    offering = Offering(id=ids.new("offering"), name="o", resource_class="gpu",
                        gpu_model="A100", credit_per_hour=Decimal("100"))
    image = Image(id=ids.new("image"), name="img")
    base = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    sessions, txns = [], []
    for n in range(2):
        sess = SessionRow(id=ids.new("session"), owner_user_id=user_id, cluster_id="c",
                          offering_id=offering.id, image_id=image.id, resource_class="gpu",
                          mode="fractional", status="running", name=f"s{n}")
        sessions.append(sess)
        for i in range(3):
            txns.append(CreditTransaction(
                id=ids.new("txn"), wallet_id=wallet.id, type="consume", amount=Decimal("-1"),
                balance_after=Decimal("99"), ref=sess.id, idempotency_key=f"c{n}-{i}",
                created_at=base + timedelta(minutes=i),
            ))
    async with db.begin():
        db.add_all([wallet, offering, image, *sessions, *txns])

    out = await _grouped_transactions(db, wallet.id, Pagination(page=1, size=50))
    assert len(out) == 2
    assert {r.ref_name for r in out} == {"s0", "s1"}
    assert all(r.entry_count == 3 and r.amount == Decimal("-3") for r in out)


@pytest.mark.asyncio
async def test_settle_marker_folds_into_the_refund_row(db):
    """settle moves no money; it should mark the session's refund line, not occupy its own."""
    user_id = ids.new("user")
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=user_id,
                          balance=Decimal("100"), reserved=Decimal("0"))
    offering = Offering(id=ids.new("offering"), name="o", resource_class="gpu",
                        gpu_model="A100", credit_per_hour=Decimal("100"))
    image = Image(id=ids.new("image"), name="img")
    sess = SessionRow(id=ids.new("session"), owner_user_id=user_id, cluster_id="c",
                      offering_id=offering.id, image_id=image.id, resource_class="gpu",
                      mode="fractional", status="terminated", name="done-01")
    base = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
    txns = [
        CreditTransaction(id=ids.new("txn"), wallet_id=wallet.id, type="hold", amount=Decimal("100"),
                          balance_after=Decimal("100"), ref=sess.id, idempotency_key="h",
                          created_at=base),
        CreditTransaction(id=ids.new("txn"), wallet_id=wallet.id, type="consume", amount=Decimal("-5"),
                          balance_after=Decimal("95"), ref=sess.id, idempotency_key="c",
                          created_at=base + timedelta(minutes=1)),
        CreditTransaction(id=ids.new("txn"), wallet_id=wallet.id, type="refund", amount=Decimal("95"),
                          balance_after=Decimal("95"), ref=sess.id, idempotency_key="r",
                          created_at=base + timedelta(minutes=2)),
        CreditTransaction(id=ids.new("txn"), wallet_id=wallet.id, type="settle", amount=Decimal("0"),
                          balance_after=Decimal("95"), ref=sess.id, idempotency_key="s",
                          created_at=base + timedelta(minutes=2)),
    ]
    async with db.begin():
        db.add_all([wallet, offering, image, sess, *txns])

    out = await _grouped_transactions(db, wallet.id, Pagination(page=1, size=50))
    assert "settle" not in {r.type for r in out}          # no standalone zero-amount line
    refund = next(r for r in out if r.type == "refund")
    assert refund.settled is True
    # the flag lands once, on the refund only
    assert [r.settled for r in out if r.type in ("consume", "hold")] == [False, False]


@pytest.mark.asyncio
async def test_storage_rows_fold_per_volume(db):
    """Per-minute storage billing folds into one row per volume, like session consume."""
    from app.db.models import StorageVolume

    user_id = ids.new("user")
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=user_id,
                          balance=Decimal("100"), reserved=Decimal("0"))
    vol = StorageVolume(id=ids.new("volume"), scope="user", scope_id=user_id, type="home",
                        name="home-data", access_mode="RWO", quota_gb=100, used_gb=0)
    base = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    rows = []
    for i in range(6):
        rows.append(CreditTransaction(
            id=ids.new("txn"), wallet_id=wallet.id, type="storage",
            amount=Decimal("-0.02"), balance_after=Decimal("100") - Decimal("0.02") * (i + 1),
            ref=vol.id, idempotency_key=f"s{i}", created_at=base + timedelta(minutes=i),
        ))
    async with db.begin():
        db.add_all([wallet, vol, *rows])

    out = await _grouped_transactions(db, wallet.id, Pagination(page=1, size=50))
    assert len(out) == 1, [r.type for r in out]
    g = out[0]
    assert g.type == "storage" and g.entry_count == 6
    assert g.live is True                         # volume still exists: bills until deleted
    assert g.amount == Decimal("-0.12")
    assert g.ref_name == "home-data"
    assert g.balance_after == Decimal("100") - Decimal("0.12")


@pytest.mark.asyncio
async def test_closed_streams_are_not_live(db):
    """A terminated session's rollup and a deleted volume's rollup do not show as billing."""
    from datetime import UTC as _UTC

    from app.db.models import StorageVolume

    user_id = ids.new("user")
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=user_id,
                          balance=Decimal("100"), reserved=Decimal("0"))
    offering = Offering(id=ids.new("offering"), name="o", resource_class="gpu",
                        gpu_model="A100", credit_per_hour=Decimal("100"))
    image = Image(id=ids.new("image"), name="img")
    sess = SessionRow(id=ids.new("session"), owner_user_id=user_id, cluster_id=ids.new("cluster"),
                      offering_id=offering.id, image_id=image.id, resource_class="gpu",
                      mode="fractional", status="terminated", name="done")
    vol = StorageVolume(id=ids.new("volume"), scope="user", scope_id=user_id, type="home",
                        name="gone", access_mode="RWO", quota_gb=10, used_gb=0,
                        deleted_at=datetime(2026, 8, 27, 13, 0, tzinfo=_UTC))
    base = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    rows = []
    for i in range(2):
        rows.append(CreditTransaction(
            id=ids.new("txn"), wallet_id=wallet.id, type="consume", amount=Decimal("-1"),
            balance_after=Decimal("99") - i, ref=sess.id, idempotency_key=f"c{i}",
            created_at=base + timedelta(minutes=i)))
        rows.append(CreditTransaction(
            id=ids.new("txn"), wallet_id=wallet.id, type="storage", amount=Decimal("-0.02"),
            balance_after=Decimal("98"), ref=vol.id, idempotency_key=f"s{i}",
            created_at=base + timedelta(minutes=i)))
    async with db.begin():
        db.add_all([wallet, offering, image, sess, vol, *rows])

    out = await _grouped_transactions(db, wallet.id, Pagination(page=1, size=50))
    assert {r.type: r.live for r in out} == {"consume": False, "storage": False}
