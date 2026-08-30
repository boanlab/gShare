"""Drain modes act on the node's sessions: force_terminate ends them (settled), reschedule
pauses+resumes — and with no capacity elsewhere the session parks PAUSED, never terminated."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.infra_router import _DrainBody, drain_node
from app.auth.rbac import Principal
from app.core import ids
from app.db.models import (
    Allocation,
    Cluster,
    CreditWallet,
    GpuDevice,
    GpuNode,
    Image,
    Offering,
    User,
)
from app.db.models import Session as SessionRow


def _admin() -> Principal:
    return Principal(user_id=ids.new("user"), global_role="super_admin", global_roles=["super_admin"])


async def _seed(db):
    clu = Cluster(id=ids.new("cluster"), name="c1", status="connected",
                  api_server="https://k8s", runtime="containerd", kubeconfig_secret_ref="ref")
    node = GpuNode(id=ids.new("node"), cluster_id=clu.id, hostname="n1", status="ready",
                   cpu=32, mem=64, disk=500)
    dev = GpuDevice(id=ids.new("device"), node_id=node.id, cluster_id=clu.id,
                    gpu_uuid="GPU-drain-1", model="RTX 4090", mode="fractional", status="ready",
                    total_mem_mb=24000, used_mem_mb=4000, total_cores=100, used_cores=25)
    owner = User(id=ids.new("user"), email="d@x.kr", name="D", password_hash="x", status="active")
    wallet = CreditWallet(id=ids.new("wallet"), owner_type="user", owner_id=owner.id,
                          balance=Decimal("100"), reserved=Decimal("0"))
    off = Offering(id=ids.new("offering"), name="o", resource_class="gpu", gpu_model="RTX 4090",
                   gpu_mem_mb=24000, credit_per_hour=Decimal("60"))
    img = Image(id=ids.new("image"), name="img")
    sess = SessionRow(id=ids.new("session"), owner_user_id=owner.id, cluster_id=clu.id,
                      offering_id=off.id, image_id=img.id, resource_class="gpu",
                      mode="fractional", status="running", gpu_mem_mb=4000, gpu_cores=25,
                      billing_wallet_id=wallet.id, credit_per_hour_snapshot=Decimal("60"))
    alloc = Allocation(id=ids.new("allocation"), session_id=sess.id, device_id=dev.id,
                       gpu_mem_mb=4000, gpu_cores=25, status="bound")
    async with db.begin():
        db.add_all([clu, node, dev, owner, wallet, off, img, sess, alloc])
    return node, dev, sess


@pytest.mark.asyncio
async def test_drain_force_terminate_ends_sessions(db):
    node, dev, sess = await _seed(db)
    res = await drain_node(node.id, _DrainBody(mode="force_terminate"), principal=_admin(), db=db)
    assert res["status"] == "cordoned"
    assert sess.id in res["terminated"] and not res["failed"]
    db.expunge_all()
    row = await db.get(SessionRow, sess.id)
    assert row.status == "terminated" and row.status_reason == "admin_stopped"
    n = await db.get(GpuNode, node.id)
    assert n.status == "cordoned"
    d = await db.get(GpuDevice, dev.id)
    assert d.used_mem_mb == 0 and d.used_cores == 0   # capacity reclaimed


@pytest.mark.asyncio
async def test_drain_reschedule_parks_paused_when_no_capacity(db, monkeypatch):
    node, dev, sess = await _seed(db)
    # Offline test env: the operator handoff is not wired; stop()'s CR patch must be a no-op
    # (terminate() already swallows handoff errors itself, so the force test needs no stub).
    from app.cluster.handoff import Handoff

    async def _noop(self, *a, **k):
        return None

    monkeypatch.setattr(Handoff, "set_paused", _noop)
    # The only eligible card sits on the node being drained -> resume has nowhere to go.
    res = await drain_node(node.id, _DrainBody(mode="reschedule"), principal=_admin(), db=db)
    assert sess.id in res["parked"], res
    assert not res["terminated"] and not res["failed"]
    db.expunge_all()
    row = await db.get(SessionRow, sess.id)
    assert row.status == "paused", row.status   # work preserved, never killed
