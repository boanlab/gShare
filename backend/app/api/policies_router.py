"""Resource policies router. Quota policy (max_concurrent/max_queued)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.auth.rbac import Principal, rbac_allows
from app.core import ids
from app.core.errors import DomainError, Forbidden, NotFound
from app.core.logging import get_logger
from app.db.base import get_db
from app.db.models import (
    Membership,
    QueueEntry,
    ResourcePolicy,
    ResourceRequest,
    StorageVolume,
    User,
)
from app.db.models import Session as SessionModel
from app.domain.audit_service import AuditService
from app.domain.notification_service import NotificationService
from app.domain.policy import resolve_effective_policy

log = get_logger(__name__)

router = APIRouter(prefix="/resource-policies", tags=["policies"])


class _Unprocessable(DomainError):
    code, http = "validation_failed", 422


class _Conflict(DomainError):
    code, http = "conflict", 409


async def _assert_policy_perm(
    principal: Principal, action: str, scope: str, scope_id: str, db: AsyncSession | None = None
) -> None:
    """Authorize by the policy's own scope. Binding on group_id alone would leak the org, user, and
    global scopes.

    global: super_admin only. org: super_admin, or that organization's org_admin. group:
    super_admin, org_admin, or that group's group_admin. user: super_admin, or a group_admin+ of a
    group the target user belongs to — so a TA can cap or extend their own students without
    system-administrator help.
    """
    if "super_admin" in principal.global_roles:
        return
    if scope == "global":
        raise Forbidden("only super_admin can manage the common (global) policy")
    if scope == "org":
        if scope_id not in principal.org_admin_orgs:
            raise Forbidden(f"not permitted: {action} (not org_admin of this organization)")
        return
    if scope == "group":
        if not rbac_allows(principal, action, scope_id):   # group_admin and above for that group, including the org_admin expansion
            raise Forbidden(f"not permitted: {action}")
        return
    if scope == "user" and db is not None:
        # group_admin+ over any group the target user is a member of.
        target_groups = (
            await db.scalars(
                select(Membership.group_id).where(
                    Membership.user_id == scope_id, Membership.group_id.is_not(None)
                )
            )
        ).all()
        if any(rbac_allows(principal, action, gid) for gid in target_groups):
            return
    raise Forbidden(f"not permitted: {action} (user-scoped policy needs group_admin over the user)")


def _can_read_policy(principal: Principal, scope: str, scope_id: str) -> bool:
    """Decide read access for one policy or a list, using the same scope rules as
    _assert_policy_perm — reading needs the same authority as writing.

    The global and user scopes are super_admin only; org needs an org_admin; group needs group_admin
    and above for that group.
    """
    if "super_admin" in principal.global_roles:
        return True
    if scope == "org":
        return scope_id in principal.org_admin_orgs
    if scope == "group":
        return rbac_allows(principal, "policy.read", scope_id)
    # The global and user scopes are super_admin only.
    return False


class PolicyCreate(BaseModel):
    scope: str
    scope_id: str
    max_concurrent: int
    max_queued: int
    max_runtime_min: int
    idle_timeout_sec: int
    cpu_session_max_concurrent: int = 1
    cpu_session_max_runtime_min: int = 240
    cpu_session_idle_timeout_sec: int = 1800
    limits: dict = {}


class PolicyUpdate(BaseModel):
    max_concurrent: int | None = None
    max_queued: int | None = None
    max_runtime_min: int | None = None
    idle_timeout_sec: int | None = None
    cpu_session_max_concurrent: int | None = None
    cpu_session_max_runtime_min: int | None = None
    cpu_session_idle_timeout_sec: int | None = None
    limits: dict | None = None


# Keys of ResourcePolicy.limits exposed through the public `limits` object: the resource quotas
# plus the node-pool spill switch (see app.domain.node_pools). CPU-session limits have their own
# top-level fields.
_PUBLIC_LIMIT_KEYS = ("cpu", "mem_gb", "gpu_mem_mb", "gpu_cores", "storage_gb", "shared_pool", "volume_gb")


def _policy_view(p: ResourcePolicy) -> dict:
    limits = dict(p.limits or {})
    return {
        "id": p.id,
        "scope": p.scope,
        "scope_id": p.scope_id,
        "max_concurrent": p.max_concurrent,
        "max_queued": p.max_queued,
        "max_runtime_min": p.max_runtime,
        "idle_timeout_sec": p.idle_timeout,
        "cpu_session_max_concurrent": limits.get("cpu_session_max_concurrent"),
        "cpu_session_max_runtime_min": limits.get("cpu_session_max_runtime_min"),
        "cpu_session_idle_timeout_sec": limits.get("cpu_session_idle_timeout_sec"),
        # Only the resource-quota keys belong in the public limits object.
        "limits": {k: v for k, v in limits.items() if k in _PUBLIC_LIMIT_KEYS},
    }


_ACTIVE_STATUSES = ("pending", "preparing", "running", "paused", "terminating")


@router.get("/effective")
async def effective_policy(
    group_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """The effective policy for the caller, with current usage and headroom, so the session wizard
    can show the limits.

    Resolution is the per-field merge in app.domain.policy (user → group → org → global), the
    same module the admission gate uses. Caps are per user, so usage sums over the CALLER's
    active sessions regardless of group.
    """
    pol = await resolve_effective_policy(db, principal.user_id, group_id)
    if pol is None:
        return {"has_policy": False}

    row = (await db.execute(
        select(
            func.coalesce(func.sum(SessionModel.gpu_mem_mb), 0),
            func.coalesce(func.sum(SessionModel.gpu_cores), 0),
            func.coalesce(func.sum(SessionModel.cpu), 0),
            func.coalesce(func.sum(SessionModel.mem_gb), 0),
            func.coalesce(func.sum(SessionModel.disk_gb), 0),
            func.count(),
        ).where(
            SessionModel.owner_user_id == principal.user_id,
            SessionModel.status.in_(_ACTIVE_STATUSES),
            SessionModel.deleted_at.is_(None),
        )
    )).one()
    used = {
        "gpu_mem_mb": int(row[0] or 0), "gpu_cores": int(row[1] or 0), "cpu": int(row[2] or 0),
        "mem_gb": int(row[3] or 0), "storage_gb": int(row[4] or 0),
    }
    active = int(row[5] or 0)
    queued = int(await db.scalar(
        select(func.count()).select_from(QueueEntry)
        .join(SessionModel, SessionModel.id == QueueEntry.session_id)
        .where(SessionModel.owner_user_id == principal.user_id)
    ) or 0)
    limits = {k: int(pol.limits.get(k) or 0) for k in (
        "gpu_mem_mb", "gpu_cores", "cpu", "mem_gb", "storage_gb", "volume_gb",
    )}
    # volume_gb usage is the provisioned quota of the caller's personal volumes — the same sum the
    # volume-creation gate checks.
    used["volume_gb"] = int(await db.scalar(
        select(func.coalesce(func.sum(StorageVolume.quota_gb), 0)).where(
            StorageVolume.scope == "user",
            StorageVolume.scope_id == principal.user_id,
            StorageVolume.deleted_at.is_(None),
        )
    ) or 0)
    remaining = {k: (max(0, limits[k] - used.get(k, 0)) if limits[k] > 0 else None) for k in limits}
    return {
        "has_policy": True,
        # The most specific scope that contributed the concurrency cap (display only).
        "scope": pol.sources.get("max_concurrent", "global"),
        "max_concurrent": pol.max_concurrent,
        "max_queued": pol.max_queued,
        # Runtime/idle windows belong here too: this card is the ONLY place a user can learn
        # when their session will be reaped. 0 = unlimited (same semantics the reaper honours).
        "max_runtime_min": pol.max_runtime,
        "idle_timeout_sec": pol.idle_timeout,
        "limits": limits,
        "used": {**used, "active": active, "queued": queued},
        "remaining": remaining,
    }


@router.get("")
async def list_policies(
    page: Pagination = Depends(),
    scope: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    principal.require(action="policy.read")
    base = select(ResourcePolicy)
    if scope is not None:
        base = base.where(ResourcePolicy.scope == scope)
    if scope_id is not None:
        base = base.where(ResourcePolicy.scope_id == scope_id)

    # Tenant isolation: outside super_admin, only policies of the organizations (org_admin) and
    # groups (group_admin and above) the caller administers are visible. Global and user scoped
    # policies are super_admin only and never shown to anyone else.
    if "super_admin" not in principal.global_roles:
        admin_groups = {gid for gid in principal.memberships if rbac_allows(principal, "policy.read", gid)}
        conds = []
        if principal.org_admin_orgs:
            conds.append(
                (ResourcePolicy.scope == "org")
                & ResourcePolicy.scope_id.in_(principal.org_admin_orgs)
            )
        if admin_groups:
            conds.append(
                (ResourcePolicy.scope == "group")
                & ResourcePolicy.scope_id.in_(admin_groups)
            )
        if conds:
            base = base.where(or_(*conds))
        else:
            # A member with no administrative authority sees no policies at all.
            base = base.where(false())

    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    rows = (
        await db.scalars(
            base.order_by(ResourcePolicy.created_at.desc())
            .limit(page.size).offset(page.offset)
        )
    ).all()
    return {
        "data": [_policy_view(p) for p in rows],
        "pagination": {
            "page": page.page, "size": page.size, "total": total or 0,
            "total_pages": ((total or 0) + page.size - 1) // page.size,
        },
    }


# ── per-user quota requests: member asks, group admin approves, user-scope policy upserted ──

class ResourceRequestCreate(BaseModel):
    group_id: str | None = None       # approver scope; the caller's active group
    cpu: int | None = None            # target vCPU sum (absolute)
    mem_gb: int | None = None
    storage_gb: int | None = None
    gpu_mem_mb: int | None = None     # target VRAM MB sum
    gpu_cores: int | None = None      # target GPU core % sum
    note: str


def _rr_view(r: ResourceRequest, requester_name: str | None = None) -> dict:
    return {
        "id": r.id, "user_id": r.user_id, "requester_name": requester_name,
        "group_id": r.group_id, "cpu": r.cpu, "mem_gb": r.mem_gb, "storage_gb": r.storage_gb,
        "gpu_mem_mb": r.gpu_mem_mb, "gpu_cores": r.gpu_cores,
        "note": r.note, "status": r.status, "decided_by": r.decided_by,
        "decided_reason": r.decided_reason,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _can_decide_rr(principal: Principal, r: ResourceRequest) -> bool:  # noqa: ARG001
    # Group admins are out of the quota loop: hardware limits are the platform tier's to grant.
    return principal.global_role == "super_admin"


@router.post("/requests", status_code=status.HTTP_201_CREATED)
async def create_resource_request(
    body: ResourceRequestCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Ask for a bigger compute quota. At least one target, all positive; note required."""
    targets = {
        k: getattr(body, k) for k in ("cpu", "mem_gb", "storage_gb", "gpu_mem_mb", "gpu_cores")
    }
    if not any(v is not None for v in targets.values()):
        raise _Unprocessable("at least one target (cpu / mem_gb / storage_gb / gpu) is required")
    if any(v is not None and v <= 0 for v in targets.values()):
        raise _Unprocessable("targets must be positive")
    if not body.note.strip():
        raise _Unprocessable("note is required")
    r = ResourceRequest(
        id=ids.new("resourcerequest"), user_id=principal.user_id, group_id=body.group_id,
        cpu=body.cpu, mem_gb=body.mem_gb, storage_gb=body.storage_gb,
        gpu_mem_mb=body.gpu_mem_mb, gpu_cores=body.gpu_cores, note=body.note.strip(),
    )
    db.add(r)
    await db.flush()
    notifier = NotificationService(db)
    # Quota requests always land on the platform tier; group admins are not in this loop.
    approvers = await notifier.system_admins()
    line = " · ".join(f"{k.upper()} {v}" for k, v in targets.items() if v is not None)
    await notifier.notify(
        approvers, "resource_request", "Quota increase requested",
        f"A request for {line} has arrived.",
        params={"line": line, "requester": principal.user_id},
        request_id=r.id, requester_id=principal.user_id,
    )
    await AuditService(db).record(
        actor=principal.user_id, action="policy.request", target=r.id, result="pending",
        group_id=body.group_id, cpu=body.cpu, mem_gb=body.mem_gb, storage_gb=body.storage_gb,
        gpu_mem_mb=body.gpu_mem_mb, gpu_cores=body.gpu_cores,
    )
    await db.commit()
    return _rr_view(r)


