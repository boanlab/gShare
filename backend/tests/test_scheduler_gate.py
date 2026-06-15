"""Scheduler gate-order tests.

Asserts the fixed gate order (policy -> budget -> hold -> VRAM precheck -> queue -> handoff) and
the desired-payload shape per mode (fractional carries HAMi fields; exclusive bypasses HAMi).
Exercised against the in-memory SQLite ``db`` fixture (conftest).
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.api.schemas.session import SessionCreate
from app.api.sessions_router import _clamp_priority
from app.auth.rbac import Principal
from app.cluster.crd import GShareSessionCRD
from app.core import ids
from app.core.errors import BudgetExceeded, InsufficientCredit, QuotaExceeded
from app.db.models import (
    Budget,
    CreditWallet,
    GpuDevice,
    GpuNode,
    Image,
    Offering,
    Project,
    ResourcePolicy,
)
from app.domain.scheduler import SchedulerService


@pytest.mark.asyncio
async def test_gate_order_policy_then_budget_then_hold(db, fake_handoff):
    """Budget gate precedes the credit hold.

    A blocking budget over its cap AND a wallet with zero available credit are set up
    simultaneously. The fixed order says budget is evaluated *before* the hold, so the request
    must fail with BudgetExceeded (409) — NOT InsufficientCredit (402) — and the wallet's
    reservation must be untouched (no hold taken).
    """
    org_id = ids.new("org")
    group = Project(id=ids.new("group"), org_id=org_id, name="p")
    user_id = ids.new("user")
    # Personal billing wallet (owner = requester) with no available credit: a hold, if reached,
    # raises 402.
    wallet = CreditWallet(
        id=ids.new("wallet"),
        owner_type="user",
        owner_id=user_id,
        balance=Decimal("0"),
        reserved=Decimal("0"),
    )
    # Group-scoped budget already over its cap, action=block -> budget gate must reject.
    budget = Budget(
        id=ids.new("budget"),
        scope="group",
        scope_id=group.id,
        period_start=datetime(2020, 1, 1, tzinfo=UTC),
        period="monthly",
        limit_credit=Decimal("50"),
        spent_credit=Decimal("100"),
        action="block",
    )
    # The offering and image exist so _validate_refs passes. _validate_balance takes its full-card
    # reference from the registered device's measured total_mem_mb, so a 16000 MB device is
    # registered too, making 8000 MB (0.5) with 50 cores (0.5) proportional and therefore
    # acceptable.
    cluster_id = ids.new("cluster")
    offering = Offering(
        id=ids.new("offering"), name="A100-frac", resource_class="gpu",
        gpu_model="A100", gpu_mem_mb=16000, gpu_cores=100, credit_per_hour=Decimal("60"),
    )
    image = Image(id=ids.new("image"), name="pytorch")
    device = GpuDevice(
        id=ids.new("device"), node_id=ids.new("node"), cluster_id=cluster_id,
        model="A100", gpu_uuid=ids.new("device"), total_mem_mb=16000, status="ready",
    )
    async with db.begin():
        db.add_all([group, wallet, budget, offering, image, device])

    svc = SchedulerService(db)
    svc.handoff = fake_handoff  # never reached, but keep the live cluster out of the test

    req = SessionCreate(
        offering_id=offering.id,
        image_id=image.id,
        resource_class="gpu",
        cluster_id=cluster_id,
        group_id=group.id,
        gpu_mem_mb=8000,
        gpu_cores=50,
        mode="fractional",
        billing_wallet_id=wallet.id,
    )
    principal = Principal(user_id=user_id)

    # Capture the id before calling create_session: a budget rejection rolls the session back, and a
    # rollback expires every instance regardless of expire_on_commit=False, so touching wallet.id
    # afterwards would trigger an async refresh.
    wallet_id = wallet.id

    with pytest.raises(BudgetExceeded) as excinfo:
        await svc.create_session(req, principal, idem="idem-1")
    assert excinfo.value.http == 409
    assert excinfo.value.code == "budget_exceeded"

    # The hold was never taken (budget rejected first): reservation unchanged.
    db.expunge_all()
    assert (await db.get(CreditWallet, wallet_id)).reserved == Decimal("0.00")
    # And the handoff was never invoked.
    assert fake_handoff.last_spec is None


@pytest.mark.asyncio
async def test_hold_is_reached_when_budget_passes(db, fake_handoff):
    """With no blocking budget, the gate proceeds to the credit hold (which here fails 402).

    Confirms the budget gate is not the only barrier: once it passes, the hold runs and an
    empty wallet surfaces InsufficientCredit. (Complements the order assertion above.)
    """
    group = Project(id=ids.new("group"), org_id=ids.new("org"), name="p")
    user_id = ids.new("user")
    wallet = CreditWallet(
        id=ids.new("wallet"),
        owner_type="user",
        owner_id=user_id,
        balance=Decimal("0"),
        reserved=Decimal("0"),
    )
    # Priced offering so the estimate -> hold amount is non-zero (empty wallet then 402s).
    cluster_id = ids.new("cluster")
    offering = Offering(
        id=ids.new("offering"),
        name="A100-frac",
        resource_class="gpu",
        gpu_model="A100",
        gpu_mem_mb=16000,
        gpu_cores=100,
        credit_per_hour=Decimal("60"),
    )
    image = Image(id=ids.new("image"), name="pytorch")   # so _validate_refs passes
    device = GpuDevice(   # supplies the 16000 MB full-card reference _validate_balance needs
        id=ids.new("device"), node_id=ids.new("node"), cluster_id=cluster_id,
        model="A100", gpu_uuid=ids.new("device"), total_mem_mb=16000, status="ready",
    )
    async with db.begin():
        db.add_all([group, wallet, offering, image, device])

    svc = SchedulerService(db)
    svc.handoff = fake_handoff

    req = SessionCreate(
        offering_id=offering.id,
        image_id=image.id,
        resource_class="gpu",
        cluster_id=cluster_id,
        group_id=group.id,
        gpu_mem_mb=8000,
        gpu_cores=50,
        mode="fractional",
        billing_wallet_id=wallet.id,
    )
    principal = Principal(user_id=user_id)

    with pytest.raises(InsufficientCredit) as excinfo:
        await svc.create_session(req, principal, idem="idem-2")
    assert excinfo.value.http == 402
    assert fake_handoff.last_spec is None


@pytest.mark.asyncio
async def test_lossless_gate_eligible_when_offering_and_node_capable(db):
    """Lossless pause requires all three: the offering opts in, the session is exclusive, and the
    cluster has a ready lossless-capable node."""
    cluster_id = ids.new("cluster")
    offering = Offering(
        id=ids.new("offering"), name="A100-excl", resource_class="gpu",
        gpu_model="A100", gpu_mem_mb=81920, gpu_cores=100,
        credit_per_hour=Decimal("400"), lossless_pause=True,
    )
    image = Image(id=ids.new("image"), name="pytorch")
    node = GpuNode(
        id=ids.new("node"), cluster_id=cluster_id, hostname="n1",
        status="ready", lossless_capable=True,
    )
    async with db.begin():
        db.add_all([offering, image, node])

    svc = SchedulerService(db)
    req = SessionCreate(
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        cluster_id=cluster_id, mode="exclusive",
    )
    sess = await svc._persist_pending(req, Principal(user_id=ids.new("user")))
    assert sess.lossless_pause is True


@pytest.mark.asyncio
async def test_lossless_gate_blocked_without_capable_node(db):
    """Even with the offering opted in, no lossless-capable node in the cluster means not
    eligible."""
    cluster_id = ids.new("cluster")
    offering = Offering(
        id=ids.new("offering"), name="A100-excl", resource_class="gpu",
        gpu_model="A100", gpu_mem_mb=81920, gpu_cores=100,
        credit_per_hour=Decimal("400"), lossless_pause=True,
    )
    image = Image(id=ids.new("image"), name="pytorch")
    node = GpuNode(  # capable=False, so the gate cannot pass
        id=ids.new("node"), cluster_id=cluster_id, hostname="n1",
        status="ready", lossless_capable=False,
    )
    async with db.begin():
        db.add_all([offering, image, node])

    svc = SchedulerService(db)
    req = SessionCreate(
        offering_id=offering.id, image_id=image.id, resource_class="gpu",
        cluster_id=cluster_id, mode="exclusive",
    )
    sess = await svc._persist_pending(req, Principal(user_id=ids.new("user")))
    assert sess.lossless_pause is False


# ── desired-payload shape per mode (pure serialization, no db) ──
class _FakeSession:
    """Minimal stand-in carrying the attributes GShareSessionCRD.to_session_spec reads."""

    def __init__(self, **kw) -> None:
        defaults = dict(
            id="ses_test",
            owner_user_id="usr_test",
            group_id=None,
            cluster_id="clu_test",
            cluster_mode="single",
            offering_id="off_test",
            image_id="img_test",
            resource_class="gpu",
            mode=None,
            gpu_mem_mb=None,
            gpu_cores=None,
            # CPU, RAM, and disk snapshotted from the offering flavor; to_session_spec reads them.
            cpu=None,
            mem_gb=None,
            disk_gb=None,
        )
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


def test_exclusive_desired_bypasses_hami():
    """exclusive desired payload = nvidia.com/gpu:1 only, no hami-scheduler."""
    crd = GShareSessionCRD()
    sess = _FakeSession(mode="exclusive", gpu_mem_mb=None, gpu_cores=None)
    req = SessionCreate(
        offering_id="off_test",
        image_id="img_test",
        resource_class="gpu",
        cluster_id="clu_test",
        mode="exclusive",
    )

    spec = crd.to_session_spec(sess, req)
    assert spec["mode"] == "exclusive"
    assert "scheduler_name" not in spec          # bypasses HAMi
    assert "gpu_mem_mb" not in spec and "gpu_cores" not in spec
    assert spec["gpu"] == {"resource": "nvidia.com/gpu", "count": 1}


def test_fractional_desired_carries_hami_fields():
    """fractional desired payload carries hami-scheduler + gpumem + gpucores."""
    crd = GShareSessionCRD()
    sess = _FakeSession(mode="fractional", gpu_mem_mb=8000, gpu_cores=50)
    req = SessionCreate(
        offering_id="off_test",
        image_id="img_test",
        resource_class="gpu",
        cluster_id="clu_test",
        gpu_mem_mb=8000,
        gpu_cores=50,
        mode="fractional",
    )

    spec = crd.to_session_spec(sess, req)
    assert spec["mode"] == "fractional"
    assert spec["scheduler_name"] == "hami-scheduler"
    assert spec["gpu_mem_mb"] == 8000
    assert spec["gpu_cores"] == 50
    assert spec["gpu"] == {"resource": "nvidia.com/gpu", "count": 1}


# ── (3) client priority clamp (sessions_router._clamp_priority) ──
def _req_with_priority(priority: int, group_id: str | None) -> SessionCreate:
    return SessionCreate(
        offering_id="off_test", image_id="img_test", resource_class="gpu",
        cluster_id="clu_test", group_id=group_id, mode="exclusive", priority=priority,
    )


def test_priority_clamped_to_zero_for_member():
    """A priority above 0 from a plain member is clamped to 0, which blocks queue jumping,
    preemption, and dodging reclaim."""
    gid = ids.new("group")
    body = _req_with_priority(9999, gid)
    principal = Principal(user_id=ids.new("user"), memberships={gid: "member"})
    _clamp_priority(principal, body)
    assert body.priority == 0


def test_priority_clamped_to_zero_for_guest_and_no_group():
    """A guest, and a request with no group to match a membership against, are clamped to 0 as
    well."""
    gid = ids.new("group")
    body = _req_with_priority(5, gid)
    _clamp_priority(Principal(user_id=ids.new("user"), memberships={gid: "guest"}), body)
    assert body.priority == 0

    body2 = _req_with_priority(5, None)  # no group given
    _clamp_priority(Principal(user_id=ids.new("user")), body2)
    assert body2.priority == 0


def test_priority_preserved_for_group_admin_and_super_admin():
    """group_admin and above for that group, and super_admin, keep the priority they asked for."""
    gid = ids.new("group")
    body = _req_with_priority(7, gid)
    _clamp_priority(Principal(user_id=ids.new("user"), memberships={gid: "group_admin"}), body)
    assert body.priority == 7

    body_su = _req_with_priority(7, gid)
    _clamp_priority(Principal(user_id=ids.new("user"), global_roles={"super_admin"}), body_su)
    assert body_su.priority == 7

    # An org_admin, which arrives expanded into group memberships, is allowed too.
    body_org = _req_with_priority(7, gid)
    _clamp_priority(Principal(user_id=ids.new("user"), memberships={gid: "org_admin"}), body_org)
    assert body_org.priority == 7


# ── (8) policy resource-sum uses request overrides, not just offering ──
@pytest.mark.asyncio
async def test_resource_sum_uses_request_disk_override(db):
    """A small offering plus a disk_gb override must not slip past the storage_gb limit.

    offering.disk_gb is 10, but req.disk_gb=200 means the pod really takes 200 GiB, so a storage_gb
    limit of 100 must raise QuotaExceeded — the override wins.
    """
    user_id = ids.new("user")
    offering = Offering(
        id=ids.new("offering"), name="cpu-small", resource_class="cpu",
        cpu=2, mem_gb=4, disk_gb=10, credit_per_hour=Decimal("0"),
    )
    pol = ResourcePolicy(
        id=ids.new("pol"), scope="user", scope_id=user_id, limits={"storage_gb": 100},
    )
    async with db.begin():
        db.add_all([offering, pol])

    svc = SchedulerService(db)
    req = SessionCreate(
        offering_id=offering.id, image_id=ids.new("image"), resource_class="cpu",
        cluster_id=ids.new("cluster"), disk_gb=200,
    )
    principal = Principal(user_id=user_id)
    with pytest.raises(QuotaExceeded) as excinfo:
        await svc._check_resource_sum(req, principal, pol)
    assert excinfo.value.details["resource"] == "storage (GiB)"
    assert excinfo.value.details["request"] == 200


@pytest.mark.asyncio
async def test_resource_sum_falls_back_to_offering_when_no_override(db):
    """Without an override, the new usage is computed from the offering specification, as before."""
    user_id = ids.new("user")
    offering = Offering(
        id=ids.new("offering"), name="cpu-ok", resource_class="cpu",
        cpu=2, mem_gb=4, disk_gb=10, credit_per_hour=Decimal("0"),
    )
    pol = ResourcePolicy(
        id=ids.new("pol"), scope="user", scope_id=user_id, limits={"storage_gb": 100},
    )
    async with db.begin():
        db.add_all([offering, pol])

    svc = SchedulerService(db)
    req = SessionCreate(
        offering_id=offering.id, image_id=ids.new("image"), resource_class="cpu",
        cluster_id=ids.new("cluster"),
    )
    # offering.disk_gb of 10 is within the limit of 100, so nothing is raised.
    await svc._check_resource_sum(req, Principal(user_id=user_id), pol)


# ── (7) max-runtime annotation stamped from policy chain (minutes → seconds) ──
@pytest.mark.asyncio
async def test_max_runtime_resolved_and_stamped_seconds(db):
    """A user policy's max_runtime, in minutes, is converted to seconds and stamped as
    gshare.io/max-runtime-sec."""
    owner = ids.new("user")
    pol = ResourcePolicy(id=ids.new("pol"), scope="user", scope_id=owner, max_runtime=60)
    async with db.begin():
        db.add(pol)
    crd = GShareSessionCRD(db=db)
    spec = {"session_id": "ses_test", "owner": owner, "group_id": None}
    max_sec = await crd._resolve_max_runtime_sec(spec)
    assert max_sec == 3600  # 60 minutes x 60 = 3600 seconds
    body = crd.build_object({**spec, "max_runtime_sec": max_sec})
    assert body["metadata"]["annotations"]["gshare.io/max-runtime-sec"] == "3600"


@pytest.mark.asyncio
async def test_max_runtime_absent_when_no_policy(db):
    """With no max_runtime cap at any scope, no annotation is attached: there is no global
    fallback."""
    crd = GShareSessionCRD(db=db)
    spec = {"session_id": "ses_test", "owner": ids.new("user"), "group_id": None}
    assert await crd._resolve_max_runtime_sec(spec) is None
    body = crd.build_object({**spec, "max_runtime_sec": None})
    assert "gshare.io/max-runtime-sec" not in body["metadata"].get("annotations", {})
