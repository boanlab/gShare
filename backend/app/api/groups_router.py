"""Organizations / Projects(groups) / Memberships router.

In the UI a project is called a **group**. The hierarchy is organization -> group (project) ->
membership. Creating a project can also create its default wallet (CreditWallet with
owner_type=project). The last admin membership — group_admin or org_admin — cannot be removed; the
attempt returns 409.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.auth.rbac import Principal
from app.core import ids
from app.core.errors import DomainError, Forbidden, InsufficientCredit, NotFound
from app.db.base import get_db
from app.db.models import (
    AuditLog,
    Cluster,
    CreditTransaction,
    CreditWallet,
    Membership,
    NodePool,
    NodePoolGrant,
    Organization,
    Project,
    User,
)
from app.db.models import Session as SessionModel
from app.domain.audit_service import AuditService
from app.domain.notification_service import NotificationService
from app.domain.welcome_credit import grant_welcome_credit

router = APIRouter(tags=["groups"])

_ORG_STATUSES = {"active", "inactive"}
_PROJECT_STATUSES = {"active", "inactive", "archived"}
# Roles grantable through a group membership. org_admin is appointed at the organization level and
# is deliberately absent here.
_MEMBERSHIP_ROLES = {"group_admin", "member", "guest"}
_ADMIN_ROLES = {"group_admin"}  # protected by the last-admin invariant; org_admin lives at org level
# Role ranking used to validate grants: guest < member < group_admin < org_admin.
# Nobody may grant a role above their own, and org_admin cannot be granted here at all — that is
# super_admin's org.set_admin.
_ROLE_RANK = {"guest": 0, "member": 1, "group_admin": 2, "org_admin": 3}


def _guard_grant_rank(principal: Principal, group_id: str, granted_role: str) -> None:
    """A non-super grantor may neither grant org_admin nor grant a role above their own group role.

    This also blocks self-promotion through the update path."""
    if "super_admin" in principal.global_roles:
        return  # super_admin may grant any permitted role
    if granted_role == "org_admin":
        raise Forbidden(
            "org_admin cannot be granted here; appointing organization admins is super_admin only.",
            {"role": granted_role},
        )
    caller_role = principal.memberships.get(group_id)
    caller_rank = _ROLE_RANK.get(caller_role, -1)
    if _ROLE_RANK.get(granted_role, -1) > caller_rank:
        raise Forbidden(
            "you cannot grant a role higher than your own.",
            {"role": granted_role, "caller_role": caller_role},
        )


class _Conflict(DomainError):
    code, http = "conflict", 409


class _LastAdmin(DomainError):
    code, http = "last_admin", 409


class _Validation(DomainError):
    code, http = "validation_failed", 422


# ── request bodies ──
class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    status: str | None = None
    # Also create a dedicated node pool named after the organization, granted to it.
    create_node_pool: bool = False


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    status: str | None = None


class ProjectCreate(BaseModel):
    org_id: str
    name: str = Field(min_length=1, max_length=80)
    status: str | None = None
    create_project_wallet: bool = True
    # Credits minted into each new member's personal wallet on first join (0 = off).
    default_member_credit: str | None = None
    # Also create a dedicated node pool named after the group, granted to it.
    create_node_pool: bool = False


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    status: str | None = None
    default_member_credit: str | None = None


def _parse_credit(v: str | None) -> Decimal:
    if v is None or str(v).strip() == "":
        return Decimal("0")
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError) as exc:
        raise _Validation("invalid credit amount", {"value": v}) from exc
    if d < 0:
        raise _Validation("credit amount must be >= 0", {"value": v})
    return d


class MembershipCreate(BaseModel):
    user_id: str
    role: str
    expires_at: datetime | None = None
    grant_credit: str | None = None


class MembershipPatch(BaseModel):
    role: str


# ── serializers ──
async def _project_wallet_id(db: AsyncSession, group_id: str) -> str | None:
    return await db.scalar(
        select(CreditWallet.id).where(
            CreditWallet.owner_type == "group", CreditWallet.owner_id == group_id
        )
    )


def _org_out(o: Organization, group_count: int = 0, user_count: int = 0) -> dict[str, Any]:
    return {
        "id": o.id,
        "name": o.name,
        "status": o.status,
        "created_at": o.created_at,
        "group_count": group_count,   # number of live groups
        "user_count": user_count,     # distinct members across those groups
    }


async def _project_out(db: AsyncSession, p: Project, member_count: int | None = None) -> dict[str, Any]:
    # Include org_name so a group_admin, who has no org.read permission, can still display it.
    org_name = await db.scalar(select(Organization.name).where(Organization.id == p.org_id))
    return {
        "id": p.id,
        "org_id": p.org_id,
        "org_name": org_name,
        "name": p.name,
        "status": p.status,
        "default_member_credit": str(p.default_member_credit or 0),
        "created_at": p.created_at,
        "wallet_id": await _project_wallet_id(db, p.id),
        # Group-membership count (org_admin rows have group_id NULL and are not counted).
        "member_count": member_count if member_count is not None else int(await db.scalar(
            select(func.count()).select_from(Membership).where(Membership.group_id == p.id)
        ) or 0),
    }


def _membership_out(m: Membership, user_name: str | None) -> dict[str, Any]:
    return {
        "id": m.id,
        "user_id": m.user_id,
        "user_name": user_name or m.user_id,
        "group_id": m.group_id,
        "org_id": m.org_id,
        "role": m.role,
        "expires_at": m.expires_at,
        "created_at": m.created_at,
    }


# ── organizations - ──
@router.get("/organizations")
async def list_organizations(
    pagination: Pagination = Depends(),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List organizations. super_admin sees all; org_admin sees only their own (tenant
    isolation)."""
    principal.require(action="org.read")
    base = select(Organization).where(Organization.deleted_at.is_(None))
    if principal.global_role != "super_admin":
        # org_admin: restricted to the organizations they administer; none means an empty result.
        base = base.where(Organization.id.in_(principal.org_admin_orgs or {"__none__"}))
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.scalars(
            base.order_by(Organization.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.size)
        )
    ).all()

    # Aggregate group counts and distinct member counts for this page in one pass, over live groups.
    org_ids = [o.id for o in rows]
    group_counts: dict[str, int] = {}
    user_counts: dict[str, int] = {}
    if org_ids:
        for oid, cnt in (
            await db.execute(
                select(Project.org_id, func.count(Project.id))
                .where(Project.org_id.in_(org_ids), Project.deleted_at.is_(None))
                .group_by(Project.org_id)
            )
        ).all():
            group_counts[oid] = int(cnt)
        for oid, cnt in (
            await db.execute(
                select(Project.org_id, func.count(func.distinct(Membership.user_id)))
                .join(Membership, Membership.group_id == Project.id)
                .join(User, User.id == Membership.user_id)
                .where(Project.org_id.in_(org_ids), Project.deleted_at.is_(None),
                       User.deleted_at.is_(None))
                .group_by(Project.org_id)
            )
        ).all():
            user_counts[oid] = int(cnt)

    return {
        "data": [_org_out(o, group_counts.get(o.id, 0), user_counts.get(o.id, 0)) for o in rows],
        "pagination": _page(pagination, total),
    }


