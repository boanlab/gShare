"""Users router. Auth/me + user CRUD + global-role grant.

Includes /auth/me and the email+password auth endpoints (/auth/login, /auth/change-password)
gated by AUTH_ALLOW_LOCAL_PASSWORD.
"""
from __future__ import annotations

import asyncio
import json
import math
import secrets
import time
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from jose import jwt
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal, idempotency_key, require_idem
from app.api.schemas.user import MeResponse
from app.auth.rbac import Principal, primary_global_role
from app.core import ids
from app.core.config import settings
from app.core.errors import DomainError, Forbidden, NotFound, Unauthenticated
from app.core.passwords import hash_password_async, verify_password_async
from app.core.ratelimit import check_rate
from app.core.redis import get_redis
from app.db.base import get_db
from app.db.models import (
    Allocation,
    CreditWallet,
    Membership,
    Organization,
    Project,
    StorageVolume,
    User,
)
from app.db.models import (
    Session as SessionModel,
)
from app.domain.audit_service import AuditService
from app.domain.welcome_credit import grant_welcome_credit

router = APIRouter(tags=["users"])

# Status / role enums.
_USER_STATUSES = {"invited", "active", "suspended"}
_GLOBAL_ROLES = {"super_admin"}
_MEMBERSHIP_ROLES = {"org_admin", "group_admin", "member", "guest"}


class _Conflict(DomainError):
    code, http = "conflict", 409


class _Validation(DomainError):
    code, http = "validation_failed", 422


# ── request bodies ──
class UserCreate(BaseModel):
    # Plain str + lightweight format check (avoids the optional email-validator dependency).
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=100)
    group_id: str                                          # group to join, required; the organization follows from it
    status: str = "active"
    initial_role: str = "member"
    password: str = Field(min_length=8, max_length=128)   # initial password, which the user must change at first login


class ChangePasswordRequest(BaseModel):
    current_password: str | None = None
    new_password: str = Field(min_length=8, max_length=128)


class UserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = None
    email: str | None = Field(default=None, min_length=3, max_length=254)
    # Administrator password reset: set a new temporary password and force a change at next login.
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserDepartmentSet(BaseModel):
    # The user's single group. None removes every group membership, leaving them unaffiliated.
    group_id: str | None = None


class GlobalRoleSet(BaseModel):
    # Grant several global roles. The global_roles list is canonical; the singular global_role is
    # accepted only for older clients.
    global_roles: list[str] | None = None
    global_role: str | None = None  # deprecated; when present it is treated as [global_role]


def _serialize(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "status": u.status,
        "global_role": u.global_role,
        "global_roles": list(u.global_roles or []),
        "must_change_password": bool(u.must_change_password),
        "created_at": u.created_at,
    }


