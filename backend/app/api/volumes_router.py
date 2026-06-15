"""Storage volumes router.

Volumes / folders / permissions / quota-requests / snapshots. This plane only computes PVC
*desired spec* — actual PVC apply/binding is the operator's responsibility.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, get_current_principal
from app.api.schemas.volume import PermissionBody, QuotaRequestBody, VolumeCreate, VolumeRead
from app.auth.rbac import Principal, rbac_allows
from app.core import ids
from app.core.config import settings
from app.core.errors import (
    DomainError,
    Forbidden,
    InvalidStateTransition,
    NotFound,
    QuotaExceeded,
)
from app.db.base import get_db
from app.db.models import (
    ResourcePolicy,
    Session,
    StorageFolder,
    StorageVolume,
    User,
    VolumeMount,
    VolumePermission,
    VolumeQuotaRequest,
    VolumeSnapshot,
)
from app.domain.audit_service import AuditService
from app.domain.notification_service import NotificationService

router = APIRouter(prefix="/storage/volumes", tags=["volumes"])


class _ValidationFailed(DomainError):
    """422 validation_failed for router-level guards: ValidationError -> 422."""

    code, http = "validation_failed", 422


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _load_volume(db: AsyncSession, volume_id: str) -> StorageVolume:
    """Fetch a live (not soft-deleted) volume or raise 404."""
    vol = await db.get(StorageVolume, volume_id)
    if vol is None or vol.deleted_at is not None:
        raise NotFound(f"volume not found: {volume_id}")
    return vol


async def _active_mount_session_ids(db: AsyncSession, volume_id: str) -> list[str]:
    """Session ids **actively** mounting the volume — conflict guard for delete/access-mode change.

    Only active sessions count (pending, preparing, running, paused, terminating). Mount records of
    terminated sessions remain as history but never block a deletion.
    """
    return list(
        (
            await db.scalars(
                select(VolumeMount.session_id)
                .join(Session, Session.id == VolumeMount.session_id)
                .where(
                    VolumeMount.volume_id == volume_id,
                    Session.status.in_(
                        ("pending", "preparing", "running", "paused", "terminating")
                    ),
                    Session.deleted_at.is_(None),
                )
            )
        ).all()
    )


async def _storage_usage(
    db: AsyncSession, scope: str, scope_id: str, exclude_volume_id: str | None = None,
) -> tuple[int | None, int]:
    """Return (limit_gb, allocated_gb) from ResourcePolicy.limits.storage_gb for the exact scope.

    The limit is None (unlimited) when there is no policy or it is 0. allocated is the sum of the
    volume quotas in the same (scope, scope_id)."""
    pol = (
        await db.scalars(
            select(ResourcePolicy).where(
                ResourcePolicy.scope == scope, ResourcePolicy.scope_id == scope_id
            )
        )
    ).first()
    raw_limit = (pol.limits or {}).get("storage_gb") if pol is not None else None
    limit = int(raw_limit) if raw_limit else None   # None or 0 means unlimited
    q = select(func.coalesce(func.sum(StorageVolume.quota_gb), 0)).where(
        StorageVolume.scope == scope,
        StorageVolume.scope_id == scope_id,
        StorageVolume.deleted_at.is_(None),
    )
    if exclude_volume_id is not None:
        q = q.where(StorageVolume.id != exclude_volume_id)
    allocated = int(await db.scalar(q) or 0)
    return limit, allocated


async def _assert_storage_quota(
    db: AsyncSession, scope: str, scope_id: str, target_quota_gb: int,
    exclude_volume_id: str | None = None,
) -> None:
    """Enforce the per-scope total storage limit from ResourcePolicy.limits.storage_gb.

    When the other volumes in the same (scope, scope_id) — this one excluded — plus the new or
    target quota exceed the limit, respond 409 quota_exceeded. No policy, or no storage_gb, means
    unlimited."""
    limit, allocated = await _storage_usage(db, scope, scope_id, exclude_volume_id)
    if limit is None:   # unlimited
        return
    if allocated + int(target_quota_gb) > limit:
        raise QuotaExceeded(
            "storage quota exceeded",
            {
                "scope": scope, "limit_gb": limit,
                "allocated_gb": allocated, "requested_gb": int(target_quota_gb),
            },
        )


@router.get("", response_model=list[VolumeRead])
async def list_volumes(
    scope: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    type: str | None = Query(default=None),
    access_mode: str | None = Query(default=None),
    page: Pagination = Depends(Pagination),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List volumes with scope/type/access_mode filters + pagination."""
    stmt = select(StorageVolume).where(StorageVolume.deleted_at.is_(None))
    if scope is not None:
        stmt = stmt.where(StorageVolume.scope == scope)
    if scope_id is not None:
        stmt = stmt.where(StorageVolume.scope_id == scope_id)
    if type is not None:
        stmt = stmt.where(StorageVolume.type == type)
    if access_mode is not None:
        stmt = stmt.where(StorageVolume.access_mode == access_mode)
    # Tenant boundary: outside super_admin, a caller sees only volumes they own (user scope),
    # volumes of a group they administer (group scope, group_admin and above), and volumes they hold
    # an explicit VolumePermission on — the same rule as _assert_volume_access.
    if "super_admin" not in principal.global_roles:
        admin_group_ids = [
            gid for gid, role in principal.memberships.items()
            if role in ("group_admin", "org_admin")
        ]
        visible = or_(
            and_(StorageVolume.scope == "user", StorageVolume.scope_id == principal.user_id),
            and_(StorageVolume.scope == "group", StorageVolume.scope_id.in_(admin_group_ids))
            if admin_group_ids else False,
            StorageVolume.id.in_(
                select(VolumePermission.volume_id).where(
                    VolumePermission.user_id == principal.user_id
                )
            ),
        )
        stmt = stmt.where(visible)
    stmt = stmt.order_by(StorageVolume.id).offset(page.offset).limit(page.size)
    vols = list((await db.scalars(stmt)).all())
    if not vols:
        return []
    # Fetch the caller's per-volume role (owner, rw, or ro) in one query and expose it as
    # VolumeRead.role.
    roles = dict(
        (await db.execute(
            select(VolumePermission.volume_id, VolumePermission.role).where(
                VolumePermission.user_id == principal.user_id,
                VolumePermission.volume_id.in_([v.id for v in vols]),
            )
        )).all()
    )
    return [VolumeRead.model_validate(v).model_copy(update={"role": roles.get(v.id)}) for v in vols]


