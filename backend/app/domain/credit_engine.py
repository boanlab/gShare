"""CreditEngine — hold / consume / settle / refund.

Invariants:
- per-wallet ``FOR UPDATE`` serialization (READ COMMITTED isolation).
- idempotency keys UNIQUE: ``hold:{ses}``, ``consume:{ses}:{minute_bucket}``, ``settle:{ses}``.
- running-total differencing (zero rounding drift): owed - already_consumed.
- single transaction boundary: wallet update + CreditTransaction INSERT together.
- this plane is the only place credit moves; the operator never touches credit.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InsufficientCredit  # noqa: F401  (raised by hold)
from app.core.ids import new as new_id
from app.db.models import CreditTransaction, CreditWallet

_CENT = Decimal("0.01")
_ZERO = Decimal("0")


def _round2(value: Decimal) -> Decimal:
    """Round to 2 decimal places (credit precision)."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _occupancy(session) -> Decimal:
    """occupancy = max(gpu_mem_mb/total_mem_mb, gpu_cores/100), 0 < occ <= 1.

    Based on the actually-allocated device total_mem_mb (device_total_mem_mb snapshot).
    """
    mem_ratio = _ZERO
    if session.device_total_mem_mb and session.gpu_mem_mb:
        mem_ratio = Decimal(session.gpu_mem_mb) / Decimal(session.device_total_mem_mb)
    core_ratio = Decimal(session.gpu_cores or 0) / Decimal(100)
    occ = max(mem_ratio, core_ratio)
    if occ <= _ZERO:
        occ = Decimal(1)  # defensive: a billed GPU session always occupies something
    return occ


