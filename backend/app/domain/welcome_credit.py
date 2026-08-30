"""Welcome credit — a per-group fixed grant minted into a member's personal wallet on first join.

Configured per group (Project.default_member_credit, 0 = off). Minting mirrors the monthly
credit_refill semantics (a topup-type transaction, no parent wallet drawdown). Idempotent on
``welcome:{group_id}:{user_id}``: a user re-added to the same group is never paid twice, and a
retried request replays cleanly.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ids
from app.core.logging import get_logger
from app.db.models import CreditTransaction, CreditWallet, Project

log = get_logger(__name__)


async def grant_welcome_credit(db: AsyncSession, user_id: str, group: Project) -> Decimal | None:
    """Mint the group's welcome credit into the user's personal wallet; None when nothing granted.

    Runs inside the caller's transaction (user/membership creation) so the grant commits — or
    rolls back — together with the membership itself.
    """
    amount = Decimal(group.default_member_credit or 0)
    if amount <= 0:
        return None
    key = f"welcome:{group.id}:{user_id}"
    already = await db.scalar(
        select(CreditTransaction.id).where(CreditTransaction.idempotency_key == key)
    )
    if already is not None:
        return None
    wallet = (
        await db.execute(
            select(CreditWallet)
            .where(CreditWallet.owner_type == "user", CreditWallet.owner_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if wallet is None:
        log.warning("welcome credit skipped: user %s has no wallet", user_id)
        return None
    wallet.balance = wallet.balance + amount
    wallet.version = (wallet.version or 0) + 1
    db.add(
        CreditTransaction(
            id=ids.new("txn"),
            wallet_id=wallet.id,
            type="topup",
            amount=amount,
            balance_after=wallet.balance,
            ref=f"welcome:{group.id}",
            idempotency_key=key,
        )
    )
    return amount