@router.get("/requests")
async def list_resource_requests(
    box: str = Query(default="mine", pattern="^(mine|incoming)$"),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ResourceRequest, User.name).join(
        User, User.id == ResourceRequest.user_id, isouter=True
    ).order_by(ResourceRequest.created_at.desc()).limit(200)
    if box == "mine":
        stmt = stmt.where(ResourceRequest.user_id == principal.user_id)
    else:
        # The incoming box is platform-tier only; group admins have no quota inbox.
        if principal.global_role != "super_admin":
            return {"data": []}
    rows = (await db.execute(stmt)).all()
    return {"data": [_rr_view(r, name) for r, name in rows]}


@router.post("/requests/{request_id}/approve")
async def approve_resource_request(
    request_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Approve: upsert the requester's USER-scope policy with ONLY the granted limit keys.

    Everything not granted stays inherited (per-field most-specific merge), so the group/global
    defaults remain the single source for the rest of the policy.
    """
    r = await db.get(ResourceRequest, request_id, with_for_update=True)
    if r is None:
        raise NotFound("resource request", {"request_id": request_id})
    if r.status != "pending":
        raise _Conflict(f"request already {r.status}")
    if not _can_decide_rr(principal, r):
        raise Forbidden("you cannot decide this request")

    granted = {
        k: v
        for k, v in (
            ("cpu", r.cpu), ("mem_gb", r.mem_gb), ("storage_gb", r.storage_gb),
            ("gpu_mem_mb", r.gpu_mem_mb), ("gpu_cores", r.gpu_cores),
        )
        if v is not None
    }
    # The user-scope row INHERITS the policy that applied until now and overrides only what was
    # granted, so it is a complete policy on its own — a half-filled row reads as "unlimited" on
    # the policy screen and hides what the user actually gets. resolve_effective_policy already
    # puts the user scope first, so re-reading it keeps any values this row set earlier.
    eff = await resolve_effective_policy(db, r.user_id, r.group_id)
    inherited = dict(eff.limits) if eff else {}
    pol = (
        await db.scalars(
            select(ResourcePolicy).where(
                ResourcePolicy.scope == "user", ResourcePolicy.scope_id == r.user_id
            ).with_for_update()
        )
    ).first()
    if pol is None:
        pol = ResourcePolicy(id=ids.new("policy"), scope="user", scope_id=r.user_id, limits={})
        db.add(pol)
    pol.limits = {**inherited, **granted}
    if eff is not None:
        for fld in ("max_concurrent", "max_queued", "max_runtime", "idle_timeout"):
            setattr(pol, fld, getattr(eff, fld))
    r.status = "approved"
    r.decided_by = principal.user_id
    await db.flush()
    line = " · ".join(f"{k.upper()} {v}" for k, v in granted.items())
    await NotificationService(db).notify(
        [r.user_id], "resource_request_approved", "Quota increase approved",
        f"Your quota was raised: {line}.",
        params={"line": line}, request_id=r.id,
    )
    await AuditService(db).record(
        actor=principal.user_id, action="policy.request.approve", target=request_id,
        result="approved", user_id=r.user_id, **granted,
    )
    await db.commit()
    await _propagate_reaper_policy(db, "user", r.user_id)
    return _rr_view(r)


class _RRReject(BaseModel):
    reason: str


@router.post("/requests/{request_id}/reject")
async def reject_resource_request(
    request_id: str,
    body: _RRReject,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    r = await db.get(ResourceRequest, request_id, with_for_update=True)
    if r is None:
        raise NotFound("resource request", {"request_id": request_id})
    if r.status != "pending":
        raise _Conflict(f"request already {r.status}")
    if not _can_decide_rr(principal, r):
        raise Forbidden("you cannot decide this request")
    if not body.reason.strip():
        raise _Unprocessable("reason is required")
    r.status = "rejected"
    r.decided_by = principal.user_id
    r.decided_reason = body.reason.strip()
    await db.flush()
    await NotificationService(db).notify(
        [r.user_id], "resource_request_rejected", "Quota increase rejected",
        f"Reason: {body.reason.strip()}",
        params={"reason": body.reason.strip()}, request_id=r.id,
    )
    await AuditService(db).record(
        actor=principal.user_id, action="policy.request.reject", target=request_id,
        result="rejected", user_id=r.user_id, reason=body.reason.strip(),
    )
    await db.commit()
    return _rr_view(r)


@router.get("/{policy_id}")
async def get_policy(
    policy_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Fetch one policy, for deep links from the edit page."""
    principal.require(action="policy.read")
    policy = await db.get(ResourcePolicy, policy_id)
    if policy is None:
        raise NotFound("policy not found")
    if not _can_read_policy(principal, policy.scope, policy.scope_id):
        raise Forbidden("not permitted: policy.read (out of administered scope)")
    return _policy_view(policy)



async def _propagate_reaper_policy(db: AsyncSession, scope: str, scope_id: str) -> int:
    """Re-stamp idle/max-runtime on every ACTIVE session the (scope, scope_id) policy touches.

    Without this a policy edit reached live sessions only on their next resume; the reaper kept
    honouring the creation-time window. Best-effort per session — a cluster hiccup must not fail
    the policy write itself (it already committed)."""
    from app.cluster.crd import GShareSessionCRD
    from app.db.models import Membership as _M
    from app.db.models import Project as _P

    stmt = select(SessionModel).where(
        SessionModel.deleted_at.is_(None),
        SessionModel.status.in_(("pending", "preparing", "running", "paused")),
    )
    if scope == "user":
        stmt = stmt.where(SessionModel.owner_user_id == scope_id)
    elif scope == "group":
        member_ids = select(_M.user_id).where(_M.group_id == scope_id)
        stmt = stmt.where(SessionModel.owner_user_id.in_(member_ids))
    elif scope == "org":
        org_members = (
            select(_M.user_id)
            .join(_P, _P.id == _M.group_id)
            .where(_P.org_id == scope_id)
        )
        stmt = stmt.where(SessionModel.owner_user_id.in_(org_members))
    # scope == "global": every active session.
    sessions = (await db.scalars(stmt)).all()
    if not sessions:
        return 0
    crd = GShareSessionCRD(db=db)
    done = 0
    for sess in sessions:
        try:
            await crd.restamp_reaper(sess.cluster_id, sess.id, sess.owner_user_id, sess.group_id)
            done += 1
        except Exception:  # noqa: BLE001 — best-effort fan-out; the policy row is already saved
            log.warning("reaper restamp failed for %s", sess.id, exc_info=True)
    return done


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_policy(
    body: PolicyCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    if body.scope not in ("global", "org", "group", "user"):
        raise _Unprocessable("scope must be global|org|group|user")
    if body.scope == "global":
        body.scope_id = "*"          # the global policy uses a single sentinel scope_id
    # Authorize by scope; binding on group_id alone would leak the org, user, and global scopes.
    await _assert_policy_perm(principal, "policy.create", body.scope, body.scope_id, db)
    for fld in ("max_concurrent", "max_queued", "max_runtime_min", "idle_timeout_sec"):
        if getattr(body, fld) < 0:
            raise _Unprocessable(f"{fld} must be >= 0")

    # (scope, scope_id) UNIQUE -> 409 on duplicate.
    existing = await db.scalar(
        select(ResourcePolicy).where(
            ResourcePolicy.scope == body.scope,
            ResourcePolicy.scope_id == body.scope_id,
        )
    )
    if existing is not None:
        raise _Conflict("a policy for this (scope, scope_id) already exists")

    # The ResourcePolicy model has dedicated columns for the GPU-session limits and a JSONB
    # `limits` blob; CPU-session limits + the quota limits live in the blob.
    limits = dict(body.limits or {})
    limits["cpu_session_max_concurrent"] = body.cpu_session_max_concurrent
    limits["cpu_session_max_runtime_min"] = body.cpu_session_max_runtime_min
    limits["cpu_session_idle_timeout_sec"] = body.cpu_session_idle_timeout_sec

    policy = ResourcePolicy(
        id=ids.new("policy"),
        scope=body.scope,
        scope_id=body.scope_id,
        max_concurrent=body.max_concurrent,
        max_queued=body.max_queued,
        max_runtime=body.max_runtime_min,
        idle_timeout=body.idle_timeout_sec,
        limits=limits,
    )
    db.add(policy)
    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="policy.create", target=policy.id,
        scope=body.scope, scope_id=body.scope_id,
    )
    await db.commit()
    await _propagate_reaper_policy(db, body.scope, body.scope_id)
    return _policy_view(policy)


@router.patch("/{policy_id}")
async def update_policy(
    policy_id: str,
    body: PolicyUpdate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    policy = await db.get(ResourcePolicy, policy_id)
    if policy is None:
        raise NotFound("policy not found")
    await _assert_policy_perm(principal, "policy.update", policy.scope, policy.scope_id, db)

    if body.max_concurrent is not None:
        policy.max_concurrent = body.max_concurrent
    if body.max_queued is not None:
        policy.max_queued = body.max_queued
    if body.max_runtime_min is not None:
        policy.max_runtime = body.max_runtime_min
    if body.idle_timeout_sec is not None:
        policy.idle_timeout = body.idle_timeout_sec

    limits = dict(policy.limits or {})
    if body.cpu_session_max_concurrent is not None:
        limits["cpu_session_max_concurrent"] = body.cpu_session_max_concurrent
    if body.cpu_session_max_runtime_min is not None:
        limits["cpu_session_max_runtime_min"] = body.cpu_session_max_runtime_min
    if body.cpu_session_idle_timeout_sec is not None:
        limits["cpu_session_idle_timeout_sec"] = body.cpu_session_idle_timeout_sec
    if body.limits is not None:
        for k in _PUBLIC_LIMIT_KEYS:
            if k in body.limits:
                limits[k] = body.limits[k]
    policy.limits = limits

    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="policy.update", target=policy.id,
    )
    await db.commit()
    await _propagate_reaper_policy(db, policy.scope, policy.scope_id)
    return _policy_view(policy)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    policy_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Delete a resource policy. The global policy is super_admin only; the rest follow
    policy.delete, which admits super_admin, org_admin, and group_admin."""
    policy = await db.get(ResourcePolicy, policy_id)
    if policy is None:
        raise NotFound("policy not found")
    await _assert_policy_perm(principal, "policy.delete", policy.scope, policy.scope_id, db)

    scope_snapshot, scope_id_snapshot = policy.scope, policy.scope_id
    await db.delete(policy)
    await AuditService(db).record(
        actor=principal.user_id, action="policy.delete", target=policy_id,
        scope=scope_snapshot, scope_id=scope_id_snapshot,
    )
    await db.commit()
    # The now-effective policy (whatever the next scope in the chain says) reaches live sessions.
    await _propagate_reaper_policy(db, scope_snapshot, scope_id_snapshot)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
