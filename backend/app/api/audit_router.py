"""Audit router. Read hash-chained audit logs."""
from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.api.schemas.audit import AuditLogList
from app.auth.rbac import Principal
from app.db.base import get_db
from app.db.models import (
    AuditLog,
    Cluster,
    CreditWallet,
    GpuNode,
    Image,
    Membership,
    Offering,
    Organization,
    Project,
    StorageVolume,
    User,
)
from app.db.models import (
    Session as SessionModel,
)
from app.domain.audit_service import AuditService

router = APIRouter(tags=["audit"])

# Prefixed-ULID pattern (usr_…, grp_…, org_… and so on). Used to find ids inside a detail payload
# and resolve them to names.
_ID_RE = re.compile(r"^[a-z]{2,5}_[0-9A-Za-z]{10,}$")


def _collect_ids(obj: object, acc: set[str]) -> None:
    """Recursively collect every prefixed-ULID string inside a JSON detail payload."""
    if isinstance(obj, str):
        if _ID_RE.match(obj):
            acc.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_ids(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _collect_ids(v, acc)


async def _resolve_simple_names(db: AsyncSession, ids: set[str]) -> dict[str, str]:
    """Resolve an arbitrary set of ids to names — users, organizations, groups, clusters, images,
    offerings, nodes, volumes — batched by prefix."""
    if not ids:
        return {}
    by: dict[str, set[str]] = {}
    for i in ids:
        by.setdefault(i.split("_", 1)[0], set()).add(i)
    out: dict[str, str] = {}

    async def simple(idset: set[str], col_id, col_name) -> None:
        if not idset:
            return
        for k, n in (await db.execute(select(col_id, col_name).where(col_id.in_(idset)))).all():
            if n:
                out[k] = n

    await simple(by.get("usr", set()), User.id, User.name)
    await simple(by.get("org", set()), Organization.id, Organization.name)
    await simple(by.get("grp", set()), Project.id, Project.name)
    await simple(by.get("clu", set()), Cluster.id, Cluster.name)
    await simple(by.get("img", set()), Image.id, Image.name)
    await simple(by.get("off", set()), Offering.id, Offering.name)
    await simple(by.get("nod", set()), GpuNode.id, GpuNode.hostname)
    if by.get("vol"):
        for vid, nm in (await db.execute(
            select(StorageVolume.id, StorageVolume.name).where(StorageVolume.id.in_(by["vol"]))
        )).all():
            out[vid] = nm or "(unnamed)"
    return out


async def _resolve_target_names(db: AsyncSession, rows: list[AuditLog]) -> dict[str, str]:
    """Map target ids to human-readable names — whose wallet, session, or volume, and the names of
    clusters, organizations, and groups. Resolved in one batch per page."""
    targets = {r.target for r in rows if r.target}
    if not targets:
        return {}
    by: dict[str, set[str]] = {}
    for t in targets:
        pfx = t.split("_", 1)[0] if "_" in t else ""
        by.setdefault(pfx, set()).add(t)
    out: dict[str, str] = {}

    async def simple(ids: set[str], col_id, col_name):
        if not ids:
            return
        for i, n in (await db.execute(select(col_id, col_name).where(col_id.in_(ids)))).all():
            if n:
                out[i] = n

    await simple(by.get("usr", set()), User.id, User.name)
    await simple(by.get("clu", set()), Cluster.id, Cluster.name)
    await simple(by.get("org", set()), Organization.id, Organization.name)
    await simple(by.get("grp", set()), Project.id, Project.name)
    await simple(by.get("off", set()), Offering.id, Offering.name)
    await simple(by.get("img", set()), Image.id, Image.name)
    await simple(by.get("nod", set()), GpuNode.id, GpuNode.hostname)

    # Volumes: the user-chosen name.
    if by.get("vol"):
        for vid, name in (await db.execute(
            select(StorageVolume.id, StorageVolume.name).where(StorageVolume.id.in_(by["vol"]))
        )).all():
            out[vid] = name or "(unnamed)"

    # Sessions: the session name plus its owner.
    if by.get("ses"):
        for sid, name, owner in (await db.execute(
            select(SessionModel.id, SessionModel.name, User.name)
            .join(User, User.id == SessionModel.owner_user_id, isouter=True)
            .where(SessionModel.id.in_(by["ses"]))
        )).all():
            out[sid] = f"{name or 'session'}{f' ({owner})' if owner else ''}"

    # Wallets: the owner's name — user, group, or organization — so the label reads as whose wallet.
    if by.get("wal"):
        wrows = (await db.execute(
            select(CreditWallet.id, CreditWallet.owner_type, CreditWallet.owner_id)
            .where(CreditWallet.id.in_(by["wal"]))
        )).all()
        uids = {oid for _, t, oid in wrows if t == "user"}
        gids = {oid for _, t, oid in wrows if t == "group"}
        oids = {oid for _, t, oid in wrows if t == "org"}
        un = {i: n for i, n in (await db.execute(select(User.id, User.name).where(User.id.in_(uids)))).all()} if uids else {}
        gn = {i: n for i, n in (await db.execute(select(Project.id, Project.name).where(Project.id.in_(gids)))).all()} if gids else {}
        on = {i: n for i, n in (await db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(oids)))).all()} if oids else {}
        for wid, t, oid in wrows:
            owner = {"user": un, "group": gn, "org": on}.get(t, {}).get(oid)
            out[wid] = owner or oid

    # Memberships: "user / group", or "user / organization (organization admin)" when org-scoped.
    if by.get("mbr"):
        mrows = (await db.execute(
            select(Membership.id, Membership.user_id, Membership.group_id, Membership.org_id)
            .where(Membership.id.in_(by["mbr"]))
        )).all()
        muids = {u for _, u, _, _ in mrows}
        mgids = {g for _, _, g, _ in mrows if g}
        moids = {o for _, _, _, o in mrows if o}
        mun = {i: n for i, n in (await db.execute(select(User.id, User.name).where(User.id.in_(muids)))).all()} if muids else {}
        mgn = {i: n for i, n in (await db.execute(select(Project.id, Project.name).where(Project.id.in_(mgids)))).all()} if mgids else {}
        mon = {i: n for i, n in (await db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(moids)))).all()} if moids else {}
        for mid, uid, gid, oid in mrows:
            user = mun.get(uid, uid)
            if gid:
                out[mid] = f"{user} / {mgn.get(gid, gid)}"
            elif oid:
                out[mid] = f"{user} / {mon.get(oid, oid)} (organization admin)"
            else:
                out[mid] = user

    return out


