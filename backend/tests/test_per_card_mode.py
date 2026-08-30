"""Per-card GPU pool tests (PER_CARD_MODE flag).

- CRD spec emission: fractional carries the ledger-reserved pin; exclusive becomes a 100% HAMi
  slice (fullCard) through hami-scheduler.
- Inventory no longer writes policy: the report decides only the hami-core↔mig axis.
- Placement skips draining cards; a drained fractional↔exclusive change applies once the card
  empties.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.infra_router import GpuDeviceModeSet, set_gpu_device_mode
from app.api.schemas.internal import OperatorGpuDeviceUpsert
from app.api.schemas.session import SessionCreate
from app.auth.rbac import Principal
from app.cluster.crd import GShareSessionCRD
from app.cluster.inventory_sync import InventorySync
from app.core import ids
from app.core.config import settings
from app.db.models import Cluster, CreditWallet, GpuDevice, GpuNode, Image, Offering, Project
from app.db.models import Session as SessionRow
from app.domain.pool import maybe_apply_drained_mode
from app.domain.scheduler import SchedulerService


def _seed(db_objs, *, mode="fractional", mode_state="ready"):
    org_id = ids.new("org")
    group = Project(id=ids.new("group"), org_id=org_id, name="p")
    user_id = ids.new("user")
    wallet = CreditWallet(
        id=ids.new("wallet"), owner_type="user", owner_id=user_id,
        balance=Decimal("1000"), reserved=Decimal("0"),
    )
    cluster_id = ids.new("cluster")
    offering = Offering(
        id=ids.new("offering"), name="A100-frac", resource_class="gpu",
        gpu_model="A100", gpu_mem_mb=16000, gpu_cores=100, credit_per_hour=Decimal("60"),
    )
    image = Image(id=ids.new("image"), name="pytorch")
    device = GpuDevice(
        id=ids.new("device"), node_id=ids.new("node"), cluster_id=cluster_id,
        model="A100", gpu_uuid=f"GPU-{ids.new('device')}", total_mem_mb=16000, status="ready",
        mode=mode, mode_state=mode_state,
    )
    db_objs.extend([group, wallet, offering, image, device])
    return group, user_id, wallet, cluster_id, offering, image, device


@pytest.mark.asyncio
async def test_fractional_spec_carries_ledger_pin(db, fake_handoff, monkeypatch):
    monkeypatch.setattr(settings, "PER_CARD_MODE", True)
    objs: list = []
    group, user_id, wallet, cluster_id, offering, image, dev = _seed(objs)
    async with db.begin():
        db.add_all(objs)

    svc = SchedulerService(db)
    svc.handoff = fake_handoff
    req = SessionCreate(
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        cluster_id=cluster_id, group_id=group.id, mode="fractional",
        gpu_mem_mb=8000, gpu_cores=50, billing_wallet_id=wallet.id,
    )
    out = await svc.create_session(req, Principal(user_id=user_id), idem="pin-1")
    assert out.status != "error"
    assert fake_handoff.last_spec is not None
    assert fake_handoff.last_spec.get("pinned_gpu_uuid") == dev.gpu_uuid
    assert fake_handoff.last_spec.get("scheduler_name") == "hami-scheduler"


@pytest.mark.asyncio
async def test_exclusive_spec_is_full_card_via_hami(db, monkeypatch):
    monkeypatch.setattr(settings, "PER_CARD_MODE", True)
    sess = SessionRow(
        id=ids.new("session"), owner_user_id=ids.new("user"), cluster_id=ids.new("cluster"),
        offering_id=ids.new("offering"), image_id=ids.new("image"), resource_class="gpu",
        mode="exclusive", status="pending",
    )
    sess._pinned_gpu_uuid = "GPU-abc"
    spec = GShareSessionCRD().to_session_spec(sess, None, image_ref="img:1")
    assert spec["full_card"] is True
    assert spec["scheduler_name"] == "hami-scheduler"
    assert spec["pinned_gpu_uuid"] == "GPU-abc"


def test_exclusive_spec_legacy_without_flag():
    sess = SessionRow(
        id=ids.new("session"), owner_user_id=ids.new("user"), cluster_id=ids.new("cluster"),
        offering_id=ids.new("offering"), image_id=ids.new("image"), resource_class="gpu",
        mode="exclusive", status="pending",
    )
    spec = GShareSessionCRD().to_session_spec(sess, None, image_ref="img:1")
    assert "full_card" not in spec
    assert "scheduler_name" not in spec


@pytest.mark.asyncio
async def test_inventory_report_does_not_flip_policy_mode(db):
    """A node relabel (reported exclusive) no longer rewrites an existing card's pool; only the
    hami-core↔mig axis is observation."""
    cluster = Cluster(id=ids.new("cluster"), name="c", api_server="https://k8s", runtime="containerd", kubeconfig_secret_ref="ref")
    node = GpuNode(id=ids.new("node"), hostname="n1", cluster_id=cluster.id, status="ready")
    dev = GpuDevice(
        id="GPU-x", node_id=node.id, cluster_id=cluster.id, model="A100",
        gpu_uuid="GPU-x", total_mem_mb=16000, status="ready", mode="fractional",
    )
    async with db.begin():
        db.add_all([cluster, node, dev])

    sync = InventorySync(db)
    ev = OperatorGpuDeviceUpsert(
        node_id="n1", uuid="GPU-x", mode="exclusive", total_mem_mb=16000,
        used_mem_mb=0, used_cores=0, total_cores=100, status="ready",
        cluster_id=cluster.id, model="A100",
    )
    await sync.upsert_device(ev, cluster.id)
    await db.commit()
    db.expunge_all()
    got = await db.get(GpuDevice, "GPU-x")
    assert got.mode == "fractional"          # policy survives the relabel
    await db.commit()   # close the autobegun read tx before the next upsert's begin()

    # But a MIG report IS taken (card-level hardware fact).
    ev_mig = ev.model_copy(update={"mode": "mig"})
    await sync.upsert_device(ev_mig, cluster.id)
    await db.commit()
    db.expunge_all()
    assert (await db.get(GpuDevice, "GPU-x")).mode == "mig"


@pytest.mark.asyncio
async def test_draining_card_accepts_no_placement_and_applies_when_empty(db, fake_handoff):
    objs: list = []
    group, user_id, wallet, cluster_id, offering, image, dev = _seed(
        objs, mode="fractional", mode_state="draining",
    )
    dev.desired_mode = "exclusive"
    async with db.begin():
        db.add_all(objs)

    svc = SchedulerService(db)
    svc.handoff = fake_handoff
    req = SessionCreate(
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        cluster_id=cluster_id, group_id=group.id, mode="fractional",
        gpu_mem_mb=8000, gpu_cores=50, billing_wallet_id=wallet.id,
    )
    out = await svc.create_session(req, Principal(user_id=user_id), idem="drain-1")
    assert out.status == "pending"            # queued: the draining card is not placeable

    # The card empties -> the metadata transition applies.
    db.expunge_all()
    got = await db.get(GpuDevice, dev.id)
    maybe_apply_drained_mode(got)
    assert got.mode == "exclusive"
    assert got.mode_state == "ready"


@pytest.mark.asyncio
async def test_set_device_mode_endpoint(db):
    cluster = Cluster(id=ids.new("cluster"), name="c", api_server="https://k8s", runtime="containerd", kubeconfig_secret_ref="ref")
    node = GpuNode(id=ids.new("node"), hostname="n1", cluster_id=cluster.id, status="ready")
    dev = GpuDevice(
        id="GPU-y", node_id=node.id, cluster_id=cluster.id, model="A100",
        gpu_uuid="GPU-y", total_mem_mb=16000, status="ready", mode="fractional",
    )
    async with db.begin():
        db.add_all([cluster, node, dev])
    admin = Principal(user_id=ids.new("user"), global_roles=["super_admin"])

    out = await set_gpu_device_mode("GPU-y", GpuDeviceModeSet(desired_mode="exclusive"),
                                    principal=admin, db=db)
    assert out["mode_state"] == "draining"
    out2 = await set_gpu_device_mode("GPU-y", GpuDeviceModeSet(desired_mode="fractional"),
                                     principal=admin, db=db)
    assert out2["mode_state"] == "ready"      # target == current -> nothing to drain


def _dev(mode, total=98304, used_mem=0, used_cores=0):
    from types import SimpleNamespace
    return SimpleNamespace(mode=mode, total_mem_mb=total, used_mem_mb=used_mem,
                           used_cores=used_cores, total_cores=100)


def test_mig_rounding_profiles():
    """Requests round UP to the smallest fitting quarter/half/full instance of the card."""
    d = _dev("mig")
    assert SchedulerService._mig_rounded(d, 12288, 13) == (24576, 25)    # 1/8 ask -> 1g.24gb
    assert SchedulerService._mig_rounded(d, 24576, 25) == (24576, 25)    # aligned quarter
    assert SchedulerService._mig_rounded(d, 30000, 25) == (49152, 50)    # between -> half
    assert SchedulerService._mig_rounded(d, 98304, 100) == (98304, 100)  # full card
    assert SchedulerService._mig_rounded(d, 98305, 100) is None          # over capacity


def test_reserve_target_prefers_mig_for_aligned_tiers():
    """A profile-aligned tier lands on the MIG pool; an unaligned one takes hami-core exact."""
    mig, core = _dev("mig"), _dev("fractional")
    # Aligned quarter (24576): MIG preferred.
    target, eff_mem, eff_cores = SchedulerService._reserve_target(
        [core, mig], 24576, 25, exclusive=False)
    assert target is mig and eff_mem == 24576
    # Unaligned 1/8 (12288): hami-core exact wins over MIG rounding waste.
    target, eff_mem, eff_cores = SchedulerService._reserve_target(
        [core, mig], 12288, 13, exclusive=False)
    assert target is core and eff_mem == 12288
    # hami-core full: 1/8 falls back to MIG with the rounded reservation.
    busy_core = _dev("fractional", used_mem=98304, used_cores=100)
    target, eff_mem, eff_cores = SchedulerService._reserve_target(
        [busy_core, mig], 12288, 13, exclusive=False)
    assert target is mig and eff_mem == 24576 and eff_cores == 25