def _active_hours(session, now: datetime) -> Decimal:
    """Active runtime in hours up to ``now`` (minimum billing unit = 1 minute, ceil).

    Paused intervals release the allocation and stop billing; ``billing_worker`` only feeds
    ``running`` sessions, and running-total differencing reconciles any boundary drift. Measured
    from ``started_at`` to ``now``, ceil to whole minutes (>=1 once the session has run at all).
    """
    started = session.started_at
    if started is None:
        return _ZERO
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    elapsed_sec = (now - started).total_seconds()
    if elapsed_sec <= 0:
        return _ZERO
    # minimum 1-minute unit, ceil partial minutes
    minutes = -(-int(elapsed_sec) // 60)  # ceil division
    if minutes < 1:
        minutes = 1
    return Decimal(minutes) / Decimal(60)


class CreditEngine:
    def __init__(self, db: AsyncSession):
        self.db = db
        # session_svc injected lazily on exhaustion (start_grace) to avoid an import cycle
        # with app.domain.session_service.

    def _atomic(self):
        """Begin a transaction, or a nested SAVEPOINT if one is already active. Credit ops must be
        callable both standalone (billing_worker/scheduler: fresh session → top-level begin) and
        nested inside a caller's transaction (status_sync.on_status → savepoint)."""
        return self.db.begin_nested() if self.db.in_transaction() else self.db.begin()

    @asynccontextmanager
    async def _keyed_atomic(self):
        """_atomic, plus protection against an idempotency-key race.

        When concurrent callers insert the same idempotency_key and one hits the unique violation,
        the operation is treated as already applied and the error is swallowed, which makes repeated
        keyed operations safe. The context manager rolls back the savepoint or transaction, so the
        loser's balance change is undone too and nothing is applied twice.

        ONLY the idempotency-key duplicate is swallowed. Any other IntegrityError — most
        importantly the wallet_sigma CHECK (0 <= reserved <= balance) — must surface: reporting a
        constraint violation as success would silently drop a money movement."""
        try:
            async with self._atomic():
                yield
        except IntegrityError as exc:
            detail = str(exc.orig or exc).lower()
            if "idempotency" not in detail and "uq_" not in detail and "unique" not in detail:
                raise

    async def hold(self, wallet_id: str, amount: Decimal, key: str) -> None:
        """Reserve ``amount`` against the wallet (idempotent on ``key``).

        FOR UPDATE the wallet; if balance-reserved < amount -> InsufficientCredit(402).
        Record a hold txn (reserved↑ only).
        """
        amount = _round2(Decimal(amount))
        async with self._keyed_atomic():
            if await self._txn_exists(key):
                return  # idempotent retry
            wallet = await self.db.get(CreditWallet, wallet_id, with_for_update=True)
            if wallet is None:
                raise InsufficientCredit(available=_ZERO, need=amount)
            available = wallet.balance - wallet.reserved
            if available < amount:
                raise InsufficientCredit(available=available, need=amount)
            wallet.reserved += amount
            # hold moves reserved only; record the held amount + session ref so settle can release
            # exactly this session's leftover reservation (not the whole wallet — multi-session
            # safe).
            ref = key[len("hold:"):] if key.startswith("hold:") else None
            await self._record(wallet, type="hold", amount=amount, key=key, ref=ref)

    async def consume(self, session, minute_bucket: int, now: datetime) -> None:
        """Per-minute charge with running-total differencing.

        credit_per_hour_snapshot==0 -> no-op (free). Idempotent on consume:{ses}:{bucket}. occupancy
        = max(mem/total_mem, cores/100); a CPU session is pinned to 1.0 by _occupancy. delta = owed
        - already; if delta<=0 return. On exhaustion (balance-reserved<=0) graceful stop. """
        # Only a zero-rate (free) session skips the ledger. A CPU session with a snapshot above zero
        # is billed proportionally to cpu, mem, and disk like any other.
        if session.credit_per_hour_snapshot == _ZERO:
            return
        if session.billing_wallet_id is None:
            return

        key = f"consume:{session.id}:{minute_bucket}"
        exhausted = False
        async with self._keyed_atomic():
            if await self._txn_exists(key):
                return  # per-minute dedupe (idempotent retry / operator restart)
            wallet = await self.db.get(
                CreditWallet, session.billing_wallet_id, with_for_update=True
            )
            if wallet is None:
                return

            occ = _occupancy(session)
            owed = _round2(
                Decimal(session.credit_per_hour_snapshot) * occ * _active_hours(session, now)
            )
            # Sum only the current run, from started_at onward. Resuming rebases started_at, so a
            # lifetime sum would leave delta <= 0 for as long as the previous run had accumulated —
            # a free window. Scoping to the run closes it.
            already = await self._sum_consumed(session, since=session.started_at)
            delta = owed - already  # running-total differencing (zero drift)
            if delta <= _ZERO:
                return

            # The wallet_sigma constraint forbids balance < 0. A charge larger than what was
            # left used to violate it right here, aborting this session's charge every minute:
            # nothing was billed, exhaustion never fired, and the session ran on for free.
            # Charge what the wallet still has; the grace pipeline handles the shortfall.
            debit = min(delta, wallet.balance)
            if debit > _ZERO:
                wallet.balance -= debit
                # release the matching slice of the reservation as it converts to spend.
                wallet.reserved = max(_ZERO, wallet.reserved - debit)
                await self._record(
                    wallet, type="consume", amount=-debit, ref=session.id, key=key
                )
            # exhaustion: the wallet could not cover the minute, or available hit zero.
            if debit < delta or wallet.balance - wallet.reserved <= _ZERO:
                exhausted = True

        if exhausted:
            await self._on_exhaustion(session)

    async def meter(
        self, wallet_id: str, owed: Decimal, *, ref: str, key: str, type: str = "storage"
    ) -> bool:
        """Idempotent per-tick metered charge for a non-session resource (e.g. retained storage).

        Debits ``owed`` from the wallet's AVAILABLE balance (never below ``reserved``, never below 0
        — the wallet_sigma CheckConstraint). Idempotent on ``key`` (e.g. storage:{vol}:{bucket}), so
        duplicate ticks cannot double-charge. Returns True when fully charged (or no-op), False when
        the wallet is exhausted (under-charged this tick) so the caller can signal/reclaim. Unlike
        session consume there is no running-total differencing — each bucket is an independent
        slice. """
        owed = _round2(Decimal(owed))
        if owed <= _ZERO:
            return True
        async with self._keyed_atomic():
            if await self._txn_exists(key):
                return True  # per-tick dedupe (idempotent retry)
            wallet = await self.db.get(CreditWallet, wallet_id, with_for_update=True)
            if wallet is None:
                return True
            available = wallet.balance - wallet.reserved
            debit = min(owed, available)
            if debit <= _ZERO:
                return False  # broke: no available balance → no ledger row, retried next bucket
            wallet.balance -= debit
            await self._record(wallet, type=type, amount=-debit, ref=ref, key=key)
            return debit >= owed

    async def settle(self, session, key: str) -> None:
        """Finalize remaining consume + release/refund the hold. key=settle:{ses}."""
        if session.billing_wallet_id is None:
            return
        async with self._keyed_atomic():
            if await self._txn_exists(key):
                return  # idempotent
            wallet = await self.db.get(
                CreditWallet, session.billing_wallet_id, with_for_update=True
            )
            if wallet is None:
                return

            # 1 finalize remaining consume (close out the final partial minute) — inline so it
            # shares this single FOR UPDATE / transaction boundary.
            if session.credit_per_hour_snapshot != _ZERO:
                now = datetime.now(UTC)
                occ = _occupancy(session)
                owed = _round2(
                    Decimal(session.credit_per_hour_snapshot) * occ * _active_hours(session, now)
                )
                already = await self._sum_consumed(session)
                delta = owed - already
                if delta > _ZERO:
                    wallet.balance -= delta
                    wallet.reserved = max(_ZERO, wallet.reserved - delta)
                    await self._record(
                        wallet,
                        type="consume",
                        amount=-delta,
                        ref=session.id,
                        key=f"consume:{session.id}:settle",
                    )

            # 2 release/refund the leftover hold (reserved attributable to this session).
            released = await self._release_hold(wallet, session)
            if released > _ZERO:
                await self._record(
                    wallet, type="refund", amount=released, ref=session.id
                )

            # 3 terminal settle marker (zero-amount, idempotency anchor).
            await self._record(wallet, type="settle", amount=_ZERO, ref=session.id, key=key)

    async def _txn_exists(self, key: str) -> bool:
        result = await self.db.execute(
            select(CreditTransaction.id).where(CreditTransaction.idempotency_key == key).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _sum_consumed(self, session, since: datetime | None = None) -> Decimal:
        """Sum of consume amounts for this session (ledger reconstruction).

        ``consume`` rows carry a negative ``amount``; return the positive total already charged.
        Given ``since`` — normally session.started_at — only rows after it are summed, limiting the
        result to the *current run* and preventing a free window after a resume. Without it the sum is
        over the session's lifetime, which is what release and settlement need.
        """
        conds = [
            CreditTransaction.wallet_id == session.billing_wallet_id,
            CreditTransaction.type == "consume",
            CreditTransaction.ref == session.id,
        ]
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
            conds.append(CreditTransaction.created_at >= since)
        result = await self.db.execute(
            select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(*conds)
        )
        total = result.scalar_one() or _ZERO
        return _round2(-Decimal(total))  # stored negative -> positive owed-so-far

    async def _sum_held(self, session) -> Decimal:
        """Total credit held (reserved) for this session across its lifetime."""
        result = await self.db.execute(
            select(func.coalesce(func.sum(CreditTransaction.amount), 0)).where(
                CreditTransaction.wallet_id == session.billing_wallet_id,
                CreditTransaction.type == "hold",
                CreditTransaction.ref == session.id,
            )
        )
        return _round2(Decimal(result.scalar_one() or _ZERO))

    async def _release_hold(self, wallet, session) -> Decimal:
        """Release *this session's* leftover reservation at settle.

        A hold raises reserved and each minute's consume lowers it by delta, so this session's
        outstanding reservation is its total holds minus its lifetime consumption. Releasing only
        that slice — never the wallet's whole reserved balance — is what keeps concurrent sessions
        on the same wallet from clobbering each other's reservations. The result is clamped to [0,
        wallet.reserved] to preserve the sum invariant. """
        held = await self._sum_held(session)
        consumed = await self._sum_consumed(session)  # lifetime
        leftover = held - consumed
        if leftover < _ZERO:
            leftover = _ZERO
        released = min(leftover, wallet.reserved) if wallet.reserved > _ZERO else _ZERO
        wallet.reserved -= released
        return _round2(released)

    async def _on_exhaustion(self, session) -> None:
        """Hook: available balance hit zero -> begin grace (graceful stop).

        Lazy import of SessionService avoids a module-level import cycle.
        """
        from app.domain.session_service import SessionService

        await SessionService(self.db).start_grace(session)

    async def _record(self, wallet, type: str, amount: Decimal, *, key: str | None = None,
                      ref: str | None = None) -> None:
        """INSERT a CreditTransaction with balance_after snapshot, in the same tx."""
        amount = _round2(Decimal(amount))
        # balance_after = available balance snapshot after applying this txn.
        balance_after = _round2(wallet.balance - wallet.reserved)
        txn = CreditTransaction(
            id=new_id("transaction"),
            wallet_id=wallet.id,
            type=type,
            amount=amount,
            balance_after=balance_after,
            ref=ref,
            idempotency_key=key if key is not None else f"{type}:{wallet.id}:{new_id('transaction')}",
        )
        self.db.add(txn)
        await self.db.flush()