@router.get("/organizations/{org_id}")
async def get_organization(
    org_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Fetch one organization, for deep links from the edit and admin pages."""
    principal.require(action="org.read")
    # Tenant isolation: the list is already filtered, but the single GET has to be blocked too,
    # otherwise an org_admin could read another organization by id.
    if principal.global_role != "super_admin" and org_id not in principal.org_admin_orgs:
        raise Forbidden("not permitted: you cannot access another organization")
    o = await db.get(Organization, org_id)
    if o is None or o.deleted_at is not None:
        raise NotFound("organization not found", {"org_id": org_id})
    return _org_out(o)


async def _auto_create_pool(
    db: AsyncSession, actor: str, scope: str, scope_id: str, name: str,
) -> str | None:
    """Best-effort dedicated pool for a freshly created tenant, granted to it.

    Placed on the sole (first live) cluster; with no live cluster the pool is skipped rather than
    failing the org/group creation. A pool-name clash in that cluster gets a short id suffix."""
    cluster = (await db.execute(
        select(Cluster).where(Cluster.deleted_at.is_(None)).order_by(Cluster.created_at).limit(1)
    )).scalars().first()
    if cluster is None:
        return None
    pool_name = name
    clash = (await db.execute(select(NodePool.id).where(
        NodePool.cluster_id == cluster.id, NodePool.name == pool_name
    ))).first()
    if clash:
        pool_name = f"{name}-{scope_id[-4:].lower()}"
    pool = NodePool(
        id=ids.new("pool"), cluster_id=cluster.id, name=pool_name,
        description=None, kind="dedicated",
    )
    db.add(pool)
    await db.flush()
    db.add(NodePoolGrant(
        id=ids.new("pool_grant"), pool_id=pool.id, scope=scope, scope_id=scope_id,
        created_by=actor,
    ))
    await AuditService(db).record(
        actor=actor, action="pool.create", target=pool.id, result="ok",
        cluster_id=cluster.id, auto_created=True, granted_to=f"{scope}:{scope_id}",
        changes={"name": {"from": None, "to": pool_name}, "kind": {"from": None, "to": "dedicated"}},
    )
    return pool.id


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: OrgCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Create an organization. super_admin only."""
    principal.require(action="org.create")
    if body.status is not None and body.status not in _ORG_STATUSES:
        raise _Validation("invalid status", {"status": body.status})
    org = Organization(id=ids.new("org"), name=body.name, status=body.status or "active")
    db.add(org)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _Conflict("organization name already exists", {"name": body.name}) from exc

    # Create the organization wallet: the top of the credit hierarchy, topped up by super_admin and
    # allocated down to groups by org_admin.
    db.add(
        CreditWallet(
            id=ids.new("wallet"),
            owner_type="org",
            owner_id=org.id,
            balance=Decimal("0"),
            reserved=Decimal("0"),
        )
    )
    if body.create_node_pool:
        await _auto_create_pool(db, principal.user_id, "org", org.id, org.name)
    await AuditService(db).record(
        actor=principal.user_id, action="org.create", target=org.id, result="ok", name=org.name
    )
    await db.commit()
    return _org_out(org)


async def _load_org(db: AsyncSession, org_id: str) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None or org.deleted_at is not None:
        raise NotFound("organization", {"org_id": org_id})
    return org


@router.patch("/organizations/{org_id}")
async def update_organization(
    org_id: str,
    body: OrgUpdate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Update an organization's name or status. super_admin only."""
    principal.require(action="org.update")
    org = await _load_org(db, org_id)
    changes: dict[str, Any] = {}
    if body.status is not None and body.status != org.status:
        if body.status not in _ORG_STATUSES:
            raise _Validation("invalid status", {"status": body.status})
        changes["status"] = {"from": org.status, "to": body.status}
        old_status = org.status
        org.status = body.status
        # An organization's status flip runs down the hierarchy: every live group under it, then
        # (via the group cascade rules) their sole-membership users.
        gids = [gid for (gid,) in (await db.execute(
            select(Project.id).where(Project.org_id == org_id, Project.deleted_at.is_(None))
        )).all()]
        if body.status == "inactive" and old_status == "active":
            for gid in gids:
                await db.execute(
                    sa_update(Project).where(Project.id == gid, Project.status == "active")
                    .values(status="inactive")
                )
            n = await _cascade_group_users(db, gids, "inactive", principal.user_id)
            if n:
                changes["cascade_users"] = {"from": 0, "to": n}
            if gids:
                changes["cascade_groups"] = {"from": 0, "to": len(gids)}
        elif body.status == "active" and old_status == "inactive":
            for gid in gids:
                await db.execute(
                    sa_update(Project).where(Project.id == gid, Project.status == "inactive")
                    .values(status="active")
                )
            n = await _cascade_group_users(db, gids, "active", principal.user_id)
            if n:
                changes["cascade_users"] = {"from": 0, "to": n}
            if gids:
                changes["cascade_groups"] = {"from": 0, "to": len(gids)}
    if body.name is not None and body.name != org.name:
        changes["name"] = {"from": org.name, "to": body.name}
        org.name = body.name
    if changes:
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise _Conflict("organization name already exists", {"name": body.name}) from exc
        await AuditService(db).record(
            actor=principal.user_id, action="org.update", target=org.id, result="ok", changes=changes
        )
    await db.commit()
    return _org_out(org)


@router.delete("/organizations/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    org_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete an organization. super_admin only; 409 while live groups remain, to avoid
    orphans."""
    principal.require(action="org.delete")
    org = await _load_org(db, org_id)
    active_projects = await db.scalar(
        select(func.count()).select_from(
            select(Project).where(
                Project.org_id == org_id, Project.deleted_at.is_(None)
            ).subquery()
        )
    )
    if active_projects:
        raise _Conflict(
            "organization has active groups", {"org_id": org_id, "active_groups": int(active_projects)}
        )
    org.deleted_at = datetime.now(UTC)
    await AuditService(db).record(
        actor=principal.user_id, action="org.delete", target=org.id, result="ok"
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Organization admins: organization-level memberships with role=org_admin ──
class OrgAdminCreate(BaseModel):
    user_id: str


def _org_admin_out(m: Membership, user_name: str | None, email: str | None) -> dict[str, Any]:
    return {
        "id": m.id,
        "user_id": m.user_id,
        "user_name": user_name or m.user_id,
        "email": email,
        "org_id": m.org_id,
        "role": m.role,
        "created_at": m.created_at,
    }


@router.get("/organizations/{org_id}/admins")
async def list_org_admins(
    org_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List an organization's admins. super_admin, or an org_admin of that organization."""
    if principal.global_role != "super_admin" and org_id not in principal.org_admin_orgs:
        principal.require(action="org.set_admin")  # -> 403
    await _load_org(db, org_id)
    rows = (
        await db.execute(
            select(Membership, User.name, User.email)
            .join(User, User.id == Membership.user_id, isouter=True)
            .where(Membership.org_id == org_id, Membership.role == "org_admin",
                   User.deleted_at.is_(None))
            .order_by(Membership.created_at.asc())
        )
    ).all()
    return {"data": [_org_admin_out(m, name, email) for (m, name, email) in rows]}


@router.post("/organizations/{org_id}/admins", status_code=status.HTTP_201_CREATED)
async def add_org_admin(
    org_id: str,
    body: OrgAdminCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Appoint an organization admin. super_admin only."""
    principal.require(action="org.set_admin")
    await _load_org(db, org_id)
    user = await db.get(User, body.user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound("user", {"user_id": body.user_id})

    dup = await db.scalar(
        select(Membership.id).where(
            Membership.user_id == body.user_id,
            Membership.org_id == org_id,
            Membership.role == "org_admin",
        )
    )
    if dup is not None:
        raise _Conflict("user already an org admin", {"user_id": body.user_id})

    m = Membership(id=ids.new("membership"), user_id=body.user_id, org_id=org_id, role="org_admin")
    db.add(m)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _Conflict("user already an org admin", {"user_id": body.user_id}) from exc
    await NotificationService(db).notify(
        [body.user_id], "org_admin_added", "Appointed organization admin",
        "You have been appointed an administrator of this organization.",
        org_id=org_id, role="org_admin",
    )
    await AuditService(db).record(
        actor=principal.user_id, action="org.admin.add", target=m.id, result="ok",
        org_id=org_id, user_id=body.user_id, role="org_admin",
    )
    await db.commit()
    return _org_admin_out(m, user.name, user.email)


@router.delete("/organizations/{org_id}/admins/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_org_admin(
    org_id: str,
    user_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Remove an organization admin. super_admin only."""
    principal.require(action="org.set_admin")
    m = await db.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.org_id == org_id,
            Membership.role == "org_admin",
        )
    )
    if m is None:
        raise NotFound("membership", {"org_id": org_id, "user_id": user_id})
    await db.delete(m)
    await AuditService(db).record(
        actor=principal.user_id, action="org.admin.remove", target=m.id, result="ok",
        org_id=org_id, user_id=user_id, role="org_admin",
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── projects / groups - ──
@router.get("/projects")
async def list_projects(
    pagination: Pagination = Depends(),
    org_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List groups. super_admin sees all; everyone else sees only the groups they belong to."""
    principal.require(action="group.read")
    base = select(Project).where(Project.deleted_at.is_(None))
    if org_id is not None:
        base = base.where(Project.org_id == org_id)
    # Non-super callers are limited to their own memberships. An org_admin sees the whole
    # organization, but membership-based filtering already covers that.
    if principal.global_role != "super_admin":
        visible = list(principal.memberships.keys())
        base = base.where(Project.id.in_(visible)) if visible else base.where(Project.id.is_(None))
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.scalars(
            base.order_by(Project.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.size)
        )
    ).all()
    # Batch the member counts for the page: one grouped query instead of one per row.
    gids = [p.id for p in rows]
    mcounts: dict[str, int] = {}
    if gids:
        mrows = (await db.execute(
            select(Membership.group_id, func.count())
            .where(Membership.group_id.in_(gids))
            .group_by(Membership.group_id)
        )).all()
        mcounts = {gid: int(c) for gid, c in mrows}
    return {
        "data": [await _project_out(db, p, member_count=mcounts.get(p.id, 0)) for p in rows],
        "pagination": _page(pagination, total),
    }


@router.get("/projects/{group_id}")
async def get_project(
    group_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Fetch one group, for deep links from the edit, admin, and delete pages."""
    p = await db.get(Project, group_id)
    if p is None or p.deleted_at is not None:
        raise NotFound("project not found", {"group_id": group_id})
    # Tenant isolation: super_admin sees all, org_admin their own organization, everyone else only
    # the groups they belong to.
    if principal.global_role != "super_admin" \
            and p.org_id not in principal.org_admin_orgs \
            and group_id not in principal.memberships:
        raise Forbidden("not permitted: you cannot access a group you do not belong to")
    return await _project_out(db, p)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Create a group along with its default wallet. super_admin or org_admin."""
    principal.require(action="group.create", group_id=None)
    # Tenant isolation: a non-super org_admin may only create groups in their own organization.
    # group.create carries no organization binding, so it is enforced here.
    if principal.global_role != "super_admin" and body.org_id not in principal.org_admin_orgs:
        raise Forbidden("not permitted: cannot create a group outside your organization")
    if body.status is not None and body.status not in _PROJECT_STATUSES:
        raise _Validation("invalid status", {"status": body.status})
    org = await db.get(Organization, body.org_id)
    if org is None or org.deleted_at is not None:
        raise NotFound("organization", {"org_id": body.org_id})

    project = Project(
        id=ids.new("group"), org_id=body.org_id, name=body.name, status=body.status or "active",
        default_member_credit=_parse_credit(body.default_member_credit),
    )
    db.add(project)
    await db.flush()

    if body.create_project_wallet:
        db.add(
            CreditWallet(
                id=ids.new("wallet"),
                owner_type="group",
                owner_id=project.id,
                balance=Decimal("0"),
                reserved=Decimal("0"),
            )
        )
    if body.create_node_pool:
        await _auto_create_pool(db, principal.user_id, "group", project.id, project.name)
    await AuditService(db).record(
        actor=principal.user_id, action="group.create", target=project.id, result="ok",
        org_id=body.org_id, name=project.name, wallet=body.create_project_wallet,
    )
    await db.commit()
    return await _project_out(db, project)



async def _cascade_group_users(
    db: AsyncSession, group_ids: list[str], to_status: str, actor: str,
) -> int:
    """Deactivate/reactivate the users of the given groups, honouring multi-membership.

    Deactivate: only users whose EVERY OTHER group membership is in an inactive/archived or
    deleted group go inactive — someone also in an active department keeps working.
    Reactivate: only users this cascade froze (marked via the audit reason on their row change —
    approximated here as: user is inactive AND has no other reason recorded, i.e. they are members
    of the reactivated group and hold no membership in another ACTIVE group that would have kept
    them active). Individually-deactivated users therefore stay put unless this group was their
    only home. Returns the number of users changed."""
    if not group_ids:
        return 0
    member_ids = [uid for (uid,) in (await db.execute(
        select(Membership.user_id).where(Membership.group_id.in_(group_ids)).distinct()
    )).all()]
    if not member_ids:
        return 0
    # Which of those users hold a membership in some OTHER group that is still active?
    others = (await db.execute(
        select(Membership.user_id).join(Project, Project.id == Membership.group_id).where(
            Membership.user_id.in_(member_ids),
            Membership.group_id.notin_(group_ids),
            Project.deleted_at.is_(None),
            Project.status == "active",
        ).distinct()
    )).all()
    keep = {uid for (uid,) in others}
    targets = [uid for uid in member_ids if uid not in keep]
    if not targets:
        return 0
    users = (await db.scalars(select(User).where(User.id.in_(targets)))).all()
    changed = 0
    for u in users:
        if to_status == "inactive" and u.status == "active":
            u.status = "inactive"
            changed += 1
            await AuditService(db).record(
                actor=actor, action="user.update", target=u.id, result="ok",
                changes={"status": {"from": "active", "to": "inactive"}},
                reason="group_deactivated",
            )
        elif to_status == "active" and u.status == "inactive":
            # Reactivation only lifts users this cascade plausibly froze: their last status change
            # to inactive carries reason=group_deactivated in the audit trail.
            last = (await db.execute(
                select(AuditLog.detail).where(
                    AuditLog.action == "user.update", AuditLog.target == u.id,
                ).order_by(AuditLog.created_at.desc()).limit(1)
            )).first()
            reason = (last[0] or {}).get("reason") if last else None
            if reason == "group_deactivated":
                u.status = "active"
                changed += 1
                await AuditService(db).record(
                    actor=actor, action="user.update", target=u.id, result="ok",
                    changes={"status": {"from": "inactive", "to": "active"}},
                    reason="group_reactivated",
                )
    return changed


@router.patch("/projects/{group_id}")
async def update_project(
    group_id: str,
    body: ProjectPatch,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Update a group's name or archived status. super_admin or group_admin."""
    principal.require(action="group.update", group_id=group_id)
    project = await _load_project(db, group_id)
    changes: dict[str, Any] = {}
    cascade_users = 0
    if body.status is not None and body.status != project.status:
        if body.status not in _PROJECT_STATUSES:
            raise _Validation("invalid status", {"status": body.status})
        changes["status"] = {"from": project.status, "to": body.status}
        old_status = project.status
        project.status = body.status
        # Deactivating a department freezes its members too (unless they belong to another
        # active department); reactivating lifts exactly the users this cascade froze.
        if body.status in ("inactive", "archived") and old_status == "active":
            cascade_users = await _cascade_group_users(db, [group_id], "inactive", principal.user_id)
        elif body.status == "active" and old_status in ("inactive", "archived"):
            cascade_users = await _cascade_group_users(db, [group_id], "active", principal.user_id)
        if cascade_users:
            changes["cascade_users"] = {"from": 0, "to": cascade_users}
    if body.name is not None and body.name != project.name:
        changes["name"] = {"from": project.name, "to": body.name}
        project.name = body.name
    if body.default_member_credit is not None:
        new_credit = _parse_credit(body.default_member_credit)
        if new_credit != project.default_member_credit:
            changes["default_member_credit"] = {
                "from": str(project.default_member_credit), "to": str(new_credit)}
            project.default_member_credit = new_credit
    if changes:
        await db.flush()
        await AuditService(db).record(
            actor=principal.user_id, action="group.update", target=group_id, result="ok",
            changes=changes,
        )
    await db.commit()
    return await _project_out(db, project)


@router.delete("/projects/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    group_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a group. super_admin or org_admin; 409 while live sessions remain.

    Memberships are removed with it, so a soft-deleted group leaves no permissions behind. The
    group wallet is preserved.
    """
    principal.require(action="group.delete", group_id=group_id)
    project = await _load_project(db, group_id)

    live = await db.scalar(
        select(func.count())
        .select_from(SessionModel)
        .where(
            SessionModel.group_id == group_id,
            SessionModel.deleted_at.is_(None),
            SessionModel.status.notin_(["terminated", "error"]),
        )
    )
    if live and int(live) > 0:
        raise _Conflict("group has running sessions", {"group_id": group_id, "running": int(live)})

    # Remove the group memberships to prevent permission leakage. org_admin memberships are bound to
    # the organization, not the group, so they stay.
    members = (
        await db.scalars(select(Membership).where(Membership.group_id == group_id))
    ).all()
    for m in members:
        await db.delete(m)

    project.deleted_at = datetime.now(UTC)
    await AuditService(db).record(
        actor=principal.user_id, action="group.delete", target=group_id, result="ok",
        name=project.name, removed_members=len(members),
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── memberships - ──
@router.get("/projects/{group_id}/memberships")
async def list_memberships(
    group_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List a group's memberships. super_admin or group_admin."""
    principal.require(action="membership.read", group_id=group_id)
    await _load_project(db, group_id)
    rows = (
        await db.execute(
            select(Membership, User.name)
            .join(User, User.id == Membership.user_id, isouter=True)
            .where(Membership.group_id == group_id, User.deleted_at.is_(None))
            .order_by(Membership.created_at.asc())
        )
    ).all()
    return {"data": [_membership_out(m, name) for (m, name) in rows]}


@router.post("/projects/{group_id}/memberships", status_code=status.HTTP_201_CREATED)
async def add_membership(
    group_id: str,
    body: MembershipCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Add a member or grant a role. A guest must carry expires_at. super_admin or group_admin."""
    principal.require(action="membership.create", group_id=group_id)
    group = await _load_project(db, group_id)
    if body.role not in _MEMBERSHIP_ROLES:
        raise _Validation("invalid role", {"role": body.role})
    _guard_grant_rank(principal, group_id, body.role)
    if body.role == "guest" and body.expires_at is None:
        raise _Validation("guest membership requires expires_at", {"role": "guest"})

    user = await db.get(User, body.user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound("user", {"user_id": body.user_id})

    # (user, project) is unique: 409 when they are already a member.
    dup = await db.scalar(
        select(Membership.id).where(
            Membership.user_id == body.user_id, Membership.group_id == group_id
        )
    )
    if dup is not None:
        raise _Conflict("user already a member", {"user_id": body.user_id})

    grant = None
    if body.grant_credit:
        try:
            grant = str(Decimal(body.grant_credit))
        except (InvalidOperation, ValueError) as exc:
            raise _Validation("invalid grant_credit", {"grant_credit": body.grant_credit}) from exc

    m = Membership(
        id=ids.new("membership"),
        user_id=body.user_id,
        group_id=group_id,
        role=body.role,
        expires_at=body.expires_at,
    )
    db.add(m)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _Conflict("user already a member", {"user_id": body.user_id}) from exc

    # Group-configured welcome credit: minted once per (group, user), like the monthly refill.
    await grant_welcome_credit(db, body.user_id, group)

    # A welcome grant actually MOVES credit (group wallet -> personal wallet). It used to be
    # recorded in the audit log without moving anything — a silent lie; now a short group wallet
    # rejects the request instead.
    if grant is not None and Decimal(grant) > 0:
        amount = Decimal(grant)
        src = await db.scalar(
            select(CreditWallet)
            .where(CreditWallet.owner_type == "group", CreditWallet.owner_id == group_id)
            .with_for_update()
        )
        dst = await db.scalar(
            select(CreditWallet)
            .where(CreditWallet.owner_type == "user", CreditWallet.owner_id == body.user_id)
            .with_for_update()
        )
        if src is None or dst is None:
            raise _Validation("grant_credit needs both the group and the user wallet",
                              {"group_id": group_id, "user_id": body.user_id})
        available = src.balance - src.reserved
        if available < amount:
            raise InsufficientCredit(available=available, need=amount)
        src.balance -= amount
        src.version += 1
        dst.balance += amount
        dst.version += 1
        db.add_all([
            CreditTransaction(
                id=ids.new("transaction"), wallet_id=src.id, type="adjust", amount=-amount,
                balance_after=src.balance, ref=f"member-grant-out:{dst.id}",
                idempotency_key=f"member-grant:{m.id}:out",
            ),
            CreditTransaction(
                id=ids.new("transaction"), wallet_id=dst.id, type="adjust", amount=amount,
                balance_after=dst.balance, ref=f"member-grant-in:{src.id}",
                idempotency_key=f"member-grant:{m.id}:in",
            ),
        ])

    await NotificationService(db).notify(
        [body.user_id], "membership_added", "Added to a group",
        f"You were added to the group with the '{body.role}' role.",
        params={"role": body.role}, group_id=group_id, role=body.role,
    )
    await AuditService(db).record(
        actor=principal.user_id, action="membership.create", target=m.id, result="ok",
        group_id=group_id, user_id=body.user_id, role=body.role, grant_credit=grant,
    )
    await db.commit()
    return _membership_out(m, user.name)


@router.patch("/projects/{group_id}/memberships/{membership_id}")
async def update_membership(
    group_id: str,
    membership_id: str,
    body: MembershipPatch,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Change a member's role. Demoting the last admin returns 409. super_admin or group_admin."""
    principal.require(action="membership.update", group_id=group_id)
    m = await _load_membership(db, group_id, membership_id)
    if body.role not in _MEMBERSHIP_ROLES:
        raise _Validation("invalid role", {"role": body.role})
    _guard_grant_rank(principal, group_id, body.role)
    # Invariant: the last admin cannot be demoted.
    if m.role in _ADMIN_ROLES and body.role not in _ADMIN_ROLES:
        await _guard_last_admin(db, group_id, exclude=membership_id)
    old = m.role
    m.role = body.role
    await db.flush()
    await NotificationService(db).notify(
        [m.user_id], "membership_role_changed", "Role changed",
        f"Your role in the group changed from '{old}' to '{body.role}'.",
        params={"old_role": old, "new_role": body.role}, group_id=group_id,
    )
    await AuditService(db).record(
        actor=principal.user_id, action="membership.update", target=membership_id, result="ok",
        group_id=group_id, changes={"role": {"from": old, "to": body.role}},
    )
    await db.commit()
    user_name = await db.scalar(select(User.name).where(User.id == m.user_id))
    return _membership_out(m, user_name)


@router.delete(
    "/projects/{group_id}/memberships/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_membership(
    group_id: str,
    membership_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Remove a member. Removing the last admin returns 409. super_admin or group_admin."""
    principal.require(action="membership.delete", group_id=group_id)
    m = await _load_membership(db, group_id, membership_id)
    if m.role in _ADMIN_ROLES:
        await _guard_last_admin(db, group_id, exclude=membership_id)
    await db.delete(m)
    await AuditService(db).record(
        actor=principal.user_id, action="membership.delete", target=membership_id, result="ok",
        group_id=group_id, user_id=m.user_id, role=m.role,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── helpers ──
def _page(pagination: Pagination, total: int) -> dict[str, int]:
    size = pagination.size
    return {
        "page": pagination.page,
        "size": size,
        "total": int(total),
        "total_pages": math.ceil(total / size) if size else 0,
    }


async def _load_project(db: AsyncSession, group_id: str) -> Project:
    project = await db.get(Project, group_id)
    if project is None or project.deleted_at is not None:
        raise NotFound("group", {"group_id": group_id})
    return project


async def _load_membership(db: AsyncSession, group_id: str, membership_id: str) -> Membership:
    m = await db.get(Membership, membership_id)
    if m is None or m.group_id != group_id:
        raise NotFound("membership", {"membership_id": membership_id})
    return m


async def _guard_last_admin(db: AsyncSession, group_id: str, exclude: str) -> None:
    """Enforce that at least one admin (group_admin or org_admin) remains; otherwise 409."""
    remaining = await db.scalar(
        select(func.count())
        .select_from(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.group_id == group_id,
            Membership.role.in_(_ADMIN_ROLES),
            Membership.id != exclude,
            User.deleted_at.is_(None),
        )
    ) or 0
    if int(remaining) == 0:
        raise _LastAdmin("cannot remove/demote the last admin of the project", {"group_id": group_id})
