"""Pool rebalancer: hami-core<->mig transitions through the GpuModeChange machinery."""
from __future__ import annotations

import pytest

from app.api.infra_router import PoolTargetsBody, set_pool_targets
from app.auth.rbac import Principal
from app.core import ids
from app.db.models import Cluster, GpuDevice, GpuNode
from app.workers import pool_rebalancer


class FakeChangeCRD:
    """Captures apply/get/delete; scripted status per device id."""

    def __init__(self):
        self.applied: dict[str, dict] = {}
        self.phase: dict[str, str] = {}
        self.deleted: list[str] = []

    async def apply_change(self, cluster_id, *, device_id, node_name, gpu_uuid, gpu_index, target_mode):
        self.applied[device_id] = {
            "node": node_name, "uuid": gpu_uuid, "index": gpu_index, "target": target_mode,
        }

    async def get_change(self, cluster_id, device_id):
        p = self.phase.get(device_id)
        return {"status": {"phase": p}} if p else None

    async def delete_change(self, cluster_id, device_id):
        self.deleted.append(device_id)


async def _fleet(db, n=3):
    cluster = Cluster(id=ids.new("cluster"), name="c", api_server="https://k8s",
                      runtime="containerd", kubeconfig_secret_ref="ref")
    node = GpuNode(id=ids.new("node"), hostname="gpu-1", cluster_id=cluster.id, status="ready")
    devs = [
        GpuDevice(
            id=f"GPU-{i}", node_id=node.id, cluster_id=cluster.id, model="RTX PRO 6000",
            gpu_uuid=f"GPU-{i}", total_mem_mb=98304, status="ready", mode="fractional",
        )
        for i in range(n)
    ]
    async with db.begin():
        db.add_all([cluster, node, *devs])
    return cluster, node, devs


@pytest.mark.asyncio
async def test_pool_targets_pick_emptiest_and_drain(db):
    cluster, _node, devs = await _fleet(db, n=3)
    devs_sorted = devs
    async with db.begin():
        d = await db.get(GpuDevice, devs_sorted[0].id)
        d.used_mem_mb = 50000   # busiest — must not be chosen
    admin = Principal(user_id=ids.new("user"), global_roles=["super_admin"])

    out = await set_pool_targets(cluster.id, PoolTargetsBody(mig_cards=2), principal=admin, db=db)
    assert len(out["moved"]) == 2
    db.expunge_all()
    chosen = [await db.get(GpuDevice, i) for i in out["moved"]]
    assert all(c.desired_mode == "mig" and c.mode_state == "draining" for c in chosen)
    assert devs_sorted[0].id not in out["moved"]


@pytest.mark.asyncio
async def test_rebalancer_full_cycle(db, monkeypatch):
    cluster, node, devs = await _fleet(db, n=1)
    dev = devs[0]
    async with db.begin():
        d = await db.get(GpuDevice, dev.id)
        d.desired_mode = "mig"
        d.mode_state = "draining"

    fake = FakeChangeCRD()
    monkeypatch.setattr(pool_rebalancer, "GpuModeChangeCRD", lambda db_: fake)

    class _Maker:
        def __call__(self):
            return _Ctx()

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(pool_rebalancer, "get_sessionmaker", lambda: _Maker())

    # Tick 1: empty draining card -> CR created, state applying.
    await pool_rebalancer.run()
    assert dev.id in fake.applied
    assert fake.applied[dev.id]["target"] == "mig"
    db.expunge_all()
    got = await db.get(GpuDevice, dev.id)
    assert got.mode_state == "applying"
    await db.commit()

    # Tick 2: Job succeeded but HAMi has not re-registered yet -> stay applying.
    fake.phase[dev.id] = "Succeeded"
    await pool_rebalancer.run()
    db.expunge_all()
    got = await db.get(GpuDevice, dev.id)
    assert got.mode_state == "applying"
    await db.commit()

    # Inventory reports the card as mig (register annotation refreshed) -> ready.
    async with db.begin():
        d = await db.get(GpuDevice, dev.id)
        d.mode = "mig"
    await pool_rebalancer.run()
    db.expunge_all()
    got = await db.get(GpuDevice, dev.id)
    assert got.mode == "mig" and got.mode_state == "ready"
    assert fake.deleted == [dev.id]


@pytest.mark.asyncio
async def test_rebalancer_failure_marks_error(db, monkeypatch):
    cluster, node, devs = await _fleet(db, n=1)
    dev = devs[0]
    async with db.begin():
        d = await db.get(GpuDevice, dev.id)
        d.desired_mode = "mig"
        d.mode_state = "applying"

    fake = FakeChangeCRD()
    fake.phase[dev.id] = "Failed"
    monkeypatch.setattr(pool_rebalancer, "GpuModeChangeCRD", lambda db_: fake)

    class _Maker:
        def __call__(self):
            return _Ctx()

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(pool_rebalancer, "get_sessionmaker", lambda: _Maker())
    await pool_rebalancer.run()
    db.expunge_all()
    assert (await db.get(GpuDevice, dev.id)).mode_state == "error"


@pytest.mark.asyncio
async def test_idle_card_metadata_transition_applies(db, monkeypatch):
    """An already-EMPTY card switching fractional->exclusive must not drain forever: nothing will
    release it, so the rebalancer has to apply the metadata change itself."""
    from app.workers import pool_rebalancer

    dev = GpuDevice(
        id=ids.new("device"), node_id=ids.new("node"), cluster_id="clu_x",
        model="RTX PRO 5000", gpu_uuid=ids.new("dev"),
        total_mem_mb=48935, used_mem_mb=0, total_cores=100, used_cores=0,
        status="ready", mode="fractional", desired_mode="exclusive", mode_state="draining",
    )
    async with db.begin():
        db.add(dev)

    monkeypatch.setattr(pool_rebalancer, "get_sessionmaker", lambda: (lambda: _NullCtx(db)))
    await pool_rebalancer.run()

    db.expunge_all()
    fresh = await db.get(GpuDevice, dev.id)
    assert fresh.mode == "exclusive"
    assert fresh.mode_state == "ready"


class _NullCtx:
    """Hand the worker the test's session without closing it."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False
