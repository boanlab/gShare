"""SchedulerService — session admission gate.

Gate order (FIXED): resource policy -> budget -> credit hold -> available-VRAM precheck
(Σ gpu_mem_mb ≤ total, FOR UPDATE) -> queue gating -> persist desired state + operator handoff.
CPU (free) sessions bypass budget/hold/VRAM/queue/GPU entirely.

This plane only makes the *admission decision*. Actual Pod creation / hami-scheduler delegation /
binding is the Go operator, which reconciles the desired CR/row.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.session import SessionCreate, SessionRead
from app.auth.rbac import Principal
from app.cluster.handoff import Handoff
from app.core import ids
from app.core.config import settings
from app.core.cuda import cuda_compatible
from app.core.errors import (
    Forbidden,
    IdempotencyInProgress,
    ImbalancedAllocation,
    IncompatibleImage,
    NoCapacity,
    NotFound,
    QuotaExceeded,
    VramBelowMinimum,
)
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.models import (
    Allocation,
    Cluster,
    CreditWallet,
    GpuDevice,
    GpuNode,
    Image,
    Offering,
    Project,
    QueueEntry,
    ResourcePolicy,
    Session,
    StorageVolume,
    VolumeMount,
    VolumePermission,
)
from app.domain.budget_service import BudgetService
from app.domain.credit_engine import CreditEngine
from app.domain.pricing import compute_credit_per_hour, round_credit

log = get_logger(__name__)

QUEUE_KEY = "gshare:queue"
# score = PRIORITY_WEIGHT * priority + aging(enqueued_at).
PRIORITY_WEIGHT = 1_000_000.0


@dataclass
class _Estimate:
    """Pricing estimate carried into the budget gate / credit hold."""

    credit_per_hour: Decimal
    occupancy: float
    hold_amount: Decimal


def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class SchedulerService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.credit = CreditEngine(db)
        self.budget = BudgetService(db)
        self.handoff = Handoff(db)

    async def create_session(
        self, req: SessionCreate, principal: Principal, idem: str
    ) -> SessionRead:
        """Run the admission gate and hand off desired state.

        Steps:
          0 persist pending session row (status=pending).
          if resource_class == "cpu": check cpu quota -> apply_desired (no credit/GPU) -> return.
          1 resource policy quota check -> 409 quota_exceeded
          2 budget gate (before hold) -> 409 budget_exceeded
          3 credit hold (idempotent hold:{ses}) -> 402 insufficient_credit
          4 available-VRAM precheck (FOR UPDATE); if short -> enqueue (hold kept), return pending
          5 persist desired state + operator handoff (GShareSession CR apply, CRD-primary)
        """
        # Auto-select a cluster when the caller did not pin one (multi-cluster deployments).
        if not req.cluster_id:
            req.cluster_id = await self._resolve_cluster(req)

        # Reject an unknown offering/image/wallet with 404 rather than letting an FK violation 500.
        await self._validate_refs(req)

        # Enforce mount permissions server-side: existence, access, mode (rw), and ROX -> ro.
        await self._validate_mounts(req, principal)

        # A session may only bill the requester's own personal wallet; charging another's is
        # blocked.
        await self._validate_billing_wallet(req, principal)

        # Idempotency: concurrent requests carrying the same Idempotency-Key must return one
        # session. Claim the key atomically with Redis SET NX; a loser polls for the winner's
        # session id.
        redis = get_redis()
        ikey = f"idem:session:{principal.user_id}:{idem}"
        prior = await redis.get(ikey)
        if prior and prior != "pending":
            done = await self.db.get(Session, prior)
            if done is not None:
                return SessionRead.model_validate(done)
        if not await redis.set(ikey, "pending", nx=True, ex=3600):
            for _ in range(50):  # ~5s, waiting for the in-flight request to create the session
                v = await redis.get(ikey)
                if v and v != "pending":
                    done = await self.db.get(Session, v)
                    if done is not None:
                        return SessionRead.model_validate(done)
                await asyncio.sleep(0.1)
            raise IdempotencyInProgress("session create already in progress for this key")

        # 0 persist pending session row (status=pending).
        sess = await self._persist_pending(req, principal)
        await redis.set(ikey, sess.id, ex=3600)  # every later return path resolves to this session
        sid = sess.id   # capture before a rollback expires `sess`; a lazy load in `except` would raise MissingGreenlet

        # A pending row left behind by a rejected gate or a failed handoff still counts against
        # capacity (available cpu/mem/disk over status in pending/preparing/reserved/running), so it
        # becomes a phantom reservation that blocks later sessions. On any exception we therefore
        # release the reservation (allocation + device.used) and mark the session `error` so it
        # drops out of the active set, keeping the row for audit. Success paths return inside the
        # try.
        try:
            # 0.5 node CPU/RAM/disk availability gate, for both cpu and gpu sessions; short capacity
            # raises NoCapacity. GPU VRAM is handled by step 4 below, and disk is skipped until the
            # nodes have reported their capacity.
            async with self.db.begin():
                await self._validate_capacity(sess)

            # 0' CPU session: skip the VRAM, queue, and GPU steps, but still gate the budget and
            # take a hold whenever the rate is above zero.
            if req.resource_class == "cpu":
                # Wrap the read preamble in its own tx and close it, or budget.gate's begin()
                # collides.
                async with self.db.begin():
                    await self._check_cpu_quota(req, principal, exclude_session_id=sess.id)
                    est = await self._estimate(req)   # rate proportional to cpu/mem/disk
                    scope_chain = await self._scope_chain(req)
                if req.billing_wallet_id and est.hold_amount > Decimal("0"):
                    async with self.db.begin():
                        await self.budget.gate(scope_chain, est)
                    await self.credit.hold(req.billing_wallet_id, est.hold_amount, key=f"hold:{sess.id}")
                async with self.db.begin():
                    await self.handoff.apply_desired(sess, req)
                return SessionRead.model_validate(sess)

            # ── resource_class == "gpu" ──
            # 1 resource policy quota check -> 409 quota_exceeded.
            # (read-only preamble in its own tx so sub-services own a clean transaction boundary.)
            async with self.db.begin():
                await self._check_quota(req, principal, exclude_session_id=sess.id)
                est = await self._estimate(req)
                scope_chain = await self._scope_chain(req)

            # 2 budget gate (FinOps, before hold) -> 409 budget_exceeded.
            # Own begin() so the gate's auto-begun tx closes before step 4 opens its own.
            async with self.db.begin():
                await self.budget.gate(scope_chain, est)

            # 3 credit hold (idempotent hold:{ses}) -> 402 insufficient_credit.
            await self.credit.hold(req.billing_wallet_id, est.hold_amount, key=f"hold:{sess.id}")

            # 4 available-VRAM precheck + reserve (FOR UPDATE). Physical GPU is NOT decided here
            # (operator delegates to hami-scheduler) — we only reserve capacity via a pending
            # Allocation to close the over-commit window reserved -> bound on the running event.
            async with self.db.begin():
                placed = await self.reserve_slice(sess, req)
                # A preemptible session may borrow a card a resident yielded when nothing else fits.
                if not placed and getattr(sess, "preemptible", False):
                    placed = await self.reserve_spot_slice(sess, req)

            # Active preemption: when neither a normal placement nor a borrow worked and this
            # request outranks someone, yield a lower-priority session losslessly and borrow its
            # card. `stop` commits its own transaction, so this must run outside a begin() block.
            if not placed and sess.priority > 0:
                if await self._preempt_lower_priority(sess, req):
                    async with self.db.begin():
                        placed = await self.reserve_spot_slice(sess, req)

            if not placed:
                # No capacity, no fitting card, nothing to preempt -> enqueue, keep the hold, return
                # pending.
                async with self.db.begin():
                    await self._enqueue(sess, req)
                return SessionRead.model_validate(sess)

            # 5 persist desired state + operator handoff (GShareSession CR apply, CRD-primary).
            async with self.db.begin():
                await self.handoff.apply_desired(sess, req)
            return SessionRead.model_validate(sess)
        except Exception:
            await self._release_reservation(sid)
            raise

    async def _resolve_cluster(self, req: SessionCreate) -> str:
        """Pick a cluster automatically when the request did not name one.

        Among connected clusters:
          - GPU: the one with the most free capacity that can still accept the request
            (mode, model, VRAM, cores). If none can, the one with the most matching devices,
            so the session queues where it has the best chance of running.
          - CPU: the first connected cluster with a ready node.

        Raises NoCapacity when no cluster is connected. The read is wrapped in its own transaction
        so _validate_refs opens a clean one. """
        async with self.db.begin():
            clusters = (
                await self.db.scalars(
                    select(Cluster)
                    .where(Cluster.status == "connected", Cluster.deleted_at.is_(None))
                    .order_by(Cluster.created_at.asc())
                )
            ).all()
            if not clusters:
                raise NoCapacity("no connected cluster is available")
            if len(clusters) == 1:
                return clusters[0].id

            if req.resource_class == "cpu":
                for c in clusters:
                    has_node = await self.db.scalar(
                        select(GpuNode.id)
                        .where(GpuNode.cluster_id == c.id, GpuNode.status == "ready")
                        .limit(1)
                    )
                    if has_node:
                        return c.id
                return clusters[0].id

            # GPU: score clusters by the free capacity of ready devices matching the offering and
            # mode.
            offering = await self.db.get(Offering, req.offering_id)
            model = offering.gpu_model if offering else None
            req_mem = req.gpu_mem_mb or 0
            req_cores = req.gpu_cores or 0
            exclusive = req.mode == "exclusive"

            best_fit: tuple[int, str] | None = None   # (total headroom, cluster_id) — best among clusters that fit
            best_any: tuple[int, str] | None = None   # (matching device count, cluster_id) — fallback when nothing fits
            for c in clusters:
                stmt = select(GpuDevice).where(
                    GpuDevice.cluster_id == c.id, GpuDevice.status == "ready"
                )
                if req.mode is not None:
                    stmt = stmt.where(GpuDevice.mode == req.mode)
                if model is not None:
                    stmt = stmt.where(GpuDevice.model == model)
                devs = (await self.db.scalars(stmt)).all()
                if not devs:
                    continue
                fits = False
                fit_free = 0
                for d in devs:
                    free_mem = (d.total_mem_mb or 0) - (d.used_mem_mb or 0)
                    free_cores = 100 - (d.used_cores or 0)
                    if exclusive:
                        if (d.used_mem_mb or 0) == 0 and (d.used_cores or 0) == 0:
                            fits = True
                            fit_free += free_mem
                    elif free_mem >= req_mem and free_cores >= req_cores:
                        fits = True
                        fit_free += free_mem
                if fits and (best_fit is None or fit_free > best_fit[0]):
                    best_fit = (fit_free, c.id)
                if best_any is None or len(devs) > best_any[0]:
                    best_any = (len(devs), c.id)
            if best_fit is not None:
                return best_fit[1]
            if best_any is not None:
                return best_any[1]
            return clusters[0].id

    def _validate_balance(self, req: SessionCreate, ref_mem: int) -> None:
        """Check that a fractional allocation balances cores against VRAM (anti-fragmentation).

        The UI only offers balanced ratios, but the API accepts arbitrary values. An asymmetric
        allocation — 1 GB of VRAM with 100% of the cores, say — leaves VRAM free while making the card
        useless for compute to every other session on it, so it is rejected: when the core share
        (cores/100) and the VRAM share (mem / full card) diverge beyond the allowed factor, respond
        422. Exclusive sessions take the whole card and are exempt.

        ``ref_mem`` is the full-card VRAM of the chosen GPU model (offering.gpu_mem_mb). In a mixed
        fleet of 4090, 5090, and PRO 5000/6000 cards the share has to be judged against the actual
        card capacity, which differs per model."""
        if req.resource_class != "gpu" or req.mode != "fractional":
            return
        mem_mb = req.gpu_mem_mb or 0
        # Minimum VRAM floor: a slice below 1 GB cannot even hold a CUDA context. The frontend
        # disables such tiers; this is defence in depth against direct API calls. (Exclusive has
        # already returned above.)
        if mem_mb < settings.GPU_MIN_FRACTIONAL_MEM_MB:
            raise VramBelowMinimum(
                f"fractional slice VRAM ({mem_mb} MB) is below the minimum of "
                f"{settings.GPU_MIN_FRACTIONAL_MEM_MB} MB",
                {"gpu_mem_mb": mem_mb, "min_mem_mb": settings.GPU_MIN_FRACTIONAL_MEM_MB},
            )
        cores = req.gpu_cores or 0
        ref = ref_mem or settings.GPU_REFERENCE_MEM_MB or 1
        mem_frac = mem_mb / ref
        core_frac = cores / 100
        tol = settings.GPU_BALANCE_TOLERANCE
        floor = settings.GPU_BALANCE_FLOOR
        hi = max(mem_frac, core_frac)
        lo = max(min(mem_frac, core_frac), floor)
        if hi > tol * lo:
            raise ImbalancedAllocation(
                "GPU core and VRAM shares are too far apart; allocate them proportionally.",
                {
                    "gpu_mem_mb": mem_mb,
                    "gpu_cores": cores,
                    "model_mem_mb": ref,
                    "mem_fraction": round(mem_frac, 3),
                    "core_fraction": round(core_frac, 3),
                    "max_ratio": tol,
                },
            )

    async def _validate_billing_wallet(self, req: SessionCreate, principal: Principal) -> None:
        """The billing wallet must be the requester's own personal wallet.

        `super_admin` is exempt for operational work, and a missing billing wallet (a free CPU
        session, for example) passes. Naming another user's, a group's, or an organization's wallet
        returns 403 — this is what stops a direct API call from draining someone else's credits.
        """
        wid = getattr(req, "billing_wallet_id", None)
        if not wid or "super_admin" in principal.global_roles:
            return
        # Wrap the read in its own tx and close it, so the following _persist_pending begin() is
        # clean.
        async with self.db.begin():
            wallet = await self.db.get(CreditWallet, wid)
            owner_type = wallet.owner_type if wallet is not None else None
            owner_id = wallet.owner_id if wallet is not None else None
        if wallet is None:
            raise NotFound("billing wallet not found", {"billing_wallet_id": wid})
        if owner_type != "user" or owner_id != principal.user_id:
            raise Forbidden(
                "not permitted: a session can only be billed to your own personal wallet",
                {"billing_wallet_id": wid},
            )

    async def _validate_mounts(self, req: SessionCreate, principal: Principal) -> None:
        """Enforce, server-side, the mount permissions of the volumes a session attaches.

        Rules, based on VolumePermission (owner | rw | ro):
          - `super_admin`, and `group_admin` or above on a group volume, count as owner (rw allowed).
          - Everyone else needs an explicit VolumePermission row, or the mount is 403. The owner of a
            personal volume gets an `owner` row when it is created.
          - Mode: an rw mount requires role in {owner, rw} (or the admin case above); an ro mount
            only requires access.
          - A volume whose access_mode is ROX (read-only shared) rejects rw and mounts ro only.
        """
        mounts = getattr(req, "volume_mounts", None) or []
        if not mounts:
            return
        su = "super_admin" in principal.global_roles
        async with self.db.begin():
            for m in mounts:
                vol = await self.db.get(StorageVolume, m.volume_id)
                if vol is None or vol.deleted_at is not None:
                    raise NotFound("volume not found", {"volume_id": m.volume_id})
                # Admin case: super_admin, or group_admin and above on that group's volume.
                is_mgr = su or (
                    vol.scope == "group"
                    and principal.memberships.get(vol.scope_id) in ("group_admin", "org_admin")
                )
                role = await self.db.scalar(
                    select(VolumePermission.role).where(
                        VolumePermission.volume_id == vol.id,
                        VolumePermission.user_id == principal.user_id,
                    )
                )
                if not is_mgr and role is None:
                    raise Forbidden(
                        "not permitted: cannot mount a volume you have no access to",
                        {"volume_id": vol.id},
                    )
                want_rw = (m.mode or "rw") == "rw"
                can_rw = is_mgr or role in ("owner", "rw")
                if want_rw and not can_rw:
                    raise Forbidden(
                        "not permitted: no write access to this volume; it can only be mounted read-only",
                        {"volume_id": vol.id, "granted_role": role},
                    )
                if want_rw and vol.access_mode == "ROX":
                    raise Forbidden(
                        "not permitted: a read-only (ROX) volume cannot be mounted rw",
                        {"volume_id": vol.id, "access_mode": vol.access_mode},
                    )

    async def _validate_refs(self, req: SessionCreate) -> None:
        """Verify the referenced rows exist before creating the session — 404 instead of an FK 500.

        The read is wrapped in its own transaction so the following _persist_pending begin() is
        clean. The full-card VRAM used as the balance reference is the registered device's measured
        total_mem_mb: offering.gpu_mem_mb is the flavor's own share, so using it would compare a
        fraction against itself and always read as 1.0."""
        async with self.db.begin():
            offering = await self.db.get(Offering, req.offering_id)
            if offering is None:
                raise NotFound("offering not found", {"offering_id": req.offering_id})
            image = await self.db.get(Image, req.image_id)
            if image is None:
                raise NotFound("image not found", {"image_id": req.image_id})
            # CUDA compatibility: an image below the offering's min_cuda cannot run on that card.
            if offering.resource_class == "gpu":
                image_cuda = (image.tags or {}).get("cuda_version")
                if not cuda_compatible(image_cuda, offering.min_cuda):
                    raise IncompatibleImage(
                        f"image CUDA {image_cuda} is below the minimum CUDA "
                        f"{offering.min_cuda} required by {offering.gpu_model}",
                        {"image_cuda": image_cuda, "min_cuda": offering.min_cuda,
                         "gpu_model": offering.gpu_model},
                    )
            wid = getattr(req, "billing_wallet_id", None)
            if wid and await self.db.get(CreditWallet, wid) is None:
                raise NotFound("billing wallet not found", {"billing_wallet_id": wid})
            # Reference is the card's real full VRAM from device inventory; fall back to
            # configuration when inventory is missing — never to offering.gpu_mem_mb.
            ref_mem = None
            if offering.resource_class == "gpu" and offering.gpu_model:
                ref_mem = (
                    await self.db.scalars(
                        select(GpuDevice.total_mem_mb)
                        .where(
                            GpuDevice.cluster_id == req.cluster_id,
                            GpuDevice.model == offering.gpu_model,
                            GpuDevice.status == "ready",
                        )
                        .limit(1)
                    )
                ).first()
            ref_mem = ref_mem or settings.GPU_REFERENCE_MEM_MB
        # Balance check outside the transaction (pure arithmetic), against the per-model full card.
        self._validate_balance(req, ref_mem)

    async def _validate_capacity(self, sess: Session) -> None:
        """Node CPU/RAM/disk gate, shared by cpu and gpu sessions. It rejects in two stages.

        1. Single-node feasibility. A Guaranteed pod must land whole on one node, so a request larger
           than any ready node's *total* capacity can never be scheduled regardless of current usage.
           Rejecting at admission is what stops a large flavor thrown at a cluster of small nodes from
           leaving a pod Pending forever.
        2. Availability: available = sum over ready GpuNode of {cpu, mem, disk} minus the sum over
           active sessions of {cpu, mem_gb, disk_gb}. Both capacity and reservations come from the
           inventory and session snapshots, so nothing here is hard-coded to an environment.

        Precise per-node bin packing is the Kubernetes scheduler's job; this is a coarse gate. Disk
        is skipped while the nodes have not reported their capacity. """
        need_cpu, need_mem, need_disk = sess.cpu or 0, sess.mem_gb or 0, sess.disk_gb or 0
        if not (need_cpu or need_mem or need_disk):
            return
        nodes = (
            await self.db.scalars(
                select(GpuNode).where(
                    GpuNode.cluster_id == sess.cluster_id, GpuNode.status == "ready"
                )
            )
        ).all()
        # 1. Single-node feasibility. A node reporting 0 for a resource is skipped for
        #    that resource.
        max_cpu = max((n.cpu or 0 for n in nodes), default=0)
        max_node_mem = max((n.mem or 0 for n in nodes), default=0)
        max_node_disk = max((n.disk or 0 for n in nodes), default=0)
        if need_cpu and max_cpu and need_cpu > max_cpu:
            raise NoCapacity(
                "cannot be scheduled: requested CPU exceeds the capacity of any single node",
                {"resource": "cpu", "reason": "node_too_small", "need": need_cpu, "max_node": max_cpu},
            )
        if need_mem and max_node_mem and need_mem > max_node_mem:
            raise NoCapacity(
                "cannot be scheduled: requested memory exceeds the capacity of any single node",
                {"resource": "memory", "reason": "node_too_small", "need_gb": need_mem, "max_node_gb": max_node_mem},
            )
        if need_disk and max_node_disk and need_disk > max_node_disk:
            raise NoCapacity(
                "cannot be scheduled: requested disk exceeds the capacity of any single node",
                {"resource": "disk", "reason": "node_too_small", "need_gb": need_disk, "max_node_gb": max_node_disk},
            )
        # 2. Aggregate availability gate.
        cap_cpu = sum(n.cpu or 0 for n in nodes)
        cap_mem = sum(n.mem or 0 for n in nodes)
        cap_disk = sum(n.disk or 0 for n in nodes)
        actives = (
            await self.db.scalars(
                select(Session).where(
                    Session.cluster_id == sess.cluster_id,
                    Session.id != sess.id,
                    Session.deleted_at.is_(None),
                    Session.status.in_(("pending", "preparing", "reserved", "running")),
                )
            )
        ).all()
        used_cpu = sum(s.cpu or 0 for s in actives)
        used_mem = sum(s.mem_gb or 0 for s in actives)
        used_disk = sum(s.disk_gb or 0 for s in actives)
        if need_cpu and need_cpu > cap_cpu - used_cpu:
            raise NoCapacity(
                "no CPU capacity available",
                {"resource": "cpu", "need": need_cpu, "available": max(0, cap_cpu - used_cpu)},
            )
        if need_mem and need_mem > cap_mem - used_mem:
            raise NoCapacity(
                "no memory capacity available",
                {"resource": "memory", "need_gb": need_mem, "available_gb": max(0, cap_mem - used_mem)},
            )
        if need_disk and cap_disk > 0 and need_disk > cap_disk - used_disk:
            raise NoCapacity(
                "no disk capacity available",
                {"resource": "disk", "need_gb": need_disk, "available_gb": max(0, cap_disk - used_disk)},
            )

    async def _persist_pending(self, req: SessionCreate, principal: Principal) -> Session:
        async with self.db.begin():
            offering = await self.db.get(Offering, req.offering_id)
            # CPU, RAM, and disk come from the compute preset when the request carries one, and from
            # the offering otherwise.
            cpu_v = req.cpu if req.cpu is not None else (offering.cpu if offering is not None else None)
            mem_v = req.mem_gb if req.mem_gb is not None else (offering.mem_gb if offering is not None else None)
            disk_v = req.disk_gb if req.disk_gb is not None else (offering.disk_gb if offering is not None else None)
            # CPU sessions are priced proportionally to cpu/mem/disk; GPU sessions use the offering
            # rate, with occupancy applied at hold and consume time.
            if req.resource_class == "cpu":
                snapshot = compute_credit_per_hour(cpu_v, mem_v, disk_v)
            else:
                snapshot = offering.credit_per_hour if offering is not None else Decimal("0")
            # Spot (preemptible) sessions can be reclaimed, so they get the discounted rate (normal
            # rate x SPOT_DISCOUNT). Exclusive always qualifies; fractional only under
            # YIELD_FRACTIONAL_BORROW, the HAMi extender path, because the device-plugin bypass
            # cannot borrow fractionally. (build/hami-fork)
            preemptible = bool(
                getattr(req, "preemptible", False) and req.resource_class != "cpu"
                and (req.mode == "exclusive" or settings.YIELD_FRACTIONAL_BORROW)
            )
            if preemptible and req.resource_class != "cpu":
                snapshot = (snapshot * Decimal(str(settings.SPOT_DISCOUNT))).quantize(Decimal("0.01"))
            # Lossless-pause eligibility: the offering opts in, the session is exclusive, and the
            # cluster has a ready node capable of it (cuda-checkpoint plus CRIU).
            lossless = bool(
                offering is not None
                and getattr(offering, "lossless_pause", False)
                and req.mode == "exclusive"
            )
            if lossless:
                lossless = (
                    await self.db.scalar(
                        select(GpuNode.id)
                        .where(
                            GpuNode.cluster_id == req.cluster_id,
                            GpuNode.lossless_capable.is_(True),
                            GpuNode.status == "ready",
                        )
                        .limit(1)
                    )
                ) is not None
            sess = Session(
                id=ids.new("session"),
                name=getattr(req, "name", None),
                owner_user_id=principal.user_id,
                group_id=req.group_id,
                cluster_id=req.cluster_id,
                cluster_mode=req.cluster_mode,
                offering_id=req.offering_id,
                image_id=req.image_id,
                resource_class=req.resource_class,
                mode=req.mode,
                gpu_mem_mb=req.gpu_mem_mb,
                gpu_cores=req.gpu_cores,
                cpu=cpu_v,
                mem_gb=mem_v,
                disk_gb=disk_v,
                billing_wallet_id=req.billing_wallet_id,
                lossless_pause=lossless,
                # When eligible (a cuda-checkpoint node and exclusive), use yield mode: pause keeps
                # the pod and evicts VRAM, so the resume is lossless — strictly better than a CRIU
                # checkpoint. Otherwise fall back to cold.
                pause_mode="yield" if lossless else "cold",
                # Spot eligibility is exclusive-only; the snapshot taken above is discounted too.
                preemptible=preemptible,
                priority=int(getattr(req, "priority", 0) or 0),
                status="pending",
                credit_per_hour_snapshot=snapshot,
            )
            self.db.add(sess)
            # Persist the mounts so the session detail can show them and a mounted volume cannot be
            # deleted underneath it (see _active_mount_session_ids).
            for m in getattr(req, "volume_mounts", None) or []:
                self.db.add(
                    VolumeMount(
                        id=ids.new("mount"),
                        session_id=sess.id,
                        volume_id=m.volume_id,
                        mount_path=m.mount_path,
                        mode=m.mode or "rw",
                    )
                )
        # Pending row is durable now (survives a later gate rejection for audit).
        return sess

    async def reschedule_from_queue(self) -> None:
        """Dequeue highest-priority queued session on capacity return; queue_ticker worker.

        ZPOPMAX the top score (highest priority), re-run the admission VRAM precheck; on success
        bind (reserve capacity + handoff), on still-no-capacity push the entry back (re-ZADD).
        """
        redis = get_redis()
        popped = await redis.zpopmax(QUEUE_KEY, 1)
        if not popped:
            return
        member, score = popped[0]
        session_id = member

        admitted: tuple[Session, SessionCreate] | None = None
        push_back = False
        async with self.db.begin():
            entry = (
                await self.db.scalars(
                    select(QueueEntry).where(QueueEntry.session_id == session_id)
                )
            ).first()
            sess = await self.db.get(Session, session_id)
            if sess is None or sess.status != "pending":
                # Session vanished or already progressed — drop the stale queue entry.
                if entry is not None:
                    await self.db.delete(entry)
            else:
                req = self._req_from_entry(entry, sess)
                placed = await self.reserve_slice(sess, req)
                if not placed and getattr(sess, "preemptible", False):
                    placed = await self.reserve_spot_slice(sess, req)
                if placed:
                    if entry is not None:
                        await self.db.delete(entry)
                    admitted = (sess, req)
                else:
                    # Still no capacity — push back onto the queue at the same score.
                    push_back = True

        if push_back:
            await redis.zadd(QUEUE_KEY, {member: score})
            return
        if admitted is not None:
            sess, req = admitted
            try:
                async with self.db.begin():
                    await self.handoff.apply_desired(sess, req)
            except Exception:
                await self._release_reservation(sess.id)
                await redis.zadd(QUEUE_KEY, {member: score})  # put it back on the queue so it can be retried
                raise

    async def _reconcile_device_usage(self, devs) -> None:
        """Resynchronise device.used_* from the sum of live (unreleased) allocations.

        used_mem_mb and used_cores are cache counters bumped on reserve and release, so they drift
        over time. Negative drift causes over-assignment at dequeue — more than the physical card
        holds — and the pod ends up stuck Pending. Correcting to ground truth immediately before the
        reservation decision (the caller holds FOR UPDATE on `devs`) prevents both over- and
        under-assignment.
        """
        for d in devs:
            row = (
                await self.db.execute(
                    select(
                        func.coalesce(func.sum(Allocation.gpu_mem_mb), 0),
                        func.coalesce(func.sum(Allocation.gpu_cores), 0),
                    ).where(
                        Allocation.device_id == d.id,
                        Allocation.ended_at.is_(None),
                        Allocation.kind != "spot",  # a borrow leaves the card physically free, so it is not counted
                    )
                )
            ).one()
            d.used_mem_mb = int(row[0] or 0)
            d.used_cores = int(row[1] or 0)

    async def reserve_slice(self, sess: Session, req: SessionCreate) -> bool:
        """Reserve a GPU slice for a session — the primitive shared by create, dequeue, and resume.

        Runs inside a transaction opened by the caller, which also owns the FOR UPDATE lock and the
        commit. On success it creates the reserving Allocation, increments device.used, sets
        sess.device_total_mem_mb, and returns True. When no card fits it changes nothing and returns
        False, leaving the enqueue / 409 / pushback decision to the caller.
        """
        mode = sess.mode or req.mode
        cluster_id = sess.cluster_id or req.cluster_id
        req_mem = (sess.gpu_mem_mb if sess.gpu_mem_mb is not None else req.gpu_mem_mb) or 0
        req_cores = (sess.gpu_cores if sess.gpu_cores is not None else req.gpu_cores) or 0
        exclusive = mode == "exclusive"
        devs = (
            await self.db.scalars(
                select(GpuDevice)
                .where(
                    GpuDevice.cluster_id == cluster_id,
                    GpuDevice.status == "ready",
                    GpuDevice.mode == mode,
                    # Yielded and lent cards are out of the normal placement pool: the resident
                    # still holds them (its pod is alive) and only spot sessions may use them. This
                    # check holds even when inventory drift has reset used_* to 0.
                    GpuDevice.lend_state == "",
                )
                .with_for_update()
            )
        ).all()
        await self._reconcile_device_usage(devs)  # correct counter drift before deciding
        # Exclusive: reserve one completely empty card whole (used = total) so no_overcommit blocks
        # co-tenancy. Fractional: best-fit the slice onto a card, allowing several sessions per
        # card.
        target, eff_mem, eff_cores = self._reserve_target(devs, req_mem, req_cores, exclusive)
        if target is None:
            return False
        target.used_mem_mb += eff_mem
        target.used_cores += eff_cores
        self.db.add(
            Allocation(
                id=ids.new("allocation"),
                session_id=sess.id,
                device_id=target.id,
                gpu_uuid=target.gpu_uuid,
                gpu_mem_mb=eff_mem,
                gpu_cores=eff_cores,
                status="reserved",
            )
        )
        sess.device_total_mem_mb = target.total_mem_mb
        return True

    async def reserve_spot_slice(self, sess: Session, req: SessionCreate) -> bool:
        """Place a preemptible spot session on a card a resident has yielded (physically free).

        The physical card is free — the resident's VRAM now lives in host RAM — so this deliberately
        does **not** touch device.used_*, which preserves the resident's occupancy accounting. It
        only creates a borrow allocation (kind='borrow') and marks lend_state='lent'. Because the
        HAMi extender does the actual placement, one yielded card can host **several fractional spot
        sessions**: an exclusive spot session takes a full card (only one with no existing borrow),
        while fractional spot sessions pack into the remaining borrow capacity. When the resident
        returns, every spot session on the card is reclaimed. (See docs/paper/manuscript, §Design.)

        Runs inside the caller's transaction. True on success, False when no lendable card is free.
        """
        mode = sess.mode or req.mode
        cluster_id = sess.cluster_id or req.cluster_id
        # Requested size: exclusive takes the full card, fractional the requested VRAM and cores.
        req_mem = sess.gpu_mem_mb or req.gpu_mem_mb or 0
        req_cores = sess.gpu_cores or req.gpu_cores or 0
        # Lock the yielded and lent cards for atomicity, and place on the first one that fits.
        devs = (
            await self.db.scalars(
                select(GpuDevice)
                .where(
                    GpuDevice.cluster_id == cluster_id,
                    GpuDevice.status == "ready",
                    GpuDevice.mode == "exclusive",
                    GpuDevice.lend_state.in_(("yielded", "lent")),
                )
                .with_for_update()
            )
        ).all()
        for dev in devs:
            # Existing borrow usage on this card. Resident occupancy is ignored: the card is free.
            borrows = (
                await self.db.scalars(
                    select(Allocation).where(
                        Allocation.device_id == dev.id,
                        Allocation.ended_at.is_(None),
                        Allocation.kind == "spot",
                    )
                )
            ).all()
            if mode == "exclusive":
                if borrows:
                    continue  # full card — unavailable once any spot session is on it
                place_mem, place_cores = dev.total_mem_mb, dev.total_cores
            else:
                if req_mem <= 0:
                    continue  # fractional still needs the requested VRAM
                used_mem = sum(b.gpu_mem_mb or 0 for b in borrows)
                used_cores = sum(b.gpu_cores or 0 for b in borrows)
                if (dev.total_mem_mb - used_mem) < req_mem or (dev.total_cores - used_cores) < req_cores:
                    continue  # not enough borrow capacity left
                place_mem, place_cores = req_mem, req_cores
            dev.lend_state = "lent"
            self.db.add(
                Allocation(
                    id=ids.new("allocation"),
                    session_id=sess.id,
                    device_id=dev.id,
                    gpu_uuid=dev.gpu_uuid,
                    gpu_mem_mb=place_mem,
                    gpu_cores=place_cores,
                    status="reserved",
                    kind="spot",
                )
            )
            sess.device_total_mem_mb = dev.total_mem_mb
            return True
        return False

    async def _preempt_lower_priority(self, sess: Session, req: SessionCreate) -> bool:
        """Active preemption: pick a lower-priority yieldable running session and make it yield.

        Candidates are sessions in the same cluster that are exclusive, have pause_mode=yield, rank
        below the requester, and **hold an active card** (lend_state=''). Stopping such a session
        yields its card into the lending pool, and the caller then places the requester on it with
        reserve_spot_slice. Reclaim respects priority too, so a lower-priority victim cannot preempt
        a higher-priority spot session back — see _reclaim_borrower.
        (See docs/paper/manuscript, §Design.)

        True when a victim yielded, False when there was nothing to take. `stop` commits, so call
        this outside a begin() block. """
        from app.domain.session_service import SessionService  # lazy: avoid import cycle

        cluster_id = sess.cluster_id or req.cluster_id
        victim_id = await self.db.scalar(
            select(Session.id)
            .join(Allocation, Allocation.session_id == Session.id)
            .join(GpuDevice, GpuDevice.id == Allocation.device_id)
            .where(
                Session.cluster_id == cluster_id,
                Session.status == "running",
                Session.mode == "exclusive",
                Session.pause_mode == "yield",
                Session.priority < sess.priority,
                Session.deleted_at.is_(None),
                Allocation.ended_at.is_(None),
                Allocation.kind == "resident",
                GpuDevice.lend_state == "",
            )
            .order_by(Session.priority.asc())
            .limit(1)
        )
        if victim_id is None:
            return False
        try:
            await SessionService(self.db).stop(victim_id)  # lossless yield; the card becomes lend_state=yielded
        except Exception:  # noqa: BLE001 — a failed preemption only drops the requester into the queue
            log.exception("preempt: victim yield failed session=%s", victim_id)
            return False
        return True

    # ── private helpers ──
    @staticmethod
    def _reserve_target(devs, req_mem: int, req_cores: int, exclusive: bool):
        """Choose the card to reserve and the occupancy to record (eff_mem, eff_cores).

        Exclusive: pick a completely empty card and occupy its **entire capacity**, which blocks
        co-tenancy; None when no card qualifies. Fractional: best-fit the slice onto a card, which
        several sessions may share."""
        if exclusive:
            free = [d for d in devs if d.used_mem_mb == 0 and d.used_cores == 0]
            if not free:
                return None, 0, 0
            d = free[0]
            return d, d.total_mem_mb, d.total_cores
        d = SchedulerService._pick_device(devs, req_mem, req_cores)
        if d is None:
            return None, 0, 0
        return d, req_mem, req_cores

    async def _release_reservation(self, session_id: str) -> None:
        """Release a reservation so a failed handoff cannot leak one.

        Closes the live allocation as released, rolls device.used back, and marks the session
        `error`. Without this, a handoff that fails after the reservation commits leaves a zombie
        allocation behind."""
        async with self.db.begin():
            alloc = (
                await self.db.scalars(
                    select(Allocation)
                    .where(Allocation.session_id == session_id, Allocation.ended_at.is_(None))
                    .with_for_update()
                )
            ).first()
            if alloc is not None:
                if alloc.device_id:
                    dev = await self.db.get(GpuDevice, alloc.device_id, with_for_update=True)
                    if dev is not None:
                        dev.used_mem_mb = max(0, dev.used_mem_mb - (alloc.gpu_mem_mb or 0))
                        dev.used_cores = max(0, dev.used_cores - (alloc.gpu_cores or 0))
                alloc.status = "released"
                alloc.ended_at = datetime.now(UTC)
            sess = await self.db.get(Session, session_id, with_for_update=True)
            if sess is not None and sess.status in ("pending", "preparing"):
                sess.status = "error"

    @staticmethod
    def _pick_device(devs, req_mem: int, req_cores: int):
        """Occupancy-aware 2D best-fit (VRAM + cores) for a fractional slice.

        Candidates are cards that fit in both dimensions. The score is the headroom left after
        placement, as (dominant dimension, total). binpack, the default, minimises it: work
        consolidates onto the fullest card, empty cards stay available for exclusive or large jobs,
        and both fragmentation and dominant-resource skew stay low. spread maximises it, picking the
        emptiest card to balance load and reduce interference.
        """
        candidates = [
            d
            for d in devs
            if (d.total_mem_mb - d.used_mem_mb) >= req_mem
            and ((d.total_cores or 100) - d.used_cores) >= req_cores
        ]
        if not candidates:
            return None

        def _free_after(d):
            tm = d.total_mem_mb or 1
            tc = d.total_cores or 100
            f_mem = (tm - d.used_mem_mb - req_mem) / tm
            f_core = (tc - d.used_cores - req_cores) / tc
            # Tightness is judged on the dominant dimension (the larger headroom), with total
            # headroom as the tie-break.
            return (max(f_mem, f_core), f_mem + f_core)

        spread = getattr(settings, "GPU_PACKING", "binpack") == "spread"
        return (max if spread else min)(candidates, key=_free_after)

    async def _estimate(self, req: SessionCreate) -> _Estimate:
        """Estimate unit price + 1h hold amount = credit_per_hour * occupancy."""
        offering = await self.db.get(Offering, req.offering_id)
        # CPU session: priced proportionally to cpu/mem/disk at occupancy 1.0, since it takes the
        # whole allocation. The hold covers one hour.
        if req.resource_class == "cpu":
            cpu_v = req.cpu if req.cpu is not None else (offering.cpu if offering is not None else None)
            mem_v = req.mem_gb if req.mem_gb is not None else (offering.mem_gb if offering is not None else None)
            disk_v = req.disk_gb if req.disk_gb is not None else (offering.disk_gb if offering is not None else None)
            cph = compute_credit_per_hour(cpu_v, mem_v, disk_v)   # already an integer
            return _Estimate(credit_per_hour=cph, occupancy=1.0, hold_amount=cph)
        cph = offering.credit_per_hour if offering is not None else Decimal("0")
        denom = (offering.gpu_mem_mb if offering and offering.gpu_mem_mb else None) or (
            req.gpu_mem_mb or 0
        )
        mem_ratio = (req.gpu_mem_mb or 0) / denom if denom else 0.0
        core_ratio = (req.gpu_cores or 0) / 100
        # An exclusive session sends no gpu_mem_mb or cores, which would compute occupancy 0 — treat
        # it as 1.0, the full card.
        occupancy = max(mem_ratio, core_ratio) or 1.0
        # Hourly cost = rate x occupancy, rounded to whole credits. The hold covers one hour.
        hold = round_credit(cph * Decimal(str(occupancy)))
        return _Estimate(credit_per_hour=cph, occupancy=occupancy, hold_amount=hold)

    async def _scope_chain(self, req: SessionCreate) -> list[tuple[str, str]]:
        """Budget scope chain, most-specific-first: [("group", prj), ("org", org)]."""
        chain: list[tuple[str, str]] = []
        if req.group_id:
            chain.append(("group", req.group_id))
            project = await self.db.get(Project, req.group_id)
            if project is not None:
                chain.append(("org", project.org_id))
        return chain

    async def _policy_for(self, req: SessionCreate, principal: Principal) -> ResourcePolicy | None:
        """Most-specific policy wins: user, then group, then organization, then global."""
        async def _scoped(scope: str, scope_id: str) -> ResourcePolicy | None:
            return (
                await self.db.scalars(
                    select(ResourcePolicy).where(
                        ResourcePolicy.scope == scope, ResourcePolicy.scope_id == scope_id
                    )
                )
            ).first()

        # 1. User
        pol = await _scoped("user", principal.user_id)
        if pol is not None:
            return pol
        # 2. Group, then 3. organization (derived from the group)
        if req.group_id:
            pol = await _scoped("group", req.group_id)
            if pol is not None:
                return pol
            project = await self.db.get(Project, req.group_id)
            if project is not None and project.org_id:
                pol = await _scoped("org", project.org_id)
                if pol is not None:
                    return pol
        # 4. Global, applied only when no more specific policy exists.
        return await _scoped("global", "*")

    async def _check_quota(
        self, req: SessionCreate, principal: Principal, exclude_session_id: str | None = None
    ) -> None:
        """Resource-policy concurrency/queue caps (independent of credit) -> 409 quota_exceeded.

        By the time admission runs, this session is already stored with status=pending (see
        _persist_pending), so every sum and count must exclude it via exclude_session_id. Otherwise
        a user's very first session counts itself and trips the limit. """
        pol = await self._policy_for(req, principal)
        if pol is None:
            return
        if pol.max_concurrent is not None:
            active = await self._count_active(principal, req, exclude_session_id)
            if active > pol.max_concurrent:
                raise QuotaExceeded(
                    "max_concurrent exceeded",
                    {"limit": pol.max_concurrent, "active": active},
                )
        if pol.max_queued is not None:
            queued = await self._count_queued(req)
            if queued > pol.max_queued:
                raise QuotaExceeded(
                    "max_queued exceeded", {"limit": pol.max_queued, "queued": queued}
                )
        await self._check_resource_sum(req, principal, pol, exclude_session_id)

    async def _check_resource_sum(
        self, req: SessionCreate, principal: Principal, pol: ResourcePolicy,
        exclude_session_id: str | None = None,
    ) -> None:
        """Check the policy's aggregate resource limits.

        The scope (group or user) passes when the sum over its active sessions plus this request
        stays within the limit, for each limit that is set (> 0). gpu_mem_mb and gpu_cores come from
        the request; cpu, mem_gb, and disk (storage) come from the offering specification. """
        limits = pol.limits or {}
        keys = ("gpu_mem_mb", "gpu_cores", "cpu", "mem_gb", "storage_gb")
        if not any((limits.get(k) or 0) > 0 for k in keys):
            return  # no limit configured, nothing to check

        scope_col = Session.group_id if req.group_id else Session.owner_user_id
        scope_val = req.group_id if req.group_id else principal.user_id
        row = (
            await self.db.execute(
                select(
                    func.coalesce(func.sum(Session.gpu_mem_mb), 0),
                    func.coalesce(func.sum(Session.gpu_cores), 0),
                    func.coalesce(func.sum(Session.cpu), 0),
                    func.coalesce(func.sum(Session.mem_gb), 0),
                    func.coalesce(func.sum(Session.disk_gb), 0),
                ).where(
                    scope_col == scope_val,
                    Session.status.in_(
                        ("pending", "preparing", "running", "paused", "terminating")
                    ),
                    Session.deleted_at.is_(None),
                    Session.id != exclude_session_id if exclude_session_id else true(),
                )
            )
        ).one()
        used_vram, used_cores, used_cpu, used_mem, used_disk = (int(x or 0) for x in row)
        offering = await self.db.get(Offering, req.offering_id)
        # New cpu/mem/disk (storage): the real pod size comes from the request overrides (req.*),
        # matching what _persist_pending picks, so the limit check must prefer them too and fall
        # back to the offering. This is what stops someone passing a small offering and then
        # inflating disk_gb, cpu, or mem to slip past the policy limit.
        new_cpu = req.cpu if req.cpu is not None else ((offering.cpu or 0) if offering else 0)
        new_mem = req.mem_gb if req.mem_gb is not None else ((offering.mem_gb or 0) if offering else 0)
        new_disk = req.disk_gb if req.disk_gb is not None else ((offering.disk_gb or 0) if offering else 0)
        checks = [
            ("gpu_mem_mb", "VRAM(MB)", used_vram, req.gpu_mem_mb or 0),
            ("gpu_cores", "GPU cores (%)", used_cores, req.gpu_cores or 0),
            ("cpu", "CPU(vCPU)", used_cpu, new_cpu),
            ("mem_gb", "memory (GiB)", used_mem, new_mem),
            ("storage_gb", "storage (GiB)", used_disk, new_disk),
        ]
        for key, label, used, add in checks:
            lim = limits.get(key) or 0
            if lim > 0 and used + add > lim:
                raise QuotaExceeded(
                    f"{key} sum exceeds policy limit",
                    {"resource": label, "limit": lim, "used": used, "request": add},
                )

    async def _check_cpu_quota(
        self, req: SessionCreate, principal: Principal, exclude_session_id: str | None = None
    ) -> None:
        """CPU session: the concurrent-CPU-session cap plus the aggregate cpu/mem/storage limits."""
        pol = await self._policy_for(req, principal)
        if pol is None:
            return
        await self._check_resource_sum(req, principal, pol, exclude_session_id)
        if pol.max_concurrent is None:
            return
        active = await self._count_active(principal, req, exclude_session_id)
        if active > pol.max_concurrent:
            raise QuotaExceeded(
                "cpu_session_max exceeded",
                {"limit": pol.max_concurrent, "active": active},
            )

    async def _count_active(
        self, principal: Principal, req: SessionCreate, exclude_session_id: str | None = None
    ) -> int:
        scope_col = Session.group_id if req.group_id else Session.owner_user_id
        scope_val = req.group_id if req.group_id else principal.user_id
        result = await self.db.scalar(
            select(func.count())
            .select_from(Session)
            .where(
                scope_col == scope_val,
                Session.resource_class == req.resource_class,
                Session.status.in_(
                    ("pending", "preparing", "running", "paused", "terminating")
                ),
                Session.deleted_at.is_(None),
                Session.id != exclude_session_id if exclude_session_id else true(),
            )
        )
        return int(result or 0)

    async def _count_queued(self, req: SessionCreate) -> int:
        stmt = (
            select(func.count())
            .select_from(QueueEntry)
            .join(Session, Session.id == QueueEntry.session_id)
        )
        if req.group_id:
            stmt = stmt.where(Session.group_id == req.group_id)
        result = await self.db.scalar(stmt)
        return int(result or 0)

    async def _enqueue(self, sess: Session, req: SessionCreate) -> None:
        """Persist a QueueEntry + ZADD gshare:queue (score = weight*priority + aging)."""
        priority = 0
        entry = (
            await self.db.scalars(
                select(QueueEntry).where(QueueEntry.session_id == sess.id)
            )
        ).first()
        if entry is None:
            entry = QueueEntry(
                id=ids.new("queue"),
                session_id=sess.id,
                session_req=req.model_dump(mode="json"),
                priority=priority,
            )
            self.db.add(entry)
            await self.db.flush()
            # Notify the owner once, on the first enqueue only.
            from app.domain.notification_service import NotificationService

            await NotificationService(self.db).notify(
                [sess.owner_user_id], "session_queued", "Session queued",
                f"Session '{sess.name or sess.id}' was queued: not enough capacity right now.",
                session_id=sess.id,
            )
        else:
            priority = entry.priority
        score = PRIORITY_WEIGHT * priority + self._aging()
        await get_redis().zadd(QUEUE_KEY, {sess.id: score})

    @staticmethod
    def _aging() -> float:
        """Aging term: earlier enqueue -> higher score within same priority (FIFO fairness).

        Use negative wall-clock seconds so that older entries (smaller timestamp) rank higher.
        """
        return -time.time()

    def _req_from_entry(self, entry: QueueEntry | None, sess: Session) -> SessionCreate:
        """Rebuild the SessionCreate for re-admission (from stored session_req or the row)."""
        if entry is not None and entry.session_req:
            return SessionCreate.model_validate(entry.session_req)
        return SessionCreate(
            offering_id=sess.offering_id,
            image_id=sess.image_id,
            resource_class=sess.resource_class,
            cluster_id=sess.cluster_id,
            cluster_mode=sess.cluster_mode,
            group_id=sess.group_id,
            gpu_mem_mb=sess.gpu_mem_mb,
            gpu_cores=sess.gpu_cores,
            mode=sess.mode,
            billing_wallet_id=sess.billing_wallet_id,
        )
