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
from app.db.base import get_db
from app.db.models import Project, QueueEntry, ResourcePolicy
from app.db.models import Session as SessionModel
from app.domain.audit_service import AuditService

router = APIRouter(prefix="/resource-policies", tags=["policies"])


class _Unprocessable(DomainError):
    code, http = "validation_failed", 422


class _Conflict(DomainError):
    code, http = "conflict", 409


def _assert_policy_perm(principal: Principal, action: str, scope: str, scope_id: str) -> None:
    """Authorize by the policy's own scope. Binding on group_id alone would leak the org, user, and
    global scopes.

    global: super_admin only. org: super_admin, or that organization's org_admin. group:
    super_admin, org_admin, or that group's group_admin. user: super_admin only — per-user policies
    belong to system administrators. """
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
    # The user scope, and anything else, is super_admin only.
    raise Forbidden(f"not permitted: {action} (user-scoped policy is super_admin only)")


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
        "limits": {
            k: v for k, v in limits.items()
            if k in ("cpu", "mem_gb", "gpu_mem_mb", "gpu_cores", "storage_gb")
        },
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

    Resolution order: user, then group when a group_id is given, then the organization derived from
    that group, then global. Usage is the sum over active sessions in the same scope.
    """
    # 1. User
    pol = (await db.scalars(select(ResourcePolicy).where(
        ResourcePolicy.scope == "user", ResourcePolicy.scope_id == principal.user_id))).first()
    # 2. Group, then 3. organization (derived from the group)
    if pol is None and group_id:
        pol = (await db.scalars(select(ResourcePolicy).where(
            ResourcePolicy.scope == "group", ResourcePolicy.scope_id == group_id))).first()
        if pol is None:
            project = await db.get(Project, group_id)
            if project is not None and project.org_id:
                pol = (await db.scalars(select(ResourcePolicy).where(
                    ResourcePolicy.scope == "org", ResourcePolicy.scope_id == project.org_id))).first()
    # 4. Global
    if pol is None:
        pol = (await db.scalars(select(ResourcePolicy).where(ResourcePolicy.scope == "global"))).first()
    if pol is None:
        return {"has_policy": False}

    scope_col = SessionModel.group_id if group_id else SessionModel.owner_user_id
    scope_val = group_id if group_id else principal.user_id
    row = (await db.execute(
        select(
            func.coalesce(func.sum(SessionModel.gpu_mem_mb), 0),
            func.coalesce(func.sum(SessionModel.gpu_cores), 0),
            func.coalesce(func.sum(SessionModel.cpu), 0),
            func.coalesce(func.sum(SessionModel.mem_gb), 0),
            func.coalesce(func.sum(SessionModel.disk_gb), 0),
            func.count(),
        ).where(
            scope_col == scope_val,
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
        .where(scope_col == scope_val)
    ) or 0)
    limits = {k: int(pol.limits.get(k) or 0) for k in ("gpu_mem_mb", "gpu_cores", "cpu", "mem_gb", "storage_gb")}
    remaining = {k: (max(0, limits[k] - used[k]) if limits[k] > 0 else None) for k in limits}
    return {
        "has_policy": True,
        "scope": pol.scope,
        "max_concurrent": pol.max_concurrent,
        "max_queued": pol.max_queued,
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
    _assert_policy_perm(principal, "policy.create", body.scope, body.scope_id)
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
    _assert_policy_perm(principal, "policy.update", policy.scope, policy.scope_id)

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
        for k in ("cpu", "mem_gb", "gpu_mem_mb", "gpu_cores", "storage_gb"):
            if k in body.limits:
                limits[k] = body.limits[k]
    policy.limits = limits

    await db.flush()
    await AuditService(db).record(
        actor=principal.user_id, action="policy.update", target=policy.id,
    )
    await db.commit()
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
    _assert_policy_perm(principal, "policy.delete", policy.scope, policy.scope_id)

    await db.delete(policy)
    await AuditService(db).record(
        actor=principal.user_id, action="policy.delete", target=policy_id,
        scope=policy.scope, scope_id=policy.scope_id,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
