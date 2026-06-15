"""Credits router.

wallets / topup / adjust / transfer / transactions / holds / topup-requests. All money mutations go
through a single DB transaction with a per-wallet ``SELECT... FOR UPDATE`` and an idempotent
``CreditTransaction`` (idempotency_key UNIQUE); errors via envelope (402 insufficient_credit). """
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal, idempotency_key, require_idem
from app.api.schemas.credit import (
    AdjustBody,
    AllocateBody,
    AllocationEscalateBody,
    AllocationRejectBody,
    AllocationRequestCreate,
    MonthlyGrantBody,
    TopupRejectBody,
    TopupRequestBody,
    TopupRequestListResponse,
    TransactionRead,
    TransferBody,
    WalletRead,
)
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import DomainError, Forbidden, InsufficientCredit, NotFound


class _Validation(DomainError):
    code, http = "validation_failed", 422


class _InsufficientPool(DomainError):
    # The parent pool (a group or organization wallet) is short, so the caller should escalate.
    # Kept distinct from 402, which means the personal balance is short.
    code, http = "insufficient_pool", 409
from app.db.base import get_db
from app.db.models import (
    CreditAllocationRequest,
    CreditTransaction,
    CreditWallet,
    Membership,
    Notification,
    Organization,
    Project,
    TopupRequest,
    User,
)
from app.domain.audit_service import AuditService

router = APIRouter(prefix="/credits", tags=["credits"])


# ── helpers ──────────────────────────────────────────────────────────────────
async def _get_wallet(db: AsyncSession, wallet_id: str) -> CreditWallet:
    wallet = await db.get(CreditWallet, wallet_id)
    if wallet is None:
        raise NotFound("wallet not found")
    return wallet


async def _lock_wallet(db: AsyncSession, wallet_id: str) -> CreditWallet:
    """Per-wallet SELECT... FOR UPDATE serialization."""
    wallet = (
        await db.scalars(
            select(CreditWallet).where(CreditWallet.id == wallet_id).with_for_update()
        )
    ).one_or_none()
    if wallet is None:
        raise NotFound("wallet not found")
    return wallet


async def _existing_txn(db: AsyncSession, key: str) -> CreditTransaction | None:
    """Idempotency: a txn for ``key`` already committed -> replay it."""
    return (
        await db.scalars(
            select(CreditTransaction).where(CreditTransaction.idempotency_key == key)
        )
    ).one_or_none()


def _can_read_wallet(principal: Principal, wallet: CreditWallet) -> bool:
    """Owner (self / project member) or billing/super admin."""
    if principal.global_role == "super_admin":
        return True
    if wallet.owner_type == "user" and wallet.owner_id == principal.user_id:
        return True
    if wallet.owner_type == "group" and wallet.owner_id in principal.memberships:
        return True
    if wallet.owner_type == "org" and wallet.owner_id in principal.org_admin_orgs:
        return True
    return False


def _wallet_view(wallet: CreditWallet) -> WalletRead:
    return WalletRead.model_validate(wallet)


# ── wallet reads ────────────────────────────────────────
@router.get("/wallets", response_model=list[WalletRead])
async def list_wallets(
    page: Pagination = Depends(),
    owner_type: str | None = Query(default=None),
    owner_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="wallet.read")
    stmt = select(CreditWallet)
    if owner_type is not None:
        stmt = stmt.where(CreditWallet.owner_type == owner_type)
    if owner_id is not None:
        stmt = stmt.where(CreditWallet.owner_id == owner_id)
    stmt = stmt.order_by(CreditWallet.created_at.desc()).limit(page.size).offset(page.offset)
    rows = (await db.scalars(stmt)).all()
    # Resolve owner names per type (user, group, organization) so the UI can show a name, not an id.
    uids = {w.owner_id for w in rows if w.owner_type == "user"}
    gids = {w.owner_id for w in rows if w.owner_type == "group"}
    oids = {w.owner_id for w in rows if w.owner_type == "org"}
    un = {u: n for u, n in (await db.execute(select(User.id, User.name).where(User.id.in_(uids)))).all()} if uids else {}
    gn = {g: n for g, n in (await db.execute(select(Project.id, Project.name).where(Project.id.in_(gids)))).all()} if gids else {}
    on = {o: n for o, n in (await db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(oids)))).all()} if oids else {}

    def _own(w: CreditWallet) -> str | None:
        return ({"user": un, "group": gn, "org": on}.get(w.owner_type) or {}).get(w.owner_id)

    out = []
    for w in rows:
        wv = _wallet_view(w)
        wv.owner_name = _own(w)
        out.append(wv)
    return out


