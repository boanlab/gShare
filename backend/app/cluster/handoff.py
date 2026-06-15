"""Handoff — persist desired state + apply GShareSession CR.

Domain services call this through a narrow port so tests can substitute a Fake handoff and assert
the desired payload. CRD-primary is the only production transport (apply the GShareSession CR).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.cluster.crd import GShareSessionCRD


class Handoff:
    def __init__(self, db: AsyncSession):
        self.db = db
        # CRD needs the db session to resolve the target Cluster's kubeconfig/SA.
        self.crd = GShareSessionCRD(db)

    async def apply_desired(self, sess, req) -> None:
        """Serialize + apply the GShareSession CR for an admitted session.

        spec = self.crd.to_session_spec(sess, req); await self.crd.apply(req.cluster_id, spec).
        No workload K8s API calls here — this is the ONLY way Python expresses desired workload
        state. The W3C traceparent is propagated onto the CR annotation so the operator can
        continue the cross-plane trace.
        """
        # The operator pulls spec.image verbatim, so resolve image_id -> Image.registry here
        # (the operator has no DB). Without this the CR carries the raw image id and the Pod
        # fails with InvalidImageName.
        image_ref = await self._resolve_image_ref(sess)
        spec = self.crd.to_session_spec(sess, req, image_ref=image_ref)   # GShareSession.spec
        await self._stamp_spot(sess, spec)
        cluster_id = getattr(req, "cluster_id", None) or sess.cluster_id
        await self.crd.apply(cluster_id, spec, traceparent=_current_traceparent())

    async def _stamp_spot(self, sess, spec: dict) -> None:
        """For a borrowing session, put borrowed_gpu_uuid and the node on the custom resource so the
        operator builds a pod with the device injected directly, bypassing the device plugin.

        Looks up the live borrow allocation (kind='borrow') and the node hosting its device."""
        from sqlalchemy import select

        from app.db.models import Allocation, GpuDevice, GpuNode

        alloc = (
            await self.db.scalars(
                select(Allocation).where(
                    Allocation.session_id == sess.id,
                    Allocation.ended_at.is_(None),
                    Allocation.kind == "spot",
                )
            )
        ).first()
        if alloc is None or alloc.device_id is None:
            return
        dev = await self.db.get(GpuDevice, alloc.device_id)
        if dev is None:
            return
        spec["borrowed_gpu_uuid"] = alloc.gpu_uuid or dev.gpu_uuid
        node = await self.db.get(GpuNode, dev.node_id)
        if node is not None:
            spec["borrowed_node"] = node.hostname

    async def _resolve_image_ref(self, sess) -> str | None:
        """Look up the catalog Image's pullable registry ref from sess.image_id."""
        image_id = getattr(sess, "image_id", None)
        if not image_id:
            return None
        from app.db.models import Image

        img = await self.db.get(Image, image_id)
        return img.registry if img is not None else None

    async def delete_desired(self, sess) -> None:
        """Remove desired state for a torn-down session (CR delete; operator finalizer cleans
        up)."""
        await self.crd.delete(sess.cluster_id, sess.id)

    async def set_paused(self, sess, paused: bool, *, graceful_demote: bool | None = None) -> None:
        """Pause(true)/resume(false) — patch spec.paused so the operator releases/recreates the Pod.

        pauseMode and preemptible are re-asserted alongside it, so the operator's pause branch
        agrees with the backend's yield decision. graceful_demote signals a yield-to-cold demotion:
        the operator toggles VRAM back, sends SIGTERM so the job writes a fresh checkpoint, and only
        then deletes the pod."""
        await self.crd.set_paused(
            sess.cluster_id, sess.id, paused,
            pause_mode=getattr(sess, "pause_mode", None),
            preemptible=getattr(sess, "preemptible", None),
            graceful_demote=graceful_demote,
        )


def _current_traceparent() -> str | None:
    """Build a W3C ``traceparent`` from the active OpenTelemetry span, if any.

    Returns None when no span/SDK is active so handoff stays usable offline and in tests; the
    operator simply starts a fresh root span in that case.
    """
    try:
        from opentelemetry import trace  # lazy: optional dependency
    except Exception:  # noqa: BLE001
        return None
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not getattr(ctx, "is_valid", False):
        return None
    flags = 1 if (int(getattr(ctx, "trace_flags", 0)) & 1) else 0
    return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{flags:02x}"
