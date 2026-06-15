"""Sessions router.

preview-cost / CRUD / start·stop·restart / terminate / logs / connections / force-terminate /
bulk-terminate / checkpoints / SSE event streams. Sessions are interactive only (no batch).
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import Pagination, get_current_principal, idempotency_key, require_idem
from app.api.schemas.session import (
    BulkTerminateRequest,
    ConnectionInfo,
    PreviewCostRequest,
    PreviewCostResponse,
    SessionCreate,
    SessionRead,
)
from app.auth.rbac import Principal
from app.core.errors import Forbidden, InvalidStateTransition, NotFound
from app.core.redis import get_redis
from app.db.base import get_db
from app.db.models import (
    GpuDevice,
    Membership,
    Offering,
    Organization,
    Project,
    Session,
    SessionCheckpoint,
    User,
)
from app.domain.connection_token import ConnectionTokenService
from app.domain.pricing import compute_credit_per_hour, round_credit
from app.domain.scheduler import SchedulerService
from app.domain.session_service import SessionService

router = APIRouter(tags=["sessions"])

# Redis pub/sub channels for SSE fan-out. status_sync publishes
# phase/status transitions here; the operator never publishes credit. Per-session channel is
# session:events:{ses_id}; the admin monitor multiplexes session:events:* (+ queue:events).
_SESSION_CHANNEL = "session:events:{session_id}"
_MONITOR_PATTERN = "session:events:*"
_QUEUE_CHANNEL = "queue:events"
_HEARTBEAT_SEC = 15

_TWO = Decimal("0.01")


def _round2(value: Decimal) -> Decimal:
    return value.quantize(_TWO, rounding=ROUND_HALF_UP)


def _occupancy(gpu_mem_mb: int | None, gpu_cores: int | None, total_mem_mb: int | None) -> float:
    """occupancy = max(mem/total_mem, cores/100)."""
    mem_ratio = 0.0
    if gpu_mem_mb and total_mem_mb:
        mem_ratio = float(gpu_mem_mb) / float(total_mem_mb)
    core_ratio = float(gpu_cores or 0) / 100.0
    return max(mem_ratio, core_ratio)


def _session_read(
    sess: Session,
    owner_name: str | None = None,
    group_name: str | None = None,
    org_id: str | None = None,
    org_name: str | None = None,
) -> SessionRead:
    """Project a Session row to SessionRead, computing occupancy from the snapshot/device."""
    occ: float | None = None
    if sess.resource_class == "gpu":
        occ = _occupancy(sess.gpu_mem_mb, sess.gpu_cores, sess.device_total_mem_mb)
    return SessionRead(
        id=sess.id,
        name=sess.name,
        status=sess.status,
        occupancy=occ,
        bound_gpu_uuid=sess.bound_gpu_uuid,
        cluster_id=sess.cluster_id,
        group_id=sess.group_id,
        group_name=group_name,
        org_id=org_id,
        org_name=org_name,
        resource_class=sess.resource_class,
        mode=sess.mode,
        gpu_mem_mb=sess.gpu_mem_mb,
        gpu_cores=sess.gpu_cores,
        owner_user_id=sess.owner_user_id,
        owner_name=owner_name,
        credit_per_hour_snapshot=(
            float(sess.credit_per_hour_snapshot) if sess.credit_per_hour_snapshot is not None else None
        ),
        started_at=sess.started_at,
        terminated_at=sess.terminated_at,
        created_at=sess.created_at,
    )


async def _load_session(db: AsyncSession, session_id: str) -> Session:
    """Fetch a non-soft-deleted session or raise 404."""
    sess = await db.get(Session, session_id)
    if sess is None or sess.deleted_at is not None:
        raise NotFound("session not found", {"session_id": session_id})
    return sess


def _is_admin_for(principal: Principal, sess: Session) -> bool:
    """Owner or group_admin+/org_admin/super_admin over the session's project."""
    if principal.global_role in ("super_admin", "org_admin"):
        return True
    role = principal.memberships.get(sess.group_id or "")
    return role in ("group_admin", "org_admin")