@router.get("/wallets/me", response_model=WalletRead)
async def my_wallet(
    group_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    if group_id is not None:
        # Project wallet requires membership.
        principal.require(action="wallet.read", group_id=group_id)
        owner_type, owner_id = "group", group_id
    else:
        owner_type, owner_id = "user", principal.user_id
    wallet = (
        await db.scalars(
            select(CreditWallet).where(
                CreditWallet.owner_type == owner_type,
                CreditWallet.owner_id == owner_id,
            )
        )
    ).one_or_none()
    if wallet is None:
        raise NotFound("wallet not found")
    return _wallet_view(wallet)


@router.get("/wallets/{wallet_id}", response_model=WalletRead)
async def get_wallet(
    wallet_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    wallet = await _get_wallet(db, wallet_id)
    if not _can_read_wallet(principal, wallet):
        raise Forbidden("not permitted: wallet.read")
    return _wallet_view(wallet)


# ── money mutations: single tx + FOR UPDATE + idempotent txn ─────
@router.post("/wallets/{wallet_id}/topup", status_code=status.HTTP_201_CREATED)
async def topup(
    wallet_id: str,
    body: TopupRequestBody,
    principal: Principal = Depends(get_current_principal),
    idem: str | None = Depends(idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    require_idem(idem)
    principal.require(action="credit.topup")
    assert idem is not None

    replay = await _existing_txn(db, idem)
    if replay is not None:
        wallet = await _get_wallet(db, wallet_id)
        return {"transaction": _txn_view(replay), "wallet": _wallet_view(wallet)}

    wallet = await _lock_wallet(db, wallet_id)
    amount = Decimal(body.amount)
    wallet.balance = wallet.balance + amount
    wallet.version = wallet.version + 1
    txn = _make_txn(wallet, "topup", amount, key=idem, ref=body.note)
    db.add(txn)
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="credit.topup", target=wallet_id,
        amount=str(amount), txn_id=txn.id,
    )
    await db.commit()
    return {"transaction": _txn_view(txn), "wallet": _wallet_view(wallet)}


@router.post("/wallets/{wallet_id}/adjust", status_code=status.HTTP_201_CREATED)
async def adjust(
    wallet_id: str,
    body: AdjustBody,
    principal: Principal = Depends(get_current_principal),
    idem: str | None = Depends(idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    require_idem(idem)
    principal.require(action="credit.adjust")
    assert idem is not None

    replay = await _existing_txn(db, idem)
    if replay is not None:
        wallet = await _get_wallet(db, wallet_id)
        return {"transaction": _txn_view(replay), "wallet": _wallet_view(wallet)}

    wallet = await _lock_wallet(db, wallet_id)
    amount = Decimal(body.amount)  # signed
    new_balance = wallet.balance + amount
    # Σ-invariant: balance must stay >= reserved >= 0 -> 422 if negative result.
    if new_balance < Decimal(0) or new_balance < wallet.reserved:
        from app.core.errors import DomainError

        class _Unprocessable(DomainError):
            code, http = "validation_failed", 422

        raise _Unprocessable(
            "adjust would violate wallet invariant",
            {"balance": str(wallet.balance), "reserved": str(wallet.reserved),
             "amount": str(amount)},
        )
    wallet.balance = new_balance
    wallet.version = wallet.version + 1
    txn = _make_txn(wallet, "adjust", amount, key=idem, ref=body.reason)
    db.add(txn)
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="credit.adjust", target=wallet_id,
        amount=str(amount), reason=body.reason, txn_id=txn.id,
    )
    await db.commit()
    return {"transaction": _txn_view(txn), "wallet": _wallet_view(wallet)}


@router.post("/wallets/{wallet_id}/transfer", status_code=status.HTTP_201_CREATED)
async def transfer(
    wallet_id: str,
    body: TransferBody,
    principal: Principal = Depends(get_current_principal),
    idem: str | None = Depends(idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    require_idem(idem)
    principal.require(action="credit.transfer")
    assert idem is not None

    if body.to_wallet_id == wallet_id:
        from app.core.errors import DomainError

        class _Unprocessable(DomainError):
            code, http = "validation_failed", 422

        raise _Unprocessable("source and destination wallets must differ")

    out_key = f"transfer:{idem}:out"
    in_key = f"transfer:{idem}:in"
    replay = await _existing_txn(db, out_key)
    if replay is not None:
        src = await _get_wallet(db, wallet_id)
        dst = await _get_wallet(db, body.to_wallet_id)
        return {
            "from": {"wallet_id": src.id, "balance_after": str(src.balance)},
            "to": {"wallet_id": dst.id, "balance_after": str(dst.balance)},
        }

    # Lock both wallets in ascending id order to avoid deadlock.
    first_id, second_id = sorted((wallet_id, body.to_wallet_id))
    first = await _lock_wallet(db, first_id)
    second = await _lock_wallet(db, second_id)
    src = first if first.id == wallet_id else second
    dst = first if first.id == body.to_wallet_id else second

    # Same hierarchy check as allocate: both wallets must be within the caller's scope, which is
    # what stops funds moving between tenants.
    await _assert_can_allocate(db, principal, src, dst)

    amount = Decimal(body.amount)
    available = src.balance - src.reserved
    if available < amount:
        raise InsufficientCredit(available=available, need=amount)

    src.balance = src.balance - amount
    src.version = src.version + 1
    dst.balance = dst.balance + amount
    dst.version = dst.version + 1

    out_txn = _make_txn(src, "adjust", -amount, key=out_key, ref=f"transfer-out:{dst.id}")
    in_txn = _make_txn(dst, "adjust", amount, key=in_key, ref=f"transfer-in:{src.id}")
    db.add_all([out_txn, in_txn])
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="credit.transfer", target=src.id,
        to_wallet=dst.id, amount=str(amount), reason=getattr(body, "reason", None),
    )
    await db.commit()
    return {
        "from": {"wallet_id": src.id, "transaction_id": out_txn.id,
                 "balance_after": str(src.balance)},
        "to": {"wallet_id": dst.id, "transaction_id": in_txn.id,
               "balance_after": str(dst.balance)},
    }


async def _assert_can_allocate(
    db: AsyncSession, principal: Principal, src: CreditWallet, dst: CreditWallet
) -> None:
    """Authorize a hierarchical allocation or reclaim.

    super_admin may move anything. An org_admin may move between their organization's wallet and the
    wallets of that organization's projects; a group_admin between their project's wallet and the
    wallets of that project's members. Direction does not matter — the same rule covers allocating
    down and reclaiming up."""
    if principal.global_role == "super_admin":
        return
    pair = {src.owner_type, dst.owner_type}
    if pair == {"org", "group"}:
        org_w = src if src.owner_type == "org" else dst
        prj_w = dst if dst.owner_type == "group" else src
        if org_w.owner_id not in principal.org_admin_orgs:
            raise Forbidden("not org_admin of this organization")
        prj = await db.get(Project, prj_w.owner_id)
        if prj is None or prj.org_id != org_w.owner_id:
            raise Forbidden("project does not belong to the organization")
        return
    if pair == {"group", "user"}:
        prj_w = src if src.owner_type == "group" else dst
        usr_w = dst if dst.owner_type == "user" else src
        role = principal.memberships.get(prj_w.owner_id)
        if role not in ("group_admin", "org_admin"):
            raise Forbidden("not group_admin of this project")
        member = await db.scalar(
            select(Membership.id).where(
                Membership.group_id == prj_w.owner_id,
                Membership.user_id == usr_w.owner_id,
            )
        )
        if member is None:
            raise Forbidden("target user is not a member of the project")
        return
    raise Forbidden("allocation must be between org<->project or project<->user")


@router.get("/allocation-scope")
async def allocation_scope(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Return the pools the caller can allocate from, and each pool's targets, with names.

    An org_admin gets their organization's wallet, whose children are that organization's groups; a
    group_admin gets their group's wallet, whose children are its members; a super_admin gets
    everything. The UI uses this to pick a pool, populate its targets, and toggle direction between
    allocate and reclaim.
    """
    su = principal.global_role == "super_admin"
    pools: list[dict] = []
    children: dict[str, list] = {}

    # ── Organization pools: an organization wallet with its group wallets as children ──
    if su:
        org_ids = [o.id for o in (await db.scalars(
            select(Organization).where(Organization.deleted_at.is_(None)))).all()]
    else:
        org_ids = list(principal.org_admin_orgs)
    if org_ids:
        org_names = {o.id: o.name for o in (await db.scalars(
            select(Organization).where(Organization.id.in_(org_ids)))).all()}
        org_w = {w.owner_id: w for w in (await db.scalars(select(CreditWallet).where(
            CreditWallet.owner_type == "org", CreditWallet.owner_id.in_(org_ids)))).all()}
        projs = (await db.scalars(select(Project).where(
            Project.org_id.in_(org_ids), Project.deleted_at.is_(None)))).all()
        proj_ids = [p.id for p in projs]
        proj_w = {w.owner_id: w for w in (await db.scalars(select(CreditWallet).where(
            CreditWallet.owner_type == "group", CreditWallet.owner_id.in_(proj_ids)))).all()} if proj_ids else {}
        by_org: dict[str, list] = {}
        for p in projs:
            by_org.setdefault(p.org_id, []).append(p)
        for oid in org_ids:
            ow = org_w.get(oid)
            if ow is None:
                continue
            pools.append({"wallet_id": ow.id, "balance": str(ow.balance), "monthly_grant": str(ow.monthly_grant), "scope": "org", "name": org_names.get(oid, oid)})
            children[ow.id] = [
                {"wallet_id": proj_w[p.id].id, "balance": str(proj_w[p.id].balance), "monthly_grant": str(proj_w[p.id].monthly_grant), "scope": "group", "name": p.name}
                for p in by_org.get(oid, []) if p.id in proj_w
            ]

    # ── Project pools: a group wallet with its members' wallets as children ──
    if su:
        my_pids = [p.id for p in (await db.scalars(select(Project).where(Project.deleted_at.is_(None)))).all()]
    else:
        my_pids = [pid for pid, role in principal.memberships.items() if role in ("group_admin", "org_admin")]
    if my_pids:
        pname = {p.id: p.name for p in (await db.scalars(select(Project).where(Project.id.in_(my_pids)))).all()}
        pw = {w.owner_id: w for w in (await db.scalars(select(CreditWallet).where(
            CreditWallet.owner_type == "group", CreditWallet.owner_id.in_(my_pids)))).all()}
        memrows = (await db.execute(
            select(Membership.group_id, Membership.user_id, User.name, User.email)
            .join(User, User.id == Membership.user_id)
            .where(Membership.group_id.in_(my_pids))
        )).all()
        member_uids = {uid for _, uid, _, _ in memrows}
        uw = {w.owner_id: w for w in (await db.scalars(select(CreditWallet).where(
            CreditWallet.owner_type == "user", CreditWallet.owner_id.in_(member_uids)))).all()} if member_uids else {}
        by_proj: dict[str, list] = {}
        for pid, uid, uname, uemail in memrows:
            by_proj.setdefault(pid, []).append((uid, uname or uemail))
        for pid in my_pids:
            wal = pw.get(pid)
            if wal is None or wal.id in children:  # skip wallets already registered as a pool
                if wal is None:
                    continue
            pools.append({"wallet_id": wal.id, "balance": str(wal.balance), "monthly_grant": str(wal.monthly_grant), "scope": "group", "name": pname.get(pid, pid)})
            children[wal.id] = [
                {"wallet_id": uw[uid].id, "balance": str(uw[uid].balance), "monthly_grant": str(uw[uid].monthly_grant), "scope": "user", "name": label}
                for uid, label in by_proj.get(pid, []) if uid in uw
            ]

    # super_admin also sees the system monthly ceiling — the cap on the sum of organization refills
    # — and the system balance.
    system = None
    if su:
        sysw = await db.scalar(select(CreditWallet).where(CreditWallet.owner_type == "system"))
        if sysw is not None:
            org_grant_sum = await db.scalar(
                select(func.coalesce(func.sum(CreditWallet.monthly_grant), 0)).where(
                    CreditWallet.owner_type == "org")
            ) or Decimal(0)
            system = {
                "wallet_id": sysw.id,
                "balance": str(sysw.balance),
                "monthly_total": str(sysw.monthly_grant),         # monthly ceiling
                "org_grant_sum": str(org_grant_sum),              # total already assigned to organizations
                "remaining": str(Decimal(sysw.monthly_grant) - Decimal(org_grant_sum)),
            }

    return {"pools": pools, "children": children, "system": system}


@router.post("/allocate", status_code=status.HTTP_201_CREATED)
async def allocate(
    body: AllocateBody,
    principal: Principal = Depends(get_current_principal),
    idem: str | None = Depends(idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    """Allocate credits down the hierarchy (organization -> project -> user), or reclaim upwards.
    An idempotency key is required.

    "Within your allowance" is enforced automatically by the source wallet's available balance, and
    exceeding it returns 402. Authorization is handled by _assert_can_allocate.
    """
    require_idem(idem)
    assert idem is not None
    if body.from_wallet_id == body.to_wallet_id:
        raise _Validation("source and destination wallets must differ")

    out_key = f"allocate:{idem}:out"
    in_key = f"allocate:{idem}:in"
    replay = await _existing_txn(db, out_key)
    if replay is not None:
        src = await _get_wallet(db, body.from_wallet_id)
        dst = await _get_wallet(db, body.to_wallet_id)
        return {
            "from": {"wallet_id": src.id, "balance_after": str(src.balance)},
            "to": {"wallet_id": dst.id, "balance_after": str(dst.balance)},
        }

    first_id, second_id = sorted((body.from_wallet_id, body.to_wallet_id))
    first = await _lock_wallet(db, first_id)
    second = await _lock_wallet(db, second_id)
    src = first if first.id == body.from_wallet_id else second
    dst = first if first.id == body.to_wallet_id else second

    await _assert_can_allocate(db, principal, src, dst)

    amount = body.amount
    available = src.balance - src.reserved
    if available < amount:
        raise InsufficientCredit(available=available, need=amount)

    src.balance = src.balance - amount
    src.version = src.version + 1
    dst.balance = dst.balance + amount
    dst.version = dst.version + 1
    out_txn = _make_txn(src, "adjust", -amount, key=out_key, ref=f"allocate-out:{dst.id}")
    in_txn = _make_txn(dst, "adjust", amount, key=in_key, ref=f"allocate-in:{src.id}")
    db.add_all([out_txn, in_txn])
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="credit.allocate", target=src.id,
        to_wallet=dst.id, amount=str(amount), reason=body.reason,
    )
    await db.commit()
    return {
        "from": {"wallet_id": src.id, "transaction_id": out_txn.id, "balance_after": str(src.balance)},
        "to": {"wallet_id": dst.id, "transaction_id": in_txn.id, "balance_after": str(dst.balance)},
    }


async def _grant_scope(
    db: AsyncSession, principal: Principal, wallet: CreditWallet
) -> tuple[CreditWallet | None, list[str]]:
    """Authorize setting a wallet's monthly_grant, and return its parent pool and sibling wallet
    ids.

    By level: an organization grant is set by a super_admin and has no parent and therefore no
    ceiling; a project grant by an org_admin, with the organization wallet as parent; a user grant
    by a group_admin, with that group's wallet as parent. Whenever there is a parent, the sum of the
    siblings' grants must stay within the parent's grant."""
    su = principal.global_role == "super_admin"
    if wallet.owner_type == "system":
        if not su:
            raise Forbidden("only super_admin sets the system monthly total")
        return None, []   # top of the hierarchy: no parent, no ceiling — this is the ceiling
    if wallet.owner_type == "org":
        if not su:
            raise Forbidden("only super_admin sets an organization's monthly grant")
        # The parent is the system ceiling: organization refills must sum within the monthly total.
        sysw = await _wallet_of(db, "system", "system")
        org_ids = (await db.scalars(
            select(CreditWallet.id).where(CreditWallet.owner_type == "org"))).all()
        return sysw, list(org_ids)
    if wallet.owner_type == "group":
        prj = await db.get(Project, wallet.owner_id)
        if prj is None:
            raise NotFound("project not found")
        if not su and prj.org_id not in principal.org_admin_orgs:
            raise Forbidden("not org_admin of this organization")
        org_w = await _wallet_of(db, "org", prj.org_id)
        sib_pids = (await db.scalars(
            select(Project.id).where(Project.org_id == prj.org_id, Project.deleted_at.is_(None)))).all()
        sibs = (await db.scalars(select(CreditWallet.id).where(
            CreditWallet.owner_type == "group", CreditWallet.owner_id.in_(sib_pids)))).all()
        return org_w, list(sibs)
    if wallet.owner_type == "user":
        q = select(Membership.group_id).where(Membership.user_id == wallet.owner_id)
        if not su:
            admin_pids = [pid for pid, role in principal.memberships.items()
                          if role in ("group_admin", "org_admin")]
            if not admin_pids:
                raise Forbidden("not a group_admin for this user")
            q = q.where(Membership.group_id.in_(admin_pids))
        pids = (await db.scalars(q)).all()
        if not pids:
            raise Forbidden("target user is not in a project you administer")
        pid = pids[0]                                   # funding pool = the administered group; the first one if several
        prj_w = await _wallet_of(db, "group", pid)
        sib_uids = (await db.scalars(
            select(Membership.user_id).where(Membership.group_id == pid))).all()
        sibs = (await db.scalars(select(CreditWallet.id).where(
            CreditWallet.owner_type == "user", CreditWallet.owner_id.in_(sib_uids)))).all()
        return prj_w, list(sibs)
    raise Forbidden("unsupported wallet type for monthly grant")


@router.post("/wallets/{wallet_id}/monthly-grant", response_model=WalletRead)
async def set_monthly_grant(
    wallet_id: str,
    body: MonthlyGrantBody,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Set a child wallet's monthly automatic refill, as the administrator one level up.

    The siblings' grants must sum within the parent's grant; 0 disables refills for that wallet. The
    refill itself — resetting balance to grant at the start of each month, use-it-or-lose-it — is
    performed by the credit_refill worker."""
    wallet = await _lock_wallet(db, wallet_id)
    parent, sibling_ids = await _grant_scope(db, principal, wallet)
    new_amount = body.amount
    if parent is not None:
        others = await db.scalar(
            select(func.coalesce(func.sum(CreditWallet.monthly_grant), 0)).where(
                CreditWallet.id.in_(sibling_ids), CreditWallet.id != wallet_id)
        ) or Decimal(0)
        if Decimal(others) + new_amount > parent.monthly_grant:
            raise _Validation(
                "monthly grants exceed parent pool",
                {"parent_grant": str(parent.monthly_grant),
                 "siblings_total": str(others), "requested": str(new_amount)},
            )
    old_grant = wallet.monthly_grant
    wallet.monthly_grant = new_amount
    # An increase is credited immediately so it is usable at once; a decrease takes effect at the
    # next monthly refill. The system wallet only holds the ceiling and needs no balance.
    if wallet.owner_type != "system" and new_amount > wallet.balance:
        wallet.balance = new_amount
    wallet.version = wallet.version + 1
    await AuditService(db).record(
        actor=principal.user_id, action="credit.set_monthly_grant",
        target=wallet.id, result="ok",
        changes={"monthly_grant": {"from": str(old_grant), "to": str(new_amount)}},
    )
    await db.commit()
    return _wallet_view(wallet)


# ── Hierarchical credit allocation requests and escalation ─────────────────────────
async def _wallet_of(db: AsyncSession, owner_type: str, owner_id: str) -> CreditWallet | None:
    return await db.scalar(
        select(CreditWallet).where(
            CreditWallet.owner_type == owner_type, CreditWallet.owner_id == owner_id
        )
    )


def _ar_view(r: CreditAllocationRequest) -> dict:
    return {
        "id": r.id, "requester_id": r.requester_id, "target_wallet_id": r.target_wallet_id,
        "level": r.level, "fulfiller_scope": r.fulfiller_scope, "fulfiller_id": r.fulfiller_id,
        "amount": str(r.amount), "status": r.status, "note": r.note, "parent_id": r.parent_id,
        "decided_by": r.decided_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _admin_projects(principal: Principal) -> list[str]:
    return [pid for pid, role in principal.memberships.items()
            if role in ("group_admin", "org_admin")]


def _can_fulfill(principal: Principal, r: CreditAllocationRequest) -> bool:
    if r.fulfiller_scope == "system":
        return principal.global_role == "super_admin"
    if principal.global_role == "super_admin":
        return True
    if r.fulfiller_scope == "org":
        return r.fulfiller_id in principal.org_admin_orgs
    if r.fulfiller_scope == "group":
        return principal.memberships.get(r.fulfiller_id) in ("group_admin", "org_admin")
    return False


async def _notify(db: AsyncSession, user_ids, ntype: str, payload: dict) -> None:
    """Create notifications for the target users (deduplicated) and ping the live push channel.
    The caller owns the commit."""
    from app.core.redis import get_redis
    from app.domain.notification_service import NOTIF_CHANNEL

    recipients = {u for u in user_ids if u}
    for uid in recipients:
        db.add(Notification(id=ids.new("notification"), user_id=uid, type=ntype, payload=payload))
    if recipients:
        try:
            r = get_redis()
            for uid in recipients:
                await r.publish(NOTIF_CHANNEL.format(user_id=uid), '{"kind":"notification"}')
        except Exception:  # noqa: BLE001
            pass


async def _fulfiller_user_ids(db: AsyncSession, scope: str, fid: str | None) -> list[str]:
    """User ids of the administrators who can approve this request; the notification recipients."""
    if scope == "system":
        rows = await db.scalars(
            select(User.id).where(
                User.global_role == "super_admin",
                User.deleted_at.is_(None),
            )
        )
        return list(rows.all())
    if scope == "group":
        rows = await db.scalars(
            select(Membership.user_id).where(
                Membership.group_id == fid,
                Membership.role.in_(["group_admin", "org_admin"]),
            )
        )
        return list(rows.all())
    if scope == "org":
        rows = await db.scalars(
            select(Membership.user_id)
            .join(Project, Project.id == Membership.group_id)
            .where(Project.org_id == fid, Membership.role == "org_admin")
        )
        return list(rows.all())
    return []


@router.post("/allocation-requests", status_code=status.HTTP_201_CREATED)
async def create_allocation_request(
    body: AllocationRequestCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Create a credit allocation request, routed one level up: a user's goes to the group admin, a
    group's to the organization admin, an organization's to a super_admin."""
    su = principal.global_role == "super_admin"
    if body.level == "user":
        if not body.group_id:
            raise _Validation("group_id required for user-level request")
        if not su and principal.memberships.get(body.group_id) is None:
            raise Forbidden("not a member of the project")
        target = await _wallet_of(db, "user", principal.user_id)
        if target is None:
            raise NotFound("user wallet not found")
        fscope, fid = "group", body.group_id
    elif body.level == "group":
        if not body.group_id:
            raise _Validation("group_id required")
        if not su and principal.memberships.get(body.group_id) not in ("group_admin", "org_admin"):
            raise Forbidden("not group_admin of the project")
        prj = await db.get(Project, body.group_id)
        if prj is None:
            raise NotFound("project not found")
        target = await _wallet_of(db, "group", body.group_id)
        if target is None:
            raise NotFound("project wallet not found")
        fscope, fid = "org", prj.org_id
    else:  # org
        if not body.org_id:
            raise _Validation("org_id required")
        if not su and body.org_id not in principal.org_admin_orgs:
            raise Forbidden("not org_admin of the organization")
        target = await _wallet_of(db, "org", body.org_id)
        if target is None:
            raise NotFound("org wallet not found")
        fscope, fid = "system", None

    r = CreditAllocationRequest(
        id=ids.new("allocrequest"), requester_id=principal.user_id,
        target_wallet_id=target.id, level=body.level, fulfiller_scope=fscope,
        fulfiller_id=fid, amount=body.amount, status="pending", note=body.note,
    )
    db.add(r)
    await db.flush()
    # Notify whoever can approve: the group or organization admin, or a super_admin.
    await _notify(
        db, await _fulfiller_user_ids(db, fscope, fid), "credit_allocation_request",
        {"title": "Credit allocation requested", "body": f"A request for {body.amount}C has arrived.",
         "request_id": r.id, "amount": str(body.amount), "level": body.level,
         "requester_id": principal.user_id},
    )
    await AuditService(db).record(
        actor=principal.user_id, action="credit.allocation_request", target=r.id,
        result="pending", level=body.level, amount=str(body.amount),
    )
    await db.commit()
    return _ar_view(r)


@router.get("/allocation-requests")
async def list_allocation_requests(
    box: str = Query(default="incoming", pattern="^(incoming|mine)$"),
    page: Pagination = Depends(),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """box=mine lists the requests you raised; box=incoming lists pending requests you can
    approve."""
    stmt = select(CreditAllocationRequest)
    if box == "mine":
        stmt = stmt.where(CreditAllocationRequest.requester_id == principal.user_id)
    else:
        conds = []
        if principal.global_role == "super_admin":
            conds.append(CreditAllocationRequest.fulfiller_scope == "system")
        if principal.org_admin_orgs:
            conds.append(and_(
                CreditAllocationRequest.fulfiller_scope == "org",
                CreditAllocationRequest.fulfiller_id.in_(principal.org_admin_orgs),
            ))
        ap = _admin_projects(principal)
        if ap:
            conds.append(and_(
                CreditAllocationRequest.fulfiller_scope == "group",
                CreditAllocationRequest.fulfiller_id.in_(ap),
            ))
        if not conds:
            return {"data": []}
        stmt = stmt.where(or_(*conds), CreditAllocationRequest.status == "pending")
    rows = (await db.scalars(
        stmt.order_by(CreditAllocationRequest.created_at.desc())
        .offset(page.offset).limit(page.size)
    )).all()
    # Resolve requester and target names so the UI shows names, with the ULID as a secondary label.
    uids = {r.requester_id for r in rows if r.requester_id}
    oids = {r.fulfiller_id for r in rows if r.fulfiller_scope == "org" and r.fulfiller_id}
    gids = {r.fulfiller_id for r in rows if r.fulfiller_scope == "group" and r.fulfiller_id}
    # The group a request concerns: for user-to-group it is the fulfiller project; for
    # group-to-organization it is the project that owns the target wallet.
    twids = {r.target_wallet_id for r in rows if r.target_wallet_id}
    wallet_proj: dict[str, str] = {}
    if twids:
        for wid, otype, oid in (
            await db.execute(
                select(CreditWallet.id, CreditWallet.owner_type, CreditWallet.owner_id)
                .where(CreditWallet.id.in_(twids))
            )
        ).all():
            if otype == "group":
                wallet_proj[wid] = oid
    pids = set(gids) | set(wallet_proj.values())
    unames = {u: n for u, n in (await db.execute(select(User.id, User.name).where(User.id.in_(uids)))).all()} if uids else {}
    onames = {o: n for o, n in (await db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(oids)))).all()} if oids else {}
    gnames = {g: n for g, n in (await db.execute(select(Project.id, Project.name).where(Project.id.in_(pids)))).all()} if pids else {}

    def _fname(r: CreditAllocationRequest) -> str | None:
        if r.fulfiller_scope == "org":
            return onames.get(r.fulfiller_id)
        if r.fulfiller_scope == "group":
            return gnames.get(r.fulfiller_id)
        if r.fulfiller_scope == "system":
            return "System"
        return None

    def _group_name(r: CreditAllocationRequest) -> str | None:
        # Group column: the fulfiller group for user-to-group, the requesting group for
        # group-to-organization, and nothing for organization-to-system.
        if r.fulfiller_scope == "group":
            return gnames.get(r.fulfiller_id)
        pid = wallet_proj.get(r.target_wallet_id)
        return gnames.get(pid) if pid else None

    return {"data": [
        {**_ar_view(r), "requester_name": unames.get(r.requester_id), "fulfiller_name": _fname(r),
         "group_name": _group_name(r)}
        for r in rows
    ]}


async def _load_ar(db: AsyncSession, request_id: str) -> CreditAllocationRequest:
    r = await db.get(CreditAllocationRequest, request_id)
    if r is None:
        raise NotFound("allocation request not found")
    return r


@router.post("/allocation-requests/{request_id}/approve")
async def approve_allocation_request(
    request_id: str,
    principal: Principal = Depends(get_current_principal),
    idem: str | None = Depends(idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    """Approve a request: allocate from the parent wallet to the target, or top the target up when
    the parent is the system wallet. An insufficient pool returns 409, prompting escalation."""
    require_idem(idem)
    r = await _load_ar(db, request_id)
    if r.status != "pending":
        raise _Validation("request is not pending")
    if not _can_fulfill(principal, r):
        raise Forbidden("you cannot fulfill this request")

    amount = r.amount
    if r.fulfiller_scope == "system":
        w = await _lock_wallet(db, r.target_wallet_id)
        w.balance = w.balance + amount
        w.version = w.version + 1
        db.add(_make_txn(w, "topup", amount, key=f"alloc:{request_id}", ref=f"alloc-req:{request_id}"))
    else:
        owner = ("group", r.fulfiller_id) if r.fulfiller_scope == "group" else ("org", r.fulfiller_id)
        src0 = await _wallet_of(db, *owner)
        if src0 is None:
            raise NotFound("source (pool) wallet not found")
        first_id, second_id = sorted((src0.id, r.target_wallet_id))
        first = await _lock_wallet(db, first_id)
        second = await _lock_wallet(db, second_id)
        src = first if first.id == src0.id else second
        dst = first if first.id == r.target_wallet_id else second
        if src.balance - src.reserved < amount:
            raise _InsufficientPool(
                "pool wallet has insufficient funds; escalate to the higher tier",
                {"available": str(src.balance - src.reserved), "need": str(amount),
                 "fulfiller_scope": r.fulfiller_scope},
            )
        src.balance = src.balance - amount
        src.version = src.version + 1
        dst.balance = dst.balance + amount
        dst.version = dst.version + 1
        db.add(_make_txn(src, "adjust", -amount, key=f"alloc:{request_id}:out", ref=f"alloc-req:{request_id}"))
        db.add(_make_txn(dst, "adjust", amount, key=f"alloc:{request_id}:in", ref=f"alloc-req:{request_id}"))

    r.status = "approved"
    r.decided_by = principal.user_id
    await db.flush()
    await _notify(
        db, [r.requester_id], "credit_allocation_approved",
        {"title": "Credit request approved", "body": f"{amount}C was allocated to your wallet.",
         "request_id": r.id, "amount": str(amount), "decided_by": principal.user_id},
    )
    await AuditService(db).record(
        actor=principal.user_id, action="credit.allocation_approve", target=request_id,
        result="approved", amount=str(amount),
    )
    await db.commit()
    return _ar_view(r)


@router.post("/allocation-requests/{request_id}/reject")
async def reject_allocation_request(
    request_id: str,
    body: AllocationRejectBody,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    r = await _load_ar(db, request_id)
    if r.status != "pending":
        raise _Validation("request is not pending")
    if not _can_fulfill(principal, r):
        raise Forbidden("you cannot fulfill this request")
    r.status = "rejected"
    r.decided_by = principal.user_id
    r.note = (r.note + " | " if r.note else "") + f"reject: {body.reason}"
    await db.flush()
    await _notify(
        db, [r.requester_id], "credit_allocation_rejected",
        {"title": "Credit request rejected", "body": f"Reason: {body.reason}",
         "request_id": r.id, "reason": body.reason, "decided_by": principal.user_id},
    )
    await AuditService(db).record(
        actor=principal.user_id, action="credit.allocation_reject", target=request_id,
        result="rejected", reason=body.reason,
    )
    await db.commit()
    return _ar_view(r)


@router.post("/allocation-requests/{request_id}/escalate", status_code=status.HTTP_201_CREATED)
async def escalate_allocation_request(
    request_id: str,
    body: AllocationEscalateBody,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """When the pool is short, let the approver raise a funding request one tier up. The original
    request stays pending."""
    r = await _load_ar(db, request_id)
    if r.status != "pending":
        raise _Validation("request is not pending")
    if not _can_fulfill(principal, r):
        raise Forbidden("you cannot escalate this request")
    if r.fulfiller_scope == "system":
        raise _Validation("system-level request cannot be escalated further")
    amount = body.amount or r.amount
    if r.fulfiller_scope == "group":
        prj = await db.get(Project, r.fulfiller_id)
        pw = await _wallet_of(db, "group", r.fulfiller_id)
        parent = CreditAllocationRequest(
            id=ids.new("allocrequest"), requester_id=principal.user_id,
            target_wallet_id=pw.id, level="group", fulfiller_scope="org",
            fulfiller_id=prj.org_id, amount=amount, status="pending",
            note=f"escalated from {request_id}", parent_id=request_id,
        )
    else:  # org -> system
        ow = await _wallet_of(db, "org", r.fulfiller_id)
        parent = CreditAllocationRequest(
            id=ids.new("allocrequest"), requester_id=principal.user_id,
            target_wallet_id=ow.id, level="org", fulfiller_scope="system",
            fulfiller_id=None, amount=amount, status="pending",
            note=f"escalated from {request_id}", parent_id=request_id,
        )
    db.add(parent)
    await db.flush()
    # Notify the tier above: the organization admin, or a super_admin.
    await _notify(
        db, await _fulfiller_user_ids(db, parent.fulfiller_scope, parent.fulfiller_id),
        "credit_allocation_request",
        {"title": "Escalated allocation request", "body": f"An escalated request for {amount}C has arrived.",
         "request_id": parent.id, "amount": str(amount), "level": parent.level,
         "requester_id": principal.user_id, "escalated_from": request_id},
    )
    await AuditService(db).record(
        actor=principal.user_id, action="credit.allocation_escalate", target=request_id,
        result="escalated", parent=parent.id, amount=str(amount),
    )
    await db.commit()
    return _ar_view(parent)


# ── ledger reads ───────────────────────────────────────────────
@router.get("/wallets/{wallet_id}/transactions", response_model=list[TransactionRead])
async def transactions(
    wallet_id: str,
    page: Pagination = Depends(),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    wallet = await _get_wallet(db, wallet_id)
    if not _can_read_wallet(principal, wallet):
        raise Forbidden("not permitted: wallet.read")
    rows = (
        await db.scalars(
            select(CreditTransaction)
            .where(CreditTransaction.wallet_id == wallet_id)
            .order_by(CreditTransaction.created_at.desc())
            .limit(page.size)
            .offset(page.offset)
        )
    ).all()
    return [TransactionRead.model_validate(t) for t in rows]


@router.get("/wallets/{wallet_id}/holds")
async def holds(
    wallet_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Active reservations + per-session settle status.

    Reconstructed from the ledger: hold/consume/settle/refund txns are grouped by ``ref``
    (session id). A session with a hold but no settle is ``active``; otherwise ``settled``.
    """
    wallet = await _get_wallet(db, wallet_id)
    if not _can_read_wallet(principal, wallet):
        raise Forbidden("not permitted: wallet.read")
    rows = (
        await db.scalars(
            select(CreditTransaction)
            .where(CreditTransaction.wallet_id == wallet_id)
            .order_by(CreditTransaction.created_at.asc())
        )
    ).all()

    by_session: dict[str, dict] = {}
    for t in rows:
        ref = t.ref
        if ref is None or t.type not in ("hold", "consume", "settle", "refund"):
            continue
        agg = by_session.setdefault(
            ref,
            {"session_id": ref, "hold_amount": Decimal(0), "consumed": Decimal(0),
             "refunded": Decimal(0), "status": "active", "held_at": None, "settled_at": None},
        )
        if t.type == "hold":
            agg["held_at"] = t.created_at
            agg["hold_amount"] += t.amount
        elif t.type == "consume":
            agg["consumed"] += t.amount
        elif t.type == "refund":
            agg["refunded"] += t.amount
            agg["status"] = "settled"
            agg["settled_at"] = t.created_at
        elif t.type == "settle":
            agg["status"] = "settled"
            agg["settled_at"] = t.created_at

    holds_out = []
    for agg in by_session.values():
        holds_out.append({
            "session_id": agg["session_id"],
            "hold_amount": str(agg["hold_amount"]),
            "consumed": str(agg["consumed"]),
            "refunded": str(agg["refunded"]),
            "status": agg["status"],
            "held_at": agg["held_at"].isoformat() if agg["held_at"] else None,
            "settled_at": agg["settled_at"].isoformat() if agg["settled_at"] else None,
        })
    return {"reserved": str(wallet.reserved), "holds": holds_out}


# ── topup-requests (approval flow) ───────────────────────────────
@router.post("/topup-requests", status_code=status.HTTP_201_CREATED)
async def create_topup_request(
    body: TopupRequestBody,
    wallet_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    # wallet_id resolution order: query parameter, then request body, then the caller's personal
    # wallet. None of the three means 404.
    target_id = wallet_id or body.wallet_id
    if target_id is None:
        target_id = await db.scalar(
            select(CreditWallet.id).where(
                CreditWallet.owner_type == "user",
                CreditWallet.owner_id == principal.user_id,
            )
        )
    if target_id is None:
        raise NotFound("wallet not found")
    wallet = await _get_wallet(db, target_id)
    if not _can_read_wallet(principal, wallet):
        raise Forbidden("not permitted: wallet.read")
    req = TopupRequest(
        id=ids.new("topup"),
        wallet_id=wallet.id,
        amount=Decimal(body.amount),
        status="pending",
        requester_id=principal.user_id,
    )
    db.add(req)
    await db.flush()
    # Notify whoever can approve a top-up: billing or super_admin.
    await _notify(
        db, await _fulfiller_user_ids(db, "system", None), "credit_topup_request",
        {"title": "Top-up requested", "body": f"A top-up request for {req.amount}C has arrived.",
         "request_id": req.id, "amount": str(req.amount), "requester_id": principal.user_id},
    )
    await db.commit()
    return {
        "id": req.id, "wallet_id": req.wallet_id, "amount": str(req.amount),
        "status": req.status, "requester_id": req.requester_id, "transaction_id": None,
    }


@router.get("/topup-requests", response_model=TopupRequestListResponse)
async def list_topup_requests(
    page: Pagination = Depends(),
    status_filter: str | None = Query(default=None, alias="status"),
    wallet_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="credit.topup")
    stmt = select(TopupRequest)
    if status_filter is not None:
        stmt = stmt.where(TopupRequest.status == status_filter)
    if wallet_id is not None:
        stmt = stmt.where(TopupRequest.wallet_id == wallet_id)
    stmt = stmt.order_by(TopupRequest.created_at.desc()).limit(page.size).offset(page.offset)
    rows = (await db.scalars(stmt)).all()
    uids = {r.requester_id for r in rows if r.requester_id}
    unames = {u: n for u, n in (await db.execute(select(User.id, User.name).where(User.id.in_(uids)))).all()} if uids else {}
    return {
        "data": [
            {"id": r.id, "wallet_id": r.wallet_id, "amount": str(r.amount),
             "status": r.status, "requester_id": r.requester_id,
             "requester_name": unames.get(r.requester_id),
             "decided_by": r.decided_by, "created_at": r.created_at.isoformat()}
            for r in rows
        ]
    }


@router.post("/topup-requests/{request_id}/approve")
async def approve_topup_request(
    request_id: str,
    principal: Principal = Depends(get_current_principal),
    idem: str | None = Depends(idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    require_idem(idem)
    principal.require(action="credit.topup")

    req = await db.get(TopupRequest, request_id)
    if req is None:
        raise NotFound("topup request not found")

    # Idempotent: approval credits exactly once on topup-req:{id}.
    txn_key = f"topup-req:{req.id}"
    replay = await _existing_txn(db, txn_key)
    if replay is not None or req.status == "approved":
        wallet = await _get_wallet(db, req.wallet_id)
        return {
            "topup_request": {"id": req.id, "status": "approved",
                              "decided_by": req.decided_by,
                              "transaction_id": replay.id if replay else None},
            "transaction": _txn_view(replay) if replay else None,
            "wallet": _wallet_view(wallet),
        }
    if req.status == "rejected":
        from app.core.errors import InvalidStateTransition

        raise InvalidStateTransition("topup request already rejected")

    wallet = await _lock_wallet(db, req.wallet_id)
    amount = Decimal(req.amount)
    wallet.balance = wallet.balance + amount
    wallet.version = wallet.version + 1
    txn = _make_txn(wallet, "topup", amount, key=txn_key, ref=f"topup-req:{req.id}")
    db.add(txn)
    req.status = "approved"
    req.decided_by = principal.user_id
    await db.flush()
    await _notify(
        db, [req.requester_id], "credit_topup_approved",
        {"title": "Top-up approved", "body": f"{amount}C was added to your wallet.",
         "request_id": req.id, "amount": str(amount)},
    )
    await AuditService(db).record(
        actor=principal.user_id, action="credit.topup_request.approve",
        target=req.id, wallet_id=req.wallet_id, amount=str(amount), txn_id=txn.id,
    )
    await db.commit()
    return {
        "topup_request": {"id": req.id, "status": "approved",
                          "decided_by": req.decided_by, "transaction_id": txn.id},
        "transaction": _txn_view(txn),
        "wallet": _wallet_view(wallet),
    }


@router.post("/topup-requests/{request_id}/reject")
async def reject_topup_request(
    request_id: str,
    body: TopupRejectBody,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="credit.topup")
    req = await db.get(TopupRequest, request_id)
    if req is None:
        raise NotFound("topup request not found")
    if req.status == "approved":
        from app.core.errors import InvalidStateTransition

        raise InvalidStateTransition("topup request already approved")
    req.status = "rejected"
    req.decided_by = principal.user_id
    req.decided_reason = body.reason
    await db.flush()
    await _notify(
        db, [req.requester_id], "credit_topup_rejected",
        {"title": "Top-up rejected",
         "body": f"Your top-up request for {req.amount}C was rejected. Reason: {body.reason}",
         "request_id": req.id, "reason": body.reason},
    )
    await AuditService(db).record(
        actor=principal.user_id, action="credit.topup_request.reject", target=req.id,
        reason=body.reason,
    )
    await db.commit()
    return {"id": req.id, "status": req.status, "decided_by": req.decided_by,
            "decided_reason": req.decided_reason}


# ── txn construction / view ────────────────────────────────────────────────────
def _make_txn(
    wallet: CreditWallet, type: str, amount: Decimal, *, key: str, ref: str | None
) -> CreditTransaction:
    """Build a CreditTransaction with balance_after snapshot."""
    return CreditTransaction(
        id=ids.new("transaction"),
        wallet_id=wallet.id,
        type=type,
        amount=amount,
        balance_after=wallet.balance,
        ref=ref,
        idempotency_key=key,
    )


def _txn_view(txn: CreditTransaction) -> dict:
    return {
        "id": txn.id,
        "wallet_id": txn.wallet_id,
        "type": txn.type,
        "amount": str(txn.amount),
        "balance_after": str(txn.balance_after),
        "ref": txn.ref,
        "idempotency_key": txn.idempotency_key,
    }