@router.get("/quota-usage")
async def storage_quota_usage(
    scope: str = Query(...),
    scope_id: str = Query(...),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Per-scope storage limit, usage, and headroom, for the limit warning on the new-volume form.

    Reads the same (scope, scope_id) policy and volume quota sum that _assert_storage_quota uses at
    creation time. has_limit=false means unlimited: no policy, or a limit of 0."""
    limit, allocated = await _storage_usage(db, scope, scope_id)
    if limit is None:
        return {"has_limit": False, "allocated_gb": allocated}
    return {
        "has_limit": True,
        "limit_gb": limit,
        "allocated_gb": allocated,
        "remaining_gb": max(0, limit - allocated),
    }


def _assert_can_manage_volume(principal: Principal, scope: str, scope_id: str) -> None:
    """Authorize mutating a volume — create, update, delete, or share. Owner or administrator only.

    - super_admin: everything.
    - user scope (a personal volume): only the owner, where scope_id is the caller. Users create and
      manage their own personal volumes.
    - group scope (a group volume): only group_admin and above for that group, reusing the
      volume.create permission matrix.
    """
    if "super_admin" in principal.global_roles:
        return
    if scope == "user":
        if scope_id == principal.user_id:
            return
        raise Forbidden("not permitted: you cannot manage another user's personal volume")
    if rbac_allows(principal, "volume.create", group_id=scope_id):
        return
    raise Forbidden("not permitted: only that group's administrators can manage a group volume")


async def _assert_volume_access(db: AsyncSession, principal: Principal, vol: StorageVolume) -> None:
    """Authorize reading a volume or snapshotting it — owner, grantee, or administrator.

    - super_admin: everything.
    - user scope: the owner.
    - group scope: group_admin and above for that group.
    - Otherwise: anyone holding an explicit VolumePermission (owner, rw, or ro).
    """
    if "super_admin" in principal.global_roles:
        return
    if vol.scope == "user" and vol.scope_id == principal.user_id:
        return
    if vol.scope == "group" and principal.memberships.get(vol.scope_id) in ("group_admin", "org_admin"):
        return
    role = await db.scalar(
        select(VolumePermission.role).where(
            VolumePermission.volume_id == vol.id,
            VolumePermission.user_id == principal.user_id,
        )
    )
    if role is not None:
        return
    raise Forbidden("not permitted: you have no access to this volume")


@router.get("/pricing")
async def volume_pricing(principal: Principal = Depends(get_current_principal)):
    """The storage rate in credits per GB-hour. The new-volume form multiplies it by quota_gb to
    show the estimated hourly and monthly cost. 0 disables storage billing. This is
    STORAGE_CREDIT_PER_GB_HOUR, the same rate the storage_billing worker charges."""
    return {"credit_per_gb_hour": float(settings.STORAGE_CREDIT_PER_GB_HOUR)}


@router.post("", status_code=status.HTTP_201_CREATED, response_model=VolumeRead)
async def create_volume(
    body: VolumeCreate,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Create a named volume of any type.

    Every type — personal workspace, group share, dataset, scratch — can be created as often as
    needed and attached to sessions. There is no auto-provisioned singleton. Within a scope, the
    (type, name) pair is unique."""
    _assert_can_manage_volume(principal, body.scope, body.scope_id)
    # Enforce the scope's total storage limit, as GPU limits are enforced. No policy means
    # unlimited.
    await _assert_storage_quota(db, body.scope, body.scope_id, body.quota_gb)
    vol = StorageVolume(
        id=ids.new("volume"),
        scope=body.scope,
        scope_id=body.scope_id,
        type=body.type,
        name=body.name,
        access_mode=body.access_mode,
        owner_id=body.scope_id if body.scope == "group" else principal.user_id,
        quota_gb=body.quota_gb,
        used_gb=0,
    )
    db.add(vol)
    # creator is the owner of the new volume /: owner role.
    db.add(
        VolumePermission(
            id=ids.new("permission"),
            volume_id=vol.id,
            user_id=principal.user_id,
            role="owner",
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # UNIQUE(scope,scope_id,type) collision -> already provisioned.
        await db.rollback()
        raise QuotaExceeded("volume already exists for scope/type") from None
    await db.refresh(vol)
    return vol


@router.get("/{volume_id}", response_model=VolumeRead)
async def get_volume(
    volume_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Volume detail."""
    vol = await _load_volume(db, volume_id)
    await _assert_volume_access(db, principal, vol)
    role = await db.scalar(
        select(VolumePermission.role).where(
            VolumePermission.volume_id == volume_id,
            VolumePermission.user_id == principal.user_id,
        )
    )
    return VolumeRead.model_validate(vol).model_copy(update={"role": role})


@router.patch("/{volume_id}", response_model=VolumeRead)
async def update_volume(
    volume_id: str,
    body: dict,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Update volume meta: quota_gb (>= used_gb) / access_mode."""
    vol = await _load_volume(db, volume_id)
    _assert_can_manage_volume(principal, vol.scope, vol.scope_id)

    new_quota = body.get("quota_gb")
    if new_quota is not None:
        if int(new_quota) < vol.used_gb:
            # cannot shrink below current usage: quota_gb >= used_gb.
            raise QuotaExceeded(f"quota_gb {new_quota} < used_gb {vol.used_gb}")
        # An expansion is bounded by the same scope limit, with this volume excluded from the sum.
        await _assert_storage_quota(db, vol.scope, vol.scope_id, int(new_quota), exclude_volume_id=vol.id)
        vol.quota_gb = int(new_quota)

    new_mode = body.get("access_mode")
    if new_mode is not None and new_mode != vol.access_mode:
        # access mode change while mounted is a conflict -> 409.
        if await _active_mount_session_ids(db, volume_id):
            raise InvalidStateTransition("cannot change access_mode while mounted")
        vol.access_mode = new_mode

    await db.commit()
    await db.refresh(vol)
    return vol


@router.delete("/{volume_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_volume(
    volume_id: str,
    confirm: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a volume; requires confirm==volume_id, rejects if mounted."""
    vol = await _load_volume(db, volume_id)
    _assert_can_manage_volume(principal, vol.scope, vol.scope_id)
    if confirm != volume_id:
        # confirmation token must equal the target volume_id -> 422.
        raise _ValidationFailed("confirmation_required")
    if await _active_mount_session_ids(db, volume_id):
        raise InvalidStateTransition("volume is mounted by an active session")
    vol.deleted_at = datetime.now(UTC)
    await db.commit()
    await AuditService(db).record(
        actor=principal.user_id, action="storage.volume.delete", target=volume_id
    )


# ── folders ──
@router.get("/{volume_id}/folders")
async def list_folders(
    volume_id: str,
    page: Pagination = Depends(Pagination),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List folders of a volume."""
    vol = await _load_volume(db, volume_id)
    await _assert_volume_access(db, principal, vol)
    rows = (
        await db.scalars(
            select(StorageFolder)
            .where(StorageFolder.volume_id == volume_id)
            .order_by(StorageFolder.path)
            .offset(page.offset)
            .limit(page.size)
        )
    ).all()
    return {
        "data": [
            {
                "path": f.path,
                "size_gb": (f.size_bytes or 0) // (1024**3),
                "modified_at": f.updated_at.isoformat() if f.updated_at else None,
            }
            for f in rows
        ]
    }


@router.post("/{volume_id}/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(
    volume_id: str,
    body: dict,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Create a folder (absolute path) under a volume."""
    vol = await _load_volume(db, volume_id)
    await _assert_volume_access(db, principal, vol)
    path = body.get("path")
    if not path or not isinstance(path, str) or not path.startswith("/"):
        raise _ValidationFailed("path must be an absolute string")
    existing = await db.scalar(
        select(StorageFolder.id).where(
            StorageFolder.volume_id == volume_id, StorageFolder.path == path
        )
    )
    if existing is not None:
        raise InvalidStateTransition("folder already exists")  # 409 conflict 
    folder = StorageFolder(id=ids.new("folder"), volume_id=volume_id, path=path, size_bytes=0)
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return {"path": folder.path, "created_at": folder.created_at.isoformat()}


# ── permissions ──
@router.get("/{volume_id}/permissions")
async def list_permissions(
    volume_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List volume share permissions with user names."""
    await _load_volume(db, volume_id)
    rows = (
        await db.execute(
            select(VolumePermission, User.name)
            .join(User, User.id == VolumePermission.user_id, isouter=True)
            .where(VolumePermission.volume_id == volume_id)
            .order_by(VolumePermission.created_at)
        )
    ).all()
    return {
        "data": [
            {
                "user_id": perm.user_id,
                "user_name": name,
                "role": perm.role,
                "granted_at": perm.created_at.isoformat() if perm.created_at else None,
            }
            for perm, name in rows
        ]
    }


@router.post("/{volume_id}/permissions", status_code=status.HTTP_201_CREATED)
async def grant_permission(
    volume_id: str,
    body: PermissionBody,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Grant/update a volume share permission; upsert on UNIQUE(volume_id,user_id)."""
    vol = await _load_volume(db, volume_id)
    _assert_can_manage_volume(principal, vol.scope, vol.scope_id)
    # Resolve an email to a user_id: people know who to share with by email, not by ULID.
    target_uid = body.user_id
    if not target_uid and body.email:
        target_uid = await db.scalar(
            select(User.id).where(func.lower(User.email) == body.email.strip().lower())
        )
        if not target_uid:
            raise _ValidationFailed("no user with that email", {"email": body.email})
    if not target_uid:
        raise _ValidationFailed("user_id or email required")
    perm = await db.scalar(
        select(VolumePermission).where(
            VolumePermission.volume_id == volume_id,
            VolumePermission.user_id == target_uid,
        )
    )
    if perm is None:
        perm = VolumePermission(
            id=ids.new("permission"),
            volume_id=volume_id,
            user_id=target_uid,
            role=body.role,
        )
        db.add(perm)
    else:
        perm.role = body.role
    await db.commit()
    await db.refresh(perm)
    return {"id": perm.id, "volume_id": volume_id, "user_id": perm.user_id, "role": perm.role}


@router.delete("/{volume_id}/permissions/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_permission(
    volume_id: str,
    user_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a share permission; refuse to remove the last owner."""
    vol = await _load_volume(db, volume_id)
    _assert_can_manage_volume(principal, vol.scope, vol.scope_id)
    perm = await db.scalar(
        select(VolumePermission).where(
            VolumePermission.volume_id == volume_id,
            VolumePermission.user_id == user_id,
        )
    )
    if perm is None:
        raise NotFound(f"permission not found for user {user_id}")
    if perm.role == "owner":
        owner_count = await db.scalar(
            select(func.count())
            .select_from(VolumePermission)
            .where(
                VolumePermission.volume_id == volume_id,
                VolumePermission.role == "owner",
            )
        )
        if (owner_count or 0) <= 1:
            raise InvalidStateTransition("cannot revoke the last owner")  # 409 
    await db.delete(perm)
    await db.commit()


# ── quota-requests ──
@router.post("/{volume_id}/quota-requests", status_code=status.HTTP_201_CREATED)
async def create_quota_request(
    volume_id: str,
    body: QuotaRequestBody,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Request a quota increase (target total > current quota)."""
    vol = await _load_volume(db, volume_id)
    if body.requested_gb <= vol.quota_gb:
        # requested target must exceed current quota -> 422.
        raise _ValidationFailed("requested_gb must be greater than current quota_gb")
    req = VolumeQuotaRequest(
        id=ids.new("quotarequest"),
        volume_id=volume_id,
        requested_gb=body.requested_gb,
        status="pending",
        requester_id=principal.user_id,
    )
    db.add(req)
    await db.flush()
    # Notify whoever approves an expansion: the group's administrators for a project volume, the
    # system administrators otherwise.
    notifier = NotificationService(db)
    recipients = (await notifier.group_admins(vol.scope_id)) if vol.scope == "group" else (await notifier.system_admins())
    await notifier.notify(
        recipients, "volume_quota_request", "Volume expansion requested",
        f"A request to expand a volume to {req.requested_gb} GiB has arrived.",
        volume_id=volume_id, request_id=req.id,
    )
    await db.commit()
    await db.refresh(req)
    return {
        "id": req.id,
        "volume_id": req.volume_id,
        "current_gb": vol.quota_gb,
        "requested_gb": req.requested_gb,
        "status": req.status,
        "requested_by": req.requester_id,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "decided_by": req.decided_by,
        "decided_at": None,
    }


@router.get("/{volume_id}/quota-requests")
async def list_quota_requests(
    volume_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    page: Pagination = Depends(Pagination),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List quota-increase requests for a volume."""
    vol = await _load_volume(db, volume_id)
    stmt = select(VolumeQuotaRequest).where(VolumeQuotaRequest.volume_id == volume_id)
    if status_filter is not None:
        stmt = stmt.where(VolumeQuotaRequest.status == status_filter)
    stmt = (
        stmt.order_by(VolumeQuotaRequest.created_at.desc())
        .offset(page.offset)
        .limit(page.size)
    )
    rows = (await db.scalars(stmt)).all()
    return {
        "data": [
            {
                "id": r.id,
                "volume_id": r.volume_id,
                "current_gb": vol.quota_gb,
                "requested_gb": r.requested_gb,
                "status": r.status,
                "requested_by": r.requester_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


async def _load_quota_request(
    db: AsyncSession, volume_id: str, request_id: str
) -> VolumeQuotaRequest:
    req = await db.get(VolumeQuotaRequest, request_id)
    if req is None or req.volume_id != volume_id:
        raise NotFound(f"quota request not found: {request_id}")
    return req


@router.post("/{volume_id}/quota-requests/{request_id}/approve")
async def approve_quota_request(
    volume_id: str,
    request_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Approve a quota request and raise volume.quota_gb in one txn."""
    vol = await _load_volume(db, volume_id)
    # Only an administrator of the volume's own scope may approve, which closes the cross-tenant
    # hole that a group_id=None requirement would leave open.
    _assert_can_manage_volume(principal, vol.scope, vol.scope_id)
    req = await _load_quota_request(db, volume_id, request_id)
    if req.status != "pending":
        raise InvalidStateTransition(f"quota request already {req.status}")
    # Re-check the scope limit at approval time, since it may have changed since the request.
    await _assert_storage_quota(db, vol.scope, vol.scope_id, req.requested_gb, exclude_volume_id=vol.id)
    req.status = "approved"
    req.decided_by = principal.user_id
    vol.quota_gb = req.requested_gb
    await db.flush()
    await NotificationService(db).notify(
        [req.requester_id], "volume_quota_approved", "Volume expansion approved",
        f"The volume was expanded to {vol.quota_gb} GiB.", volume_id=volume_id,
    )
    await db.commit()
    await db.refresh(req)
    await db.refresh(vol)
    await AuditService(db).record(
        actor=principal.user_id,
        action="storage.quota.approve",
        target=request_id,
        volume_id=volume_id,
        quota_gb=vol.quota_gb,
    )
    return {
        "quota_request": {
            "id": req.id,
            "status": req.status,
            "decided_by": req.decided_by,
            "decided_at": req.updated_at.isoformat() if req.updated_at else None,
        },
        "volume": {"id": vol.id, "quota_gb": vol.quota_gb, "used_gb": vol.used_gb},
    }


@router.post("/{volume_id}/quota-requests/{request_id}/reject")
async def reject_quota_request(
    volume_id: str,
    request_id: str,
    body: dict | None = None,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Reject a quota request (mirror of approve); quota_gb unchanged."""
    vol = await _load_volume(db, volume_id)
    _assert_can_manage_volume(principal, vol.scope, vol.scope_id)
    req = await _load_quota_request(db, volume_id, request_id)
    reason = (body or {}).get("reason")
    if not reason:
        # rejection reason is required -> 422.
        raise _ValidationFailed("reason is required")
    if req.status != "pending":
        raise InvalidStateTransition(f"quota request already {req.status}")
    req.status = "rejected"
    req.decided_by = principal.user_id
    await db.commit()
    await db.refresh(req)
    await AuditService(db).record(
        actor=principal.user_id,
        action="storage.quota.reject",
        target=request_id,
        volume_id=volume_id,
        reason=reason,
    )
    return {
        "id": req.id,
        "volume_id": volume_id,
        "status": req.status,
        "decided_by": req.decided_by,
        "decided_at": req.updated_at.isoformat() if req.updated_at else None,
        "reason": reason,
    }


# ── snapshots ──
@router.post("/{volume_id}/snapshots", status_code=status.HTTP_202_ACCEPTED)
async def create_snapshot(
    volume_id: str,
    body: dict | None = None,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Create a point-in-time snapshot (async); reject if one is in progress."""
    vol = await _load_volume(db, volume_id)
    await _assert_volume_access(db, principal, vol)
    in_progress = await db.scalar(
        select(VolumeSnapshot.id).where(
            VolumeSnapshot.volume_id == volume_id,
            VolumeSnapshot.status == "creating",
        )
    )
    if in_progress is not None:
        raise InvalidStateTransition("a snapshot is already in progress")  # 409 
    name = (body or {}).get("name")
    snap = VolumeSnapshot(
        id=ids.new("snapshot"),
        volume_id=volume_id,
        status="creating",
        size_bytes=vol.used_gb * (1024**3),
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)
    return {
        "id": snap.id,
        "volume_id": snap.volume_id,
        "name": name,
        "status": snap.status,
        "size_gb": (snap.size_bytes or 0) // (1024**3),
        "created_at": snap.created_at.isoformat() if snap.created_at else None,
    }


@router.get("/{volume_id}/snapshots")
async def list_snapshots(
    volume_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    page: Pagination = Depends(Pagination),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List snapshots of a volume."""
    vol = await _load_volume(db, volume_id)
    await _assert_volume_access(db, principal, vol)
    stmt = select(VolumeSnapshot).where(VolumeSnapshot.volume_id == volume_id)
    if status_filter is not None:
        stmt = stmt.where(VolumeSnapshot.status == status_filter)
    stmt = stmt.order_by(VolumeSnapshot.created_at.desc()).offset(page.offset).limit(page.size)
    rows = (await db.scalars(stmt)).all()
    return {
        "data": [
            {
                "id": s.id,
                "volume_id": s.volume_id,
                "status": s.status,
                "size_gb": (s.size_bytes or 0) // (1024**3),
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in rows
        ]
    }


async def _load_snapshot(db: AsyncSession, volume_id: str, snapshot_id: str) -> VolumeSnapshot:
    snap = await db.get(VolumeSnapshot, snapshot_id)
    if snap is None or snap.volume_id != volume_id:
        raise NotFound(f"snapshot not found: {snapshot_id}")
    return snap


@router.post("/{volume_id}/snapshots/{snapshot_id}/restore", status_code=status.HTTP_202_ACCEPTED)
async def restore_snapshot(
    volume_id: str,
    snapshot_id: str,
    body: dict | None = None,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Restore a volume to a snapshot; reject if mounted or snapshot not ready."""
    vol = await _load_volume(db, volume_id)
    _assert_can_manage_volume(principal, vol.scope, vol.scope_id)
    snap = await _load_snapshot(db, volume_id, snapshot_id)
    if (body or {}).get("confirm") != volume_id:
        raise _ValidationFailed("confirmation token mismatch")  # 422 
    if snap.status != "ready":
        raise InvalidStateTransition("snapshot is not ready")  # 409
    if await _active_mount_session_ids(db, volume_id):
        raise InvalidStateTransition("volume is mounted by an active session")  # 409
    await AuditService(db).record(
        actor=principal.user_id,
        action="storage.snapshot.restore",
        target=snapshot_id,
        volume_id=volume_id,
    )
    return {
        "volume_id": volume_id,
        "snapshot_id": snapshot_id,
        "restore_id": ids.new("snapshot"),
        "status": "restoring",
        "started_at": _utcnow_iso(),
    }


@router.delete("/{volume_id}/snapshots/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_snapshot(
    volume_id: str,
    snapshot_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Delete a snapshot; reject while still creating."""
    vol = await _load_volume(db, volume_id)
    _assert_can_manage_volume(principal, vol.scope, vol.scope_id)
    snap = await _load_snapshot(db, volume_id, snapshot_id)
    if snap.status == "creating":
        raise InvalidStateTransition("snapshot is still being created")  # 409 
    await db.delete(snap)
    await db.commit()
    await AuditService(db).record(
        actor=principal.user_id,
        action="storage.snapshot.delete",
        target=snapshot_id,
        volume_id=volume_id,
    )