def _require_access(principal: Principal, sess: Session) -> None:
    """Only the owner, or an administrator (group_admin and above, org_admin, super_admin), may read
    or act on a session.

    A plain member cannot see someone else's session even within the same group; administrators use
    the session monitoring screen for that.
    """
    if sess.owner_user_id == principal.user_id:
        return
    if _is_admin_for(principal, sess):
        return
    raise Forbidden("not permitted: you can only access your own sessions or those you administer")


@router.post("/sessions/preview-cost", response_model=PreviewCostResponse)
async def preview_cost(
    body: PreviewCostRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Estimate cost without creating a session (pricing.estimate).

    estimated_credit_per_hour = offering.credit_per_hour * occupancy;
    occupancy = max(gpu_mem_mb/device_total_mem_mb, gpu_cores/100);
    hold_amount = estimated_credit_per_hour * (expected_runtime_min/60), default 2h horizon.
    CPU (free) sessions are always 0.
    """
    principal.require(action="session.create", group_id=body.group_id)

    offering = await db.get(Offering, body.offering_id)
    if offering is None:
        raise NotFound("offering not found", {"offering_id": body.offering_id})

    if body.resource_class == "cpu":
        # CPU session: priced proportionally to cpu, mem, and disk from the compute preset or the
        # offering defaults, at occupancy 1.0, with a one-hour hold.
        cpu_v = body.cpu if body.cpu is not None else offering.cpu
        mem_v = body.mem_gb if body.mem_gb is not None else offering.mem_gb
        disk_v = body.disk_gb if body.disk_gb is not None else offering.disk_gb
        per_hour = compute_credit_per_hour(cpu_v, mem_v, disk_v)
        return PreviewCostResponse(
            estimated_credit_per_hour=float(per_hour), occupancy=1.0, hold_amount=float(per_hour)
        )

    # Device VRAM denominator for occupancy: representative ready device of the offering's model
    # in the requested cluster. Fall back to GPU_REFERENCE_MEM_MB (NOT offering.gpu_mem_mb —
    # that is the flavor's fractional allotment, not the full card).
    total_mem_mb: int | None = None
    if body.gpu_mem_mb:
        stmt = select(GpuDevice.total_mem_mb).where(
            GpuDevice.cluster_id == body.cluster_id,
            GpuDevice.status == "ready",
        )
        if offering.gpu_model is not None:
            stmt = stmt.where(GpuDevice.model == offering.gpu_model)
        total_mem_mb = (await db.scalars(stmt.order_by(GpuDevice.total_mem_mb).limit(1))).first()
    if total_mem_mb is None:
        from app.core.config import settings as _s
        total_mem_mb = _s.GPU_REFERENCE_MEM_MB

    # An exclusive session sends no gpu_mem_mb or cores, which would compute occupancy 0 — treat it
    # as the full card, 1.0.
    occ = 1.0 if body.mode == "exclusive" else _occupancy(body.gpu_mem_mb, body.gpu_cores, total_mem_mb)
    per_hour = round_credit(Decimal(offering.credit_per_hour) * Decimal(str(occ)))   # whole credits
    # SessionCreate has no expected_runtime_min field; use a 2h default reservation horizon.
    hold = round_credit(per_hour * Decimal("2"))
    return PreviewCostResponse(
        estimated_credit_per_hour=float(per_hour),
        occupancy=occ,
        hold_amount=float(hold),
    )


def _clamp_priority(principal: Principal, body: SessionCreate) -> None:
    """Clamp the requested priority server-side, based on the caller's role in the session's group.

    Priority weights admission, preemption, and reclaim. Trusting the client's value would let a
    plain member jump the admission queue, actively preempt another tenant's running yielded session
    (_preempt_lower_priority), and dodge reclaim (_reclaim_borrower). So a priority above 0 is
    allowed only for a super_admin or for group_admin and above in that group; everyone else —
    members, guests, and requests without a group — is forced to 0.
    Rank: guest < member < group_admin < org_admin.
    """
    if (body.priority or 0) <= 0:
        return
    if "super_admin" in principal.global_roles:
        return
    role = principal.memberships.get(body.group_id or "")
    if role in ("group_admin", "org_admin"):
        return
    body.priority = 0


@router.post("/sessions", status_code=status.HTTP_201_CREATED, response_model=SessionRead)
async def create_session(
    body: SessionCreate,
    principal: Principal = Depends(get_current_principal),
    idem: str | None = Depends(idempotency_key),
    db: AsyncSession = Depends(get_db),
):
    """Admit a session through the scheduler gate. Idempotency-Key required."""
    require_idem(idem)
    principal.require(action="session.create", group_id=body.group_id)
    _clamp_priority(principal, body)
    return await SchedulerService(db).create_session(body, principal, idem)


@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(
    page: Pagination = Depends(),
    status_filter: str | None = Query(default=None, alias="status"),
    group_id: str | None = Query(default=None),
    scope: str = Query(default="mine", pattern="^(mine|all)$"),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List sessions, newest first, paginated.

    scope="mine" (the default, backing the user's Sessions page) returns **only the caller's own
    sessions**, whatever their role. Administrators do not see other people's sessions on their
    personal screen; that is what scope=all is for. scope="all" (the administrators' session
    monitoring screen) returns everything within the caller's authority: super_admin and org_admin
    see all sessions, group_admin sees their own plus those of the groups they administer. A plain
    user asking for scope=all is silently downgraded to their own sessions, so it cannot be used to
    escalate. """
    stmt = select(Session).where(Session.deleted_at.is_(None))

    if scope == "all" and principal.global_role in ("super_admin", "org_admin"):
        pass  # global visibility, for monitoring
    elif scope == "all" and (
        admin_projects := [
            pid for pid, role in principal.memberships.items()
            if role in ("group_admin", "org_admin")
        ]
    ):
        # group_admin: their own sessions plus those of the groups they administer.
        stmt = stmt.where(
            (Session.owner_user_id == principal.user_id)
            | (Session.group_id.in_(admin_projects))
        )
    else:
        # scope=mine, or an unauthorised scope=all, resolves to the caller's own sessions.
        stmt = stmt.where(Session.owner_user_id == principal.user_id)

    if status_filter is not None:
        stmt = stmt.where(Session.status == status_filter)
    if group_id is not None:
        stmt = stmt.where(Session.group_id == group_id)

    stmt = stmt.order_by(Session.created_at.desc()).offset(page.offset).limit(page.size)
    rows = (await db.scalars(stmt)).all()
    # Resolve owner names for the administrators' monitoring view.
    oids = {s.owner_user_id for s in rows if s.owner_user_id}
    onames = {u: n for u, n in (await db.execute(select(User.id, User.name).where(User.id.in_(oids)))).all()} if oids else {}
    # Map groups to their names and organizations, and organizations to their names, since
    # monitoring shows both. A session created without a group falls back to the owner's membership
    # group.
    owner_group: dict[str, str] = {}   # owner_user_id -> their first membership group
    no_group_owners = {s.owner_user_id for s in rows if not s.group_id and s.owner_user_id}
    if no_group_owners:
        mrows = (await db.execute(
            select(Membership.user_id, Membership.group_id)
            .where(Membership.user_id.in_(no_group_owners), Membership.group_id.is_not(None))
            .order_by(Membership.created_at.asc())
        )).all()
        for uid, gid in mrows:
            owner_group.setdefault(uid, gid)

    gids = {s.group_id for s in rows if s.group_id} | set(owner_group.values())
    gmap: dict[str, tuple[str, str | None]] = {}
    org_names: dict[str, str] = {}
    if gids:
        prows = (await db.execute(
            select(Project.id, Project.name, Project.org_id).where(Project.id.in_(gids))
        )).all()
        gmap = {pid: (pname, org_id) for pid, pname, org_id in prows}
        org_ids = {o for _, o in gmap.values() if o}
        if org_ids:
            org_names = {
                oid: on for oid, on in
                (await db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(org_ids)))).all()
            }

    def _read(s: Session) -> SessionRead:
        # Prefer the session's group, falling back to the owner's membership group.
        gid = s.group_id or owner_group.get(s.owner_user_id)
        gname, org_id = gmap.get(gid, (None, None)) if gid else (None, None)
        return _session_read(
            s, owner_name=onames.get(s.owner_user_id),
            group_name=gname, org_id=org_id, org_name=org_names.get(org_id) if org_id else None,
        )

    return [_read(s) for s in rows]