def _audit_view(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "actor_id": row.actor,
        "action": row.action,
        "target": row.target,
        "result": row.result,
        "detail": dict(row.detail or {}),
        "trace_id": row.trace_id,
        "prev_hash": row.prev_hash,
        "entry_hash": row.entry_hash,
        "at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/audit-logs", response_model=AuditLogList)
async def list_audit_logs(
    page: Pagination = Depends(),
    actor_id: str | None = Query(default=None),
    actor_q: str | None = Query(default=None),   # search by actor name or email, so no ULID is needed
    action: str | None = Query(default=None),
    target: str | None = Query(default=None),
    at_gte: datetime | None = Query(default=None, alias="at[gte]"),
    at_lt: datetime | None = Query(default=None, alias="at[lt]"),
    sort: str = Query(default="-at"),
    verify: bool = Query(default=False),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="audit.read")

    base = select(AuditLog)
    # Hierarchy scope, based on the audit row's scope columns: super_admin sees everything,
    # org_admin their own organization, group_admin only their own group.
    if principal.global_role != "super_admin":
        admin_gids = [
            pid for pid, role in principal.memberships.items()
            if role in ("group_admin", "org_admin")
        ]
        admin_orgs = list(principal.org_admin_orgs)
        conds = []
        if admin_gids:
            conds.append(AuditLog.group_id.in_(admin_gids))
        if admin_orgs:
            conds.append(AuditLog.org_id.in_(admin_orgs))
        base = base.where(or_(*conds)) if conds else base.where(false())
    if actor_id is not None:
        base = base.where(AuditLog.actor == actor_id)
    if actor_q:
        like = f"%{actor_q.strip()}%"
        sub = select(User.id).where(or_(User.name.ilike(like), User.email.ilike(like)))
        base = base.where(AuditLog.actor.in_(sub))
    if action is not None:
        base = base.where(AuditLog.action == action)
    if target is not None:
        base = base.where(AuditLog.target == target)
    if at_gte is not None:
        base = base.where(AuditLog.created_at >= at_gte)
    if at_lt is not None:
        base = base.where(AuditLog.created_at < at_lt)

    total = await db.scalar(select(func.count()).select_from(base.subquery()))

    # Default sort is newest-first by created_at.
    desc = not (sort == "at")
    order = AuditLog.created_at.desc() if desc else AuditLog.created_at.asc()
    rows = (
        await db.scalars(
            base.order_by(order, AuditLog.id.desc() if desc else AuditLog.id.asc())
            .limit(page.size).offset(page.offset)
        )
    ).all()

    # Map actor ULIDs to names so the UI can show a name.
    actor_ids = {r.actor for r in rows if r.actor}
    names: dict[str, str] = {}
    emails: dict[str, str] = {}
    if actor_ids:
        nrows = (await db.execute(
            select(User.id, User.name, User.email).where(User.id.in_(actor_ids))
        )).all()
        names = {uid: nm for uid, nm, _ in nrows}
        emails = {uid: em for uid, _, em in nrows if em}

    target_names = await _resolve_target_names(db, list(rows))

    # Resolve every id found in the JSON detail and in the row's scope columns (org_id, group_id).
    detail_ids: set[str] = set()
    for r in rows:
        _collect_ids(r.detail, detail_ids)
        if r.org_id:
            detail_ids.add(r.org_id)
        if r.group_id:
            detail_ids.add(r.group_id)
    id_names = await _resolve_simple_names(db, detail_ids)
    id_names.update(names)  # actor id→name
    for r in rows:
        if target_names.get(r.target):
            id_names[r.target] = target_names[r.target]

    # Hard-deleted users (usr_) have no live row left to resolve, so the name snapshot written into
    # the detail at deletion time is used instead.
    for r in rows:
        tid = r.target
        if not tid or not tid.startswith("usr_") or target_names.get(tid):
            continue
        det = r.detail or {}
        snap = det.get("name") if isinstance(det, dict) else None
        if snap:
            target_names[tid] = snap
            id_names[tid] = snap  # so other rows on the same page resolve this user_id to a name too

    # Hard-deleted memberships (mbr_) likewise have no live row, so the label is reconstructed from
    # detail.user_id and the audit row's scope.
    for r in rows:
        tid = r.target
        if not tid or not tid.startswith("mbr_") or target_names.get(tid):
            continue
        det = r.detail or {}
        uid = det.get("user_id") if isinstance(det, dict) else None
        uname = id_names.get(uid) if uid else None
        if r.org_id:
            scope = id_names.get(r.org_id, r.org_id)
            recon = f"{uname or uid} / {scope} (organization admin)" if uname or uid else None
        elif r.group_id:
            scope = id_names.get(r.group_id, r.group_id)
            recon = f"{uname or uid} / {scope}" if uname or uid else None
        else:
            recon = uname
        if recon:
            target_names[tid] = recon
            id_names[tid] = recon

    out: dict = {
        "data": [
            {**_audit_view(r), "actor_name": names.get(r.actor),
            "actor_email": emails.get(r.actor), "target_name": target_names.get(r.target)}
            for r in rows
        ],
        "names": id_names,
        "pagination": {
            "page": page.page, "size": page.size, "total": total or 0,
            "total_pages": ((total or 0) + page.size - 1) // page.size,
        },
    }
    # Optional integrity attestation: recompute and check the full hash chain.
    if verify:
        out["chain"] = await AuditService(db).verify_chain()
    return out