@router.get("/auth/me", response_model=MeResponse)
async def auth_me(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Return identity/role/memberships of the current principal.

    memberships is returned as a list of {group_id, project_name, role} for the console's context
    switcher and RBAC, converted from the internal {project_id: role} mapping with names resolved.
    """
    pids = list(principal.memberships.keys())
    pinfo: dict[str, tuple[str, str | None]] = {}   # pid -> (project_name, org_id)
    onames: dict[str, str] = {}                       # org_id -> org_name
    if pids:
        rows = (await db.execute(
            select(Project.id, Project.name, Project.org_id).where(Project.id.in_(pids))
        )).all()
        pinfo = {pid: (nm, oid) for pid, nm, oid in rows}
        oids = {oid for _, oid in pinfo.values() if oid}
        if oids:
            orows = (await db.execute(
                select(Organization.id, Organization.name).where(Organization.id.in_(oids))
            )).all()
            onames = {oid: onm for oid, onm in orows}
    admin_gids: set[str] = set()
    if pids:
        # Which of the caller's groups have at least one live admin — the credit-request form
        # disables the "ask my group admin" path when nobody could approve it.
        arows = (await db.execute(
            select(Membership.group_id.distinct())
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.group_id.in_(pids),
                Membership.role.in_(("group_admin", "org_admin")),
                User.deleted_at.is_(None),
            )
        )).all()
        admin_gids = {gid for (gid,) in arows}
    memberships = [
        {
            "group_id": pid,
            "project_name": pinfo.get(pid, (pid, None))[0],
            "org_id": pinfo.get(pid, (None, None))[1],
            "org_name": onames.get(pinfo.get(pid, (None, None))[1]),
            "role": role,
            "has_group_admin": pid in admin_gids,
        }
        for pid, role in principal.memberships.items()
    ]
    me = await db.get(User, principal.user_id)
    return {
        "user_id": principal.user_id,
        "global_role": principal.global_role,
        "global_roles": sorted(principal.global_roles),
        "org_admin_orgs": sorted(principal.org_admin_orgs),
        "memberships": memberships,
        "email": principal.email,
        "name": me.name if me is not None else None,
        "must_change_password": bool(me.must_change_password) if me is not None else False,
    }


class _LoginRequest(BaseModel):
    email: str | None = None
    password: str | None = None


@router.post("/auth/login")
async def auth_login(body: _LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Email+password local login (gated by AUTH_ALLOW_LOCAL_PASSWORD).

    Looks the user up by email and issues an HS256 token signed with USER_JWT_SECRET, which
    jwt_auth.verify_jwt verifies.
    """
    if not settings.AUTH_ALLOW_LOCAL_PASSWORD:
        raise Unauthenticated("local password login disabled")
    email = (body.email or "").strip().lower()
    # Brute-force / thundering-herd guard. Per-email first (targeted stuffing), then per-client-IP
    # (broad sweeps). The deployment sits behind ingress, so the first X-Forwarded-For hop is the
    # client; fall back to the socket peer.
    client_ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    if email:
        await check_rate(f"login:email:{email}", limit=10, window_sec=300)
    await check_rate(f"login:ip:{client_ip}", limit=30, window_sec=300)
    user = None
    if email:
        user = (
            await db.execute(select(User).where(func.lower(User.email) == email))
        ).scalar_one_or_none()
    if user is None:
        raise Unauthenticated("invalid credentials")
    # A suspended or soft-deleted account must not authenticate. Checked before the password so
    # the account state, not the credential, decides — the message stays generic on purpose.
    if user.deleted_at is not None or user.status == "suspended":
        raise Unauthenticated("invalid credentials")
    # Password check: when a hash exists it must match. An account with no hash — bootstrap or
    # legacy — is let through, but must_change_password is set so a password is chosen immediately.
    if user.password_hash:
        if not await verify_password_async(body.password or "", user.password_hash):
            raise Unauthenticated("invalid credentials")
    elif not user.must_change_password:
        user.must_change_password = True
        await db.commit()
    return _issue_token(user)


def _issue_token(user: User) -> dict[str, Any]:
    now = int(time.time())
    ttl = 86400
    claims = {
        "sub": user.id,
        "aud": "gshare-user",   # audience for user tokens, kept distinct from session connect cookies so the two cannot be mixed
        "email": user.email,
        "global_role": user.global_role,
        "global_roles": list(user.global_roles or []),
        "must_change_password": bool(user.must_change_password),
        "iat": now,
        "exp": now + ttl,
    }
    token = jwt.encode(claims, settings.USER_JWT_SECRET, algorithm="HS256")
    return {
        "access_token": token,
        "refresh_token": token,
        "token_type": "Bearer",
        "expires_in": ttl,
    }


@router.post("/auth/change-password")
async def change_password(
    body: ChangePasswordRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Change your own password. Unless must_change_password is set, the current password is
    verified. A fresh token is issued afterwards."""
    user = await db.get(User, principal.user_id)
    if user is None:
        raise NotFound("user", {"user_id": principal.user_id})
    if not user.must_change_password and user.password_hash:
        if not await verify_password_async(body.current_password or "", user.password_hash):
            raise Unauthenticated("current password mismatch")
    user.password_hash = await hash_password_async(body.new_password)
    user.must_change_password = False
    await AuditService(db).record(
        actor=user.id, action="user.change_password", target=user.id, result="ok"
    )
    await db.commit()
    return _issue_token(user)


@router.get("/users")
async def list_users(
    pagination: Pagination = Depends(),
    status_filter: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None),
    org_id: str | None = Query(default=None),
    group_id: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Paginated user list. super_admin, org_admin, or group_admin, the latter two scoped to the
    groups they administer."""
    principal.require(action="user.read")

    is_super = "super_admin" in principal.global_roles
    base = select(User).where(User.deleted_at.is_(None))
    if not is_super:
        # org_admin (expanded to the whole organization) and group_admin see only members of the
        # groups they administer.
        admin_group_ids = {
            gid for gid, r in principal.memberships.items() if r in ("org_admin", "group_admin")
        }
        member_ids = select(Membership.user_id).where(
            Membership.group_id.in_(admin_group_ids or {"__none__"})
        )
        base = base.where(User.id.in_(member_ids))
    if org_id is not None:
        # Limited to members of the organization's groups, plus that organization's org_admins.
        org_member_ids = (
            select(Membership.user_id)
            .join(Project, Project.id == Membership.group_id)
            .where(Project.org_id == org_id)
        )
        org_admin_ids = select(Membership.user_id).where(
            Membership.org_id == org_id, Membership.role == "org_admin"
        )
        base = base.where(or_(User.id.in_(org_member_ids), User.id.in_(org_admin_ids)))
    if group_id is not None:
        base = base.where(
            User.id.in_(select(Membership.user_id).where(Membership.group_id == group_id))
        )
    if status_filter is not None:
        if status_filter not in _USER_STATUSES:
            raise _Validation("invalid status filter", {"status": status_filter})
        base = base.where(User.status == status_filter)
    if q:
        like = f"%{q}%"
        base = base.where(or_(User.name.ilike(like), User.email.ilike(like)))

    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.scalars(
            base.order_by(User.created_at.desc()).offset(pagination.offset).limit(pagination.size)
        )
    ).all()

    # Load the organization and group memberships for this page in one query and attach them per
    # row, for the list columns.
    memberships_by_user: dict[str, list[dict[str, Any]]] = {}
    user_ids = [u.id for u in rows]
    if user_ids:
        mem_rows = (
            await db.execute(
                select(
                    Membership.user_id,
                    Membership.group_id,
                    Membership.role,
                    Project.name,
                    Project.org_id,
                    Organization.name,
                )
                .join(Project, Project.id == Membership.group_id)
                .join(Organization, Organization.id == Project.org_id)
                .where(Membership.user_id.in_(user_ids))
            )
        ).all()
        for uid, gid, role, gname, org_id, oname in mem_rows:
            memberships_by_user.setdefault(uid, []).append(
                {"group_id": gid, "group_name": gname, "org_id": org_id, "org_name": oname, "role": role}
            )

    # Organization-level memberships (org_admin) are separate from group memberships, so they are
    # loaded and attached separately for the role column.
    org_admins_by_user: dict[str, list[dict[str, Any]]] = {}
    if user_ids:
        oa_rows = (
            await db.execute(
                select(Membership.user_id, Membership.org_id, Organization.name)
                .join(Organization, Organization.id == Membership.org_id)
                .where(
                    Membership.user_id.in_(user_ids),
                    Membership.org_id.is_not(None),
                    Membership.role == "org_admin",
                )
            )
        ).all()
        for uid, oid, oname in oa_rows:
            org_admins_by_user.setdefault(uid, []).append({"org_id": oid, "org_name": oname})

    def _with_memberships(u: User) -> dict[str, Any]:
        return {
            **_serialize(u),
            "memberships": memberships_by_user.get(u.id, []),
            "org_admins": org_admins_by_user.get(u.id, []),
        }

    size = pagination.size
    return {
        "data": [_with_memberships(u) for u in rows],
        "pagination": {
            "page": pagination.page,
            "size": size,
            "total": total,
            "total_pages": math.ceil(total / size) if size else 0,
        },
    }


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Create a user + enroll them into the chosen group with an initial membership role.

    The new user is created in ``active`` (or ``suspended``) state; ``invited`` is reserved for the
    invite-mail flow. A group is mandatory — the user joins it on creation (org is derived from the
    group). Email is UNIQUE (-> 409 conflict). super_admin·org_admin.
    """
    principal.require(action="user.create")

    email = body.email.strip().lower()
    at = email.find("@")
    if at <= 0 or "." not in email[at + 1 :] or email.endswith("."):
        raise _Validation("invalid email", {"email": body.email})
    if body.status not in {"active", "suspended"}:
        raise _Validation("invalid status", {"status": body.status})
    if body.initial_role not in _MEMBERSHIP_ROLES:
        raise _Validation("invalid initial_role", {"initial_role": body.initial_role})

    # The group is required; an unknown one is 404. The organization follows from the group.
    group = await db.get(Project, body.group_id)
    if group is None or group.deleted_at is not None:
        raise NotFound("group", {"group_id": body.group_id})

    # Pre-check uniqueness for a clean 409 (the UNIQUE index is the final defense).
    existing = await db.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise _Conflict("email already exists", {"email": email})

    user = User(
        id=ids.new("user"),
        email=email,
        name=body.name,
        status=body.status,
        global_role=None,
        global_roles=[],
        password_hash=await hash_password_async(body.password),  # initial password set at registration
        must_change_password=True,                     # force a change at first login
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:  # raced unique violation
        await db.rollback()
        raise _Conflict("email already exists", {"email": email}) from exc

    # Create the personal wallet: the leaf of the credit hierarchy, funded by the group admin and
    # spent by the user's sessions.
    from decimal import Decimal as _Decimal

    db.add(
        CreditWallet(
            id=ids.new("wallet"),
            owner_type="user",
            owner_id=user.id,
            balance=_Decimal("0"),
            reserved=_Decimal("0"),
        )
    )

    # Grant membership of the chosen group with initial_role. The (user, group) pair is new, so
    # there is nothing to conflict with.
    db.add(
        Membership(
            id=ids.new("membership"),
            user_id=user.id,
            group_id=body.group_id,
            role=body.initial_role,
        )
    )

    # Group-configured welcome credit, minted into the fresh personal wallet (0 = off).
    await db.flush()
    welcome = await grant_welcome_credit(db, user.id, group)

    await AuditService(db).record(
        actor=principal.user_id,
        action="user.create",
        target=user.id,
        result="ok",
        email=user.email,
        group_id=body.group_id,
        org_id=group.org_id,
        initial_role=body.initial_role,
        welcome_credit=(str(welcome) if welcome is not None else None),
    )
    await db.commit()

    response.headers["Location"] = f"/api/v1/users/{user.id}"
    return _serialize(user)


class BulkUserRow(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=100)


class BulkUserCreate(BaseModel):
    group_id: str
    initial_role: str = "member"
    rows: list[BulkUserRow] = Field(min_length=1, max_length=200)


_BULK_RESULT_TTL_SEC = 86400


def _valid_email(email: str) -> bool:
    at = email.find("@")
    return at > 0 and "." in email[at + 1:] and not email.endswith(".")


@router.post("/users/bulk", status_code=status.HTTP_207_MULTI_STATUS)
async def bulk_create_users(
    body: BulkUserCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    idem: str | None = Depends(idempotency_key),
):
    """Roster import: create up to 200 users per request, each with a personal wallet, a
    membership in the chosen group, and a server-generated initial password returned once in the
    response (must_change_password forces rotation at first login).

    Partial success by design: rows report ``created`` / ``exists`` / ``invalid`` individually so
    re-uploading a roster is safe — existing accounts are reported, not fatal. The whole batch is
    idempotent on the Idempotency-Key (a replay returns the stored result, passwords included,
    for 24h). One audit record per batch, not one per student. super_admin·org_admin.
    """
    principal.require(action="user.create")
    idem = require_idem(idem)
    await check_rate(f"users-bulk:{principal.user_id}", limit=30, window_sec=300)

    redis = get_redis()
    cache_key = f"users-bulk:{principal.user_id}:{idem}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    if body.initial_role not in _MEMBERSHIP_ROLES:
        raise _Validation("invalid initial_role", {"initial_role": body.initial_role})
    group = await db.get(Project, body.group_id)
    if group is None or group.deleted_at is not None:
        raise NotFound("group", {"group_id": body.group_id})

    # Validate + dedupe client-side rows first so hashing only runs for viable rows.
    seen: set[str] = set()
    prepared: list[tuple[int, str, str] | tuple[int, str, None]] = []  # (row, email, name|None=invalid)
    for i, row in enumerate(body.rows):
        email = row.email.strip().lower()
        if not _valid_email(email):
            prepared.append((i, row.email, None))
        elif email in seen:
            prepared.append((i, email, None))
        else:
            seen.add(email)
            prepared.append((i, email, row.name.strip()))

    existing = set(
        (await db.scalars(select(User.email).where(User.email.in_(seen)))).all()
    ) if seen else set()

    # Hash the initial passwords off the loop with bounded concurrency.
    to_create = [(i, email, name) for i, email, name in prepared
                 if name is not None and email not in existing]
    sem = asyncio.Semaphore(4)

    async def _mk_password() -> tuple[str, str]:
        pw = secrets.token_urlsafe(9)
        async with sem:
            return pw, await hash_password_async(pw)

    passwords = await asyncio.gather(*(_mk_password() for _ in to_create))

    results: list[dict[str, Any]] = []
    created = 0
    by_index: dict[int, dict[str, Any]] = {}
    for (i, email, name), (pw, pw_hash) in zip(to_create, passwords, strict=True):
        user = User(
            id=ids.new("user"), email=email, name=name, status="active",
            global_role=None, global_roles=[],
            password_hash=pw_hash, must_change_password=True,
        )
        db.add(user)
        db.add(CreditWallet(
            id=ids.new("wallet"), owner_type="user", owner_id=user.id,
            balance=Decimal("0"), reserved=Decimal("0"),
        ))
        db.add(Membership(
            id=ids.new("membership"), user_id=user.id,
            group_id=body.group_id, role=body.initial_role,
        ))
        created += 1
        # Welcome credit needs the wallet row visible to the SELECT inside the grant.
        await db.flush()
        await grant_welcome_credit(db, user.id, group)
        by_index[i] = {
            "row": i, "email": email, "status": "created",
            "user_id": user.id, "initial_password": pw,
        }

    failed_emails: list[str] = []
    for i, email, name in prepared:
        if i in by_index:
            results.append(by_index[i])
        elif name is None:
            failed_emails.append(email)
            results.append({"row": i, "email": email, "status": "invalid",
                            "code": "invalid_email_or_duplicate"})
        else:
            results.append({"row": i, "email": email, "status": "exists"})

    try:
        await db.flush()
    except IntegrityError as exc:
        # A concurrent import raced us on some email; the client re-uploads and gets `exists`.
        await db.rollback()
        raise _Conflict("bulk import raced another import; retry", {"detail": str(exc.orig)[:200]}) from exc

    await AuditService(db).record(
        actor=principal.user_id, action="user.bulk_create", target=body.group_id, result="ok",
        group_id=body.group_id, org_id=group.org_id, initial_role=body.initial_role,
        requested=len(body.rows), created=created,
        skipped_existing=sum(1 for r in results if r["status"] == "exists"),
        invalid=failed_emails[:50],
    )
    await db.commit()

    response_body = {
        "results": results,
        "summary": {"requested": len(body.rows), "created": created,
                    "exists": sum(1 for r in results if r["status"] == "exists"),
                    "invalid": sum(1 for r in results if r["status"] == "invalid")},
    }
    await redis.set(cache_key, json.dumps(response_body), ex=_BULK_RESULT_TTL_SEC)
    return response_body


@router.get("/users/resolve")
async def resolve_user(
    email: str = Query(min_length=3, max_length=254),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Resolve an EXACT email to {id, name} for the volume-share confirm step.

    Any authenticated user may call it: sharing needs "does this address exist, and who is it",
    and members cannot list users. Only an exact match answers — no enumeration surface beyond
    what grant-by-email already reveals.
    """
    row = (
        await db.execute(
            select(User.id, User.name).where(
                func.lower(User.email) == email.strip().lower(), User.deleted_at.is_(None)
            )
        )
    ).first()
    if row is None:
        raise NotFound("user", {"email": email})
    return {"id": row[0], "name": row[1]}


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """User detail incl. memberships. self or super_admin/org_admin."""
    is_self = principal.user_id == user_id
    if not is_self:
        principal.require(action="user.read")

    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound("user", {"user_id": user_id})

    memberships = (
        await db.scalars(select(Membership).where(Membership.user_id == user_id))
    ).all()
    out = _serialize(user)
    out["memberships"] = [
        {"group_id": m.group_id, "role": m.role, "expires_at": m.expires_at}
        for m in memberships
    ]
    return out


@router.get("/users/{user_id}/usage")
async def get_user_usage(
    user_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Live resource footprint: sessions, host compute, GPU slices, volumes, wallet.

    Self or an administrator (``user.read``). Everything is CURRENT state, not history — the
    admin drawer answers "what is this user holding right now?".
    """
    if principal.user_id != user_id:
        principal.require(action="user.read")
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound("user", {"user_id": user_id})

    live = (
        await db.scalars(
            select(SessionModel).where(
                SessionModel.owner_user_id == user_id,
                SessionModel.deleted_at.is_(None),
                SessionModel.status.notin_(["terminated"]),
            )
        )
    ).all()
    by_status: dict[str, int] = {}
    for x in live:
        by_status[x.status] = by_status.get(x.status, 0) + 1
    # Host CPU/RAM is only held while a pod exists; paused (cold) sessions gave theirs back.
    holding = [x for x in live if x.status in ("preparing", "running", "terminating")]

    # GPU slices actually reserved/bound to this user's live sessions.
    alloc = (
        await db.execute(
            select(
                func.count(Allocation.id),
                func.coalesce(func.sum(Allocation.gpu_mem_mb), 0),
                func.coalesce(func.sum(Allocation.gpu_cores), 0),
            ).join(SessionModel, SessionModel.id == Allocation.session_id).where(
                SessionModel.owner_user_id == user_id,
                SessionModel.deleted_at.is_(None),
                Allocation.status.in_(["reserved", "bound"]),
            )
        )
    ).one()

    vols = (
        await db.execute(
            select(
                func.count(StorageVolume.id),
                func.coalesce(func.sum(StorageVolume.quota_gb), 0),
                func.coalesce(func.sum(StorageVolume.used_gb), 0),
            ).where(
                StorageVolume.scope == "user",
                StorageVolume.scope_id == user_id,
                StorageVolume.deleted_at.is_(None),
            )
        )
    ).one()

    wallet = (
        await db.scalars(
            select(CreditWallet).where(
                CreditWallet.owner_type == "user", CreditWallet.owner_id == user_id
            )
        )
    ).first()

    return {
        "sessions": {
            "active": len(live),
            "running": by_status.get("running", 0),
            "paused": by_status.get("paused", 0),
            "queued": by_status.get("queued", 0) + by_status.get("pending", 0),
        },
        "host": {
            "cpu": sum(x.cpu or 0 for x in holding),
            "mem_gb": sum(x.mem_gb or 0 for x in holding),
        },
        "gpu": {
            "allocations": int(alloc[0]),
            "gpu_mem_mb": int(alloc[1]),
            "gpu_cores": int(alloc[2]),
        },
        "volumes": {"count": int(vols[0]), "quota_gb": int(vols[1]), "used_gb": int(vols[2])},
        "wallet": {
            "balance": float(wallet.balance) if wallet else 0.0,
            "reserved": float(wallet.reserved) if wallet else 0.0,
        },
    }


async def _target_group_ids(db: AsyncSession, user_id: str) -> set[str]:
    """The set of group ids the target user belongs to."""
    rows = (
        await db.scalars(
            select(Membership.group_id).where(
                Membership.user_id == user_id, Membership.group_id.is_not(None)
            )
        )
    ).all()
    return {g for g in rows if g is not None}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserPatch,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Update a user. What may be changed depends on the caller's role.

    - super_admin: email, name, status, and password reset. Group membership has its own endpoint.
    - org_admin: password reset (and group) for users in their organization. Not email or name.
    - group_admin: password reset only, for users in their group.
    - The user themselves: their display name only.
    """
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound("user", {"user_id": user_id})

    is_self = principal.user_id == user_id
    is_super = "super_admin" in principal.global_roles
    # Sharing an administered group with the target (as org_admin or group_admin) permits a
    # password reset and similar actions.
    admin_group_ids = {
        gid for gid, r in principal.memberships.items() if r in ("org_admin", "group_admin")
    }
    tgt_groups = await _target_group_ids(db, user_id)
    shares_admin_scope = is_super or bool(admin_group_ids & tgt_groups)
    is_org_admin_of_target = is_super or any(
        principal.memberships.get(g) == "org_admin" for g in tgt_groups
    )

    if not (is_self or shares_admin_scope):
        raise Forbidden("not permitted: user.update")

    changes: dict[str, Any] = {}
    # Name: the user themselves, or a super_admin.
    if body.name is not None and body.name != user.name:
        if not (is_self or is_super):
            raise Forbidden("not permitted: user.set_name")
        changes["name"] = {"from": user.name, "to": body.name}
        user.name = body.name
    # Email: super_admin only, and unique.
    if body.email is not None:
        email = body.email.strip().lower()
        if email != user.email:
            if not is_super:
                raise Forbidden("not permitted: user.set_email")
            at = email.find("@")
            if at <= 0 or "." not in email[at + 1 :] or email.endswith("."):
                raise _Validation("invalid email", {"email": body.email})
            dup = await db.scalar(select(User.id).where(User.email == email, User.id != user_id))
            if dup is not None:
                raise _Conflict("email already exists", {"email": email})
            changes["email"] = {"from": user.email, "to": email}
            user.email = email
    # Status (suspended or active): super_admin or org_admin.
    if body.status is not None and body.status != user.status:
        if not is_org_admin_of_target:
            raise Forbidden("not permitted: user.set_status")
        if body.status not in _USER_STATUSES:
            raise _Validation("invalid status", {"status": body.status})
        changes["status"] = {"from": user.status, "to": body.status}
        user.status = body.status
    # Password reset: super_admin, or an org_admin or group_admin sharing scope with the target.
    if body.password is not None:
        if not shares_admin_scope:
            raise Forbidden("not permitted: user.reset_password")
        user.password_hash = await hash_password_async(body.password)
        user.must_change_password = True
        changes["password"] = {"from": "***", "to": "reset"}

    if changes:
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise _Conflict("email already exists", {"email": body.email}) from exc
        await AuditService(db).record(
            actor=principal.user_id, action="user.update", target=user.id, result="ok", changes=changes
        )
    await db.commit()

    # Deactivation reclaims compute: a suspended account keeps its records and volumes, but its
    # live sessions are terminated and settled exactly like the soft-delete path does.
    if changes.get("status", {}).get("to") == "suspended":
        live_ids = list(
            (
                await db.execute(
                    select(SessionModel.id).where(
                        SessionModel.owner_user_id == user_id,
                        SessionModel.deleted_at.is_(None),
                        SessionModel.status.notin_(["terminated"]),
                    )
                )
            ).scalars()
        )
        if live_ids:
            from app.domain.session_service import SessionService  # lazy: avoids an import cycle

            svc = SessionService(db)
            for sid in live_ids:
                # terminate() commits internally; idempotent per session.
                await svc.terminate(sid, forced=True, reason="admin_stopped")
            user = await db.get(User, user_id)
            if user is None:
                raise NotFound("user", {"user_id": user_id})
    return _serialize(user)


@router.put("/users/{user_id}/department")
async def set_user_department(
    user_id: str,
    body: UserDepartmentSet,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Set, or move, a user's group. super_admin or org_admin.

    - super_admin: move to any group, or pass None to remove all group membership.
    - org_admin: move only within their own organization; memberships elsewhere are left alone.

    If the user is not already in the target group they are added as a member, and their previous
    group memberships within the caller's scope are removed.
    """
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound("user", {"user_id": user_id})

    is_super = "super_admin" in principal.global_roles
    # Groups this org_admin administers, expanded to the whole organization.
    managed_group_ids = {gid for gid, r in principal.memberships.items() if r == "org_admin"}
    if not (is_super or managed_group_ids):
        raise Forbidden("not permitted: user.set_department")

    target_gid = body.group_id
    if target_gid is not None:
        project = await db.get(Project, target_gid)
        if project is None or project.deleted_at is not None:
            raise NotFound("group", {"group_id": target_gid})
        if not is_super and target_gid not in managed_group_ids:
            raise Forbidden("not permitted: target group outside your organization")

    # What to remove: for a super_admin, all of the target's group memberships; for an org_admin,
    # only those within the groups they administer.
    existing = (
        await db.scalars(
            select(Membership).where(
                Membership.user_id == user_id, Membership.group_id.is_not(None)
            )
        )
    ).all()
    removable = [
        m for m in existing
        if m.group_id != target_gid and (is_super or m.group_id in managed_group_ids)
    ]
    for m in removable:
        await db.delete(m)

    added = False
    if target_gid is not None and not any(m.group_id == target_gid for m in existing):
        db.add(Membership(id=ids.new("membership"), user_id=user_id, group_id=target_gid, role="member"))
        added = True

    await AuditService(db).record(
        actor=principal.user_id, action="user.update", target=user_id, result="ok",
        changes={"department": {"from": [m.group_id for m in removable], "to": target_gid}},
    )
    await db.commit()
    user2 = await db.get(User, user_id)
    return {**_serialize(user2), "department_changed": bool(removable) or added}


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    hard: bool = Query(default=False),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate (soft, default) or hard-delete a user.

    Default = soft delete (``status=suspended`` + ``deleted_at``). ``?hard=true`` is
    super_admin-only and refused when a live session exists (ledger preservation -> 409 conflict).
    """
    principal.require(action="user.delete")

    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound("user", {"user_id": user_id})

    # Live sessions: the delete screen promises "running sessions are terminated and settled",
    # so the soft path does exactly that — terminate (settle + refund the hold) each non-terminal
    # session as admin_stopped, then deactivate. Only a HARD delete still refuses, because it
    # erases the rows the ledger references.
    live_ids = list(
        (
            await db.execute(
                select(SessionModel.id).where(
                    SessionModel.owner_user_id == user_id,
                    SessionModel.deleted_at.is_(None),
                    SessionModel.status.notin_(["terminated"]),
                )
            )
        ).scalars()
    )
    if live_ids and hard:
        raise _Conflict("user has running sessions", {"running": len(live_ids)})
    if live_ids:
        from app.domain.session_service import SessionService  # lazy: avoids an import cycle

        svc = SessionService(db)
        for sid in live_ids:
            # terminate() commits internally (it serialises on the session row); idempotent.
            await svc.terminate(sid, forced=True, reason="admin_stopped")
        # terminate()'s commits expired the identity map; re-fetch the row we mutate below.
        user = await db.get(User, user_id)
        if user is None or user.deleted_at is not None:
            raise NotFound("user", {"user_id": user_id})

    # Snapshot the name and email into the audit detail: after a hard delete the User row is gone
    # and the name can no longer be resolved.
    snap_name, snap_email = user.name, user.email
    if hard:
        if principal.global_role != "super_admin":
            raise Forbidden("not permitted: user.hard_delete")
        # Preserve the ledger: any session history, terminated included, blocks a hard delete —
        # soft-delete instead.
        sess_n = await db.scalar(
            select(func.count()).select_from(SessionModel).where(SessionModel.owner_user_id == user_id)
        )
        if sess_n and sess_n > 0:
            raise _Conflict("user has session history; use soft delete", {"sessions": int(sess_n)})
        # Clean up rows that depend on the user: memberships, notifications, volume permissions, and
        # the personal wallet when it has no transactions.
        await db.execute(text("DELETE FROM membership WHERE user_id = :u"), {"u": user_id})
        await db.execute(text("DELETE FROM notification WHERE user_id = :u"), {"u": user_id})
        await db.execute(text("DELETE FROM volume_permission WHERE user_id = :u"), {"u": user_id})
        await db.execute(text(
            "DELETE FROM credit_wallet WHERE owner_type = 'user' AND owner_id = :u "
            "AND id NOT IN (SELECT wallet_id FROM credit_transaction)"
        ), {"u": user_id})
        await db.delete(user)
        action = "user.delete.hard"
    else:
        user.status = "suspended"
        user.deleted_at = func.now()
        action = "user.delete.soft"

    await AuditService(db).record(
        actor=principal.user_id, action=action, target=user_id, result="ok",
        name=snap_name, email=snap_email,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/users/{user_id}/global-role")
async def set_global_role(
    user_id: str,
    body: GlobalRoleSet,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Grant or revoke global roles; super_admin only. Several roles may be granted at once."""
    principal.require(action="user.set_global_role")

    # global_roles is canonical; a singular global_role from an older client is promoted to a list.
    if body.global_roles is not None:
        requested = body.global_roles
    elif body.global_role is not None:
        requested = [body.global_role]
    else:
        requested = []
    # Normalise: deduplicate and validate.
    roles: list[str] = []
    for r in requested:
        if r not in _GLOBAL_ROLES:
            raise _Validation("invalid global_role", {"global_role": r})
        if r not in roles:
            roles.append(r)

    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFound("user", {"user_id": user_id})

    old_roles = sorted(user.global_roles or [])
    user.global_roles = roles
    user.global_role = primary_global_role(roles)  # keep the derived primary in sync for scalar checks and queries
    await AuditService(db).record(
        actor=principal.user_id,
        action="user.global_role.set",
        target=user_id,
        result="ok",
        changes={"global_roles": {"from": old_roles, "to": sorted(roles)}},
    )
    await db.commit()
    return _serialize(user)