@router.get("/sessions/events")
async def sessions_monitor_events(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    """Admin session/queue monitor SSE stream.

    NOTE: this literal route MUST be declared before ``/sessions/{session_id}`` — otherwise the
    parameterized route captures ``/sessions/events`` (session_id="events") and returns 404.
    """
    principal.require(action="session.monitor")
    return EventSourceResponse(
        _sse_events([_QUEUE_CHANNEL], [_MONITOR_PATTERN], request)
    )


@router.get("/sessions/gpu-availability")
async def gpu_availability(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Free GPU capacity per model, used by the session wizard to disable tiers. member and above.

    Groups the currently ready devices by model and returns each device's free memory, free cores,
    and mode. The console uses this to disable tiers that would not fit on a single card of the
    selected model, since the scheduler requires a fit on one device.

    This is a literal path, so it must be declared before ``/sessions/{session_id}`` — the same
    reason the events route is."""
    principal.require(action="session.read")
    devs = (
        await db.scalars(select(GpuDevice).where(GpuDevice.status == "ready"))
    ).all()
    by_model: dict[str, dict] = {}
    for d in devs:
        m = by_model.setdefault(
            d.model, {"gpu_model": d.model, "card_mem_mb": 0, "devices": []}
        )
        m["card_mem_mb"] = max(m["card_mem_mb"], d.total_mem_mb or 0)
        m["devices"].append({
            "free_mem_mb": (d.total_mem_mb or 0) - (d.used_mem_mb or 0),
            "free_cores": (d.total_cores or 0) - (d.used_cores or 0),
            "total_mem_mb": d.total_mem_mb or 0,
            "total_cores": d.total_cores or 0,
            "mode": d.mode or "fractional",
        })
    return {"data": list(by_model.values())}


@router.get("/sessions/{session_id}", response_model=SessionRead)
async def get_session(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single session (owner·admin)."""
    sess = await _load_session(db, session_id)
    _require_access(principal, sess)
    return _session_read(sess)


@router.post("/sessions/{session_id}/start", response_model=SessionRead)
async def start_session(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Resume billing state machine."""
    sess = await _load_session(db, session_id)
    _require_access(principal, sess)
    await SessionService(db).start(session_id)
    sess = await _load_session(db, session_id)
    return _session_read(sess)


@router.post("/sessions/{session_id}/stop", response_model=SessionRead)
async def stop_session(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Stop (pause billing) + finalize remaining consume."""
    sess = await _load_session(db, session_id)
    _require_access(principal, sess)
    await SessionService(db).stop(session_id)
    sess = await _load_session(db, session_id)
    return _session_read(sess)


@router.post("/sessions/{session_id}/restart", response_model=SessionRead)
async def restart_session(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Restart a session."""
    sess = await _load_session(db, session_id)
    _require_access(principal, sess)
    await SessionService(db).restart(session_id)
    sess = await _load_session(db, session_id)
    return _session_read(sess)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_202_ACCEPTED)
async def terminate_session(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Terminate + settle."""
    sess = await _load_session(db, session_id)
    _require_access(principal, sess)
    await SessionService(db).terminate(session_id)
    return {"session_id": session_id, "status": "terminating"}


@router.post("/sessions/{session_id}/force-terminate", status_code=status.HTTP_202_ACCEPTED)
async def force_terminate(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Admin force-terminate without owner consent."""
    sess = await _load_session(db, session_id)
    principal.require(action="session.force_terminate", group_id=sess.group_id)
    await SessionService(db).terminate(session_id, forced=True)
    return {"session_id": session_id, "status": "terminating"}


@router.post("/sessions/bulk-terminate", status_code=status.HTTP_202_ACCEPTED)
async def bulk_terminate(
    body: BulkTerminateRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Force-terminate multiple sessions; per-target result array, idempotent."""
    principal.require(action="session.force_terminate")
    svc = SessionService(db)
    results: list[dict] = []
    terminated = skipped = failed = 0
    for sid in body.session_ids:
        sess = await db.get(Session, sid)
        if sess is None or sess.deleted_at is not None:
            failed += 1
            results.append({"session_id": sid, "status": "failed", "error": "not_found"})
            continue
        # scope guard per target: 403 if any out-of-scope
        principal.require(action="session.force_terminate", group_id=sess.group_id)
        if sess.status == "terminated":
            skipped += 1
            results.append({"session_id": sid, "status": "skipped"})
            continue
        try:
            await svc.terminate(sid, forced=True)
            terminated += 1
            results.append({"session_id": sid, "status": "terminating"})
        except InvalidStateTransition as exc:
            failed += 1
            results.append({"session_id": sid, "status": "failed", "error": exc.code})
    return {
        "requested": len(body.session_ids),
        "terminated": terminated,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


@router.get("/sessions/{session_id}/logs")
async def session_logs(
    session_id: str,
    tail: int = Query(default=200, ge=1, le=5000),
    stream: str = Query(default="all", pattern="^(stdout|stderr|all)$"),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Recent container/lifecycle log lines.

    Log content originates in the workload Pod (operator/k8s layer). This plane reads the lines the
    operator has streamed into Redis (``logs:{ses}`` list, newest appended); returns the tail.
    """
    sess = await _load_session(db, session_id)
    _require_access(principal, sess)

    r = get_redis()
    raw = await r.lrange(f"logs:{session_id}", -tail, -1)
    lines: list[dict] = []
    for item in raw:
        try:
            rec = json.loads(item)
        except (ValueError, TypeError):
            rec = {"ts": None, "stream": "stdout", "message": str(item)}
        if stream != "all" and rec.get("stream") not in (stream, None):
            continue
        lines.append(rec)
    return {"session_id": session_id, "lines": lines}


@router.get("/sessions/{session_id}/connections", response_model=list[ConnectionInfo])
async def session_connections(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Issue one-time cnx_ connection tokens."""
    sess = await _load_session(db, session_id)
    _require_access(principal, sess)
    if sess.status != "running":
        raise InvalidStateTransition(
            "session is not running", {"status": sess.status}
        )

    from app.core.config import settings as _settings

    svc = ConnectionTokenService()
    # Path-based routing under one domain: {scheme}://{domain}/proxy/{cr}/{path}. The operator
    # creates an Ingress on the same console host with a /proxy/{cr} prefix and maps each subpath to
    # a port — /code to vscode:8080, /lab to jupyter:8888, /terminal to ttyd:7681. SESSION_DOMAIN is
    # the console domain. The custom resource name is RFC 1123 (lower-cased, '_' to '-'), the same
    # transformation the operator applies to its custom resource and Ingress names.
    cr_name = session_id.lower().replace("_", "-")
    host_base = f"{_settings.SESSION_URL_SCHEME}://{_settings.SESSION_DOMAIN}/proxy/{cr_name}"
    kinds = {
        # code-server lives at the /code subpath, with the ingress stripping the prefix. The
        # trailing slash is required so relative assets such as ./_static resolve against
        # /proxy/{cr}/code/.
        "vscode": f"{host_base}/code/",
        "jupyter": f"{host_base}/lab",
        "terminal": f"{host_base}/terminal",   # web terminal (ttyd)
    }
    expires_at = datetime.now(UTC) + timedelta(
        seconds=_settings.CONNECTION_TOKEN_TTL_SEC
    )
    out: list[ConnectionInfo] = []
    for kind, url in kinds.items():
        token = await svc.issue(session_id, kind, sess.owner_user_id)
        # The connect token travels in the query string; the ingress auth-url
        # (/internal/connect/verify) redeems it once.
        out.append(
            ConnectionInfo(
                kind=kind, url=f"{url}?gshare_cnx={token}", connection_token=token,
                expires_at=expires_at, command=None,
            )
        )
    return out


@router.post("/sessions/{session_id}/checkpoints", status_code=status.HTTP_202_ACCEPTED)
async def create_checkpoint(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Create a point-in-time checkpoint (async)."""
    from app.core import ids

    sess = await _load_session(db, session_id)
    _require_access(principal, sess)
    if sess.status != "running":
        raise InvalidStateTransition(
            "session is not running", {"status": sess.status}
        )
    ckp = SessionCheckpoint(
        id=ids.new("checkpoint"),
        session_id=session_id,
        type="criu",
        storage_ref=None,
        size_bytes=None,
    )
    db.add(ckp)
    await db.commit()
    await db.refresh(ckp)
    return {
        "id": ckp.id,
        "session_id": session_id,
        "type": ckp.type,
        "status": "creating",
        "storage_ref": ckp.storage_ref,
        "size_bytes": ckp.size_bytes,
    }


@router.get("/sessions/{session_id}/checkpoints")
async def list_checkpoints(
    session_id: str,
    page: Pagination = Depends(),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """List checkpoints for a session, newest first."""
    sess = await _load_session(db, session_id)
    _require_access(principal, sess)
    base = select(SessionCheckpoint).where(SessionCheckpoint.session_id == session_id)
    total = (
        await db.scalar(
            select(func.count())
            .select_from(SessionCheckpoint)
            .where(SessionCheckpoint.session_id == session_id)
        )
    ) or 0
    rows = (
        await db.scalars(
            base.order_by(SessionCheckpoint.created_at.desc())
            .offset(page.offset)
            .limit(page.size)
        )
    ).all()
    data = [
        {
            "id": c.id,
            "session_id": c.session_id,
            "type": c.type,
            "storage_ref": c.storage_ref,
            "size_bytes": c.size_bytes,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in rows
    ]
    return {
        "data": data,
        "pagination": {"page": page.page, "size": page.size, "total": total},
    }


@router.post("/sessions/{session_id}/checkpoints/{checkpoint_id}/restore",
             status_code=status.HTTP_202_ACCEPTED)
async def restore_checkpoint(
    session_id: str,
    checkpoint_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Restore a session from a checkpoint (async)."""
    sess = await _load_session(db, session_id)
    _require_access(principal, sess)
    ckp = await db.get(SessionCheckpoint, checkpoint_id)
    if ckp is None or ckp.session_id != session_id:
        raise NotFound("checkpoint not found", {"checkpoint_id": checkpoint_id})
    return {
        "session_id": session_id,
        "checkpoint_id": checkpoint_id,
        "status": "restoring",
        "target": "same_session",
        "started_at": datetime.now(UTC).isoformat(),
    }


async def _sse_events(channels: list[str] | None, patterns: list[str] | None,
                      request: Request):
    """Generic SSE generator over Redis pub/sub with periodic heartbeat.

    Subscribes to the given channel(s)/pattern(s), yields each published message as an SSE event,
    and emits a heartbeat every _HEARTBEAT_SEC seconds. Terminates cleanly on client disconnect or
    cancellation, always unsubscribing/closing the pubsub.
    """
    r = get_redis()
    pubsub = r.pubsub()
    seq = 0
    try:
        if channels:
            await pubsub.subscribe(*channels)
        if patterns:
            await pubsub.psubscribe(*patterns)
        while True:
            if await request.is_disconnected():
                break
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_HEARTBEAT_SEC
            )
            seq += 1
            if msg is None:
                yield {
                    "event": "heartbeat",
                    "id": str(seq),
                    "data": json.dumps(
                        {"at": datetime.now(UTC).isoformat()}
                    ),
                }
                continue
            data = msg.get("data")
            if not isinstance(data, str):
                continue
            event_type = "message"
            payload = data
            try:
                parsed = json.loads(data)
                if isinstance(parsed, dict) and "event" in parsed:
                    event_type = parsed.get("event") or "message"
                    payload = json.dumps(parsed.get("data", parsed))
            except (ValueError, TypeError):
                pass
            yield {"event": event_type, "id": str(seq), "data": payload}
    except asyncio.CancelledError:
        raise
    finally:
        try:
            if patterns:
                await pubsub.punsubscribe()
            if channels:
                await pubsub.unsubscribe()
        finally:
            closer = getattr(pubsub, "aclose", None) or pubsub.close
            await closer()


@router.get("/sessions/{session_id}/events")
async def session_events(
    session_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Per-session SSE event stream.

    Subscribes to the session's status-change channel (Redis pub/sub fed by status_sync) and pushes
    status_changed / phase / allocation / heartbeat events. 404/403 are raised before the stream is
    established; the stream terminates cleanly on client disconnect.
    """
    sess = await _load_session(db, session_id)
    _require_access(principal, sess)
    channel = _SESSION_CHANNEL.format(session_id=session_id)
    return EventSourceResponse(_sse_events([channel], None, request))


