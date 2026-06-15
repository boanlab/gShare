"""Startup bootstrap: first super_admin + (all-in-one) local cluster.

An empty system has no super_admin. On startup, ``seed_bootstrap_admin`` ensures a super_admin
account from the ``GSHARE_BOOTSTRAP_ADMIN_EMAIL`` / ``GSHARE_BOOTSTRAP_ADMIN_PASSWORD`` env
(idempotent; the account is forced to change its password on first login).

For the in-cluster all-in-one deployment, ``seed_local_cluster`` ensures the local Cluster row
the operator reports inventory/sessions against (gated by ``GSHARE_BOOTSTRAP_LOCAL_CLUSTER``).
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.core import ids
from app.core.config import settings
from app.core.logging import get_logger
from app.core.passwords import hash_password
from app.db.models import (
    Cluster,
    CreditWallet,
    Image,
    Offering,
    ResourcePolicy,
    ResourcePreset,
    User,
)

log = get_logger(__name__)

# The four catalogue images: session bases published to Docker Hub under boanlab, registered
# idempotently at startup. cuda_version is the real CUDA baked into the registry tag, which the
# session wizard compares against the offering's min_cuda. There is no CPU-only image.
_BASE_IMAGES = [
    {"name": "PyTorch 2.6 (GPU)", "registry": "boanlab/gshare-session:pytorch2.6-cuda12.4-cudnn9", "cuda_version": "12.4"},
    {"name": "TensorFlow 2.18 (GPU)", "registry": "boanlab/gshare-session:tensorflow2.18-cuda12.5-cudnn9", "cuda_version": "12.5"},
    {"name": "ML Base (GPU)", "registry": "boanlab/gshare-session:ml-cuda12.4-cudnn9", "cuda_version": "12.4"},
    {"name": "Ubuntu 24.04 (CPU)", "registry": "boanlab/gshare-session:ml-ubuntu24.04"},
]

# The default GPU offering catalogue: one full-card row per model. The session wizard derives the
# per-model fraction tiers from the full-card VRAM.
#
# gpu_model has to match the name inventory reports (GpuDevice.model) or nothing schedules against
# it — adjust it to the model names your fleet actually reports. cpu, mem, and disk are the host
# resources that accompany a full card, and credit_per_hour is the hourly rate; both are suggested
# defaults an administrator can change. min_cuda is the architecture's minimum CUDA toolkit:
# Ampere sm_80 -> 11.0, Ada sm_89 -> 11.8, Hopper sm_90 -> 12.0, Blackwell sm_120 -> 12.8.
_GPU_OFFERINGS = [
    {"name": "RTX 4090",       "gpu_model": "NVIDIA GeForce RTX 4090",       "gpu_mem_mb": 24564, "cpu": 8,  "mem_gb": 48,  "disk_gb": 100, "credit_per_hour": "100",  "min_cuda": "11.8"},
    {"name": "RTX 5090",       "gpu_model": "NVIDIA GeForce RTX 5090",       "gpu_mem_mb": 32768, "cpu": 12, "mem_gb": 96,  "disk_gb": 150, "credit_per_hour": "150",  "min_cuda": "12.8"},
    {"name": "RTX PRO 5000",   "gpu_model": "NVIDIA RTX PRO 5000 Blackwell", "gpu_mem_mb": 49152, "cpu": 16, "mem_gb": 128, "disk_gb": 200, "credit_per_hour": "200",  "min_cuda": "12.8"},
    {"name": "RTX PRO 6000",   "gpu_model": "NVIDIA RTX PRO 6000 Blackwell", "gpu_mem_mb": 98304, "cpu": 24, "mem_gb": 256, "disk_gb": 400, "credit_per_hour": "300",  "min_cuda": "12.8"},
    {"name": "A100 40GB PCIe", "gpu_model": "NVIDIA A100-PCIE-40GB",         "gpu_mem_mb": 40960, "cpu": 24, "mem_gb": 128, "disk_gb": 300, "credit_per_hour": "300",  "min_cuda": "11.0"},
    {"name": "A100 80GB PCIe", "gpu_model": "NVIDIA A100 80GB PCIe",         "gpu_mem_mb": 81920, "cpu": 32, "mem_gb": 256, "disk_gb": 400, "credit_per_hour": "400",  "min_cuda": "11.0"},
    {"name": "A100 40GB SXM4", "gpu_model": "NVIDIA A100-SXM4-40GB",         "gpu_mem_mb": 40960, "cpu": 24, "mem_gb": 128, "disk_gb": 300, "credit_per_hour": "350",  "min_cuda": "11.0"},
    {"name": "A100 80GB SXM4", "gpu_model": "NVIDIA A100-SXM4-80GB",         "gpu_mem_mb": 81920, "cpu": 32, "mem_gb": 256, "disk_gb": 400, "credit_per_hour": "450",  "min_cuda": "11.0"},
    {"name": "H100 80GB PCIe", "gpu_model": "NVIDIA H100 PCIe",              "gpu_mem_mb": 81920, "cpu": 32, "mem_gb": 512, "disk_gb": 500, "credit_per_hour": "800",  "min_cuda": "12.0"},
    {"name": "H100 80GB SXM5", "gpu_model": "NVIDIA H100 80GB HBM3",         "gpu_mem_mb": 81920, "cpu": 32, "mem_gb": 512, "disk_gb": 500, "credit_per_hour": "1000", "min_cuda": "12.0"},
    {"name": "H100 NVL 94GB",  "gpu_model": "NVIDIA H100 NVL",               "gpu_mem_mb": 96256, "cpu": 48, "mem_gb": 640, "disk_gb": 600, "credit_per_hour": "1100", "min_cuda": "12.0"},
]


# Default resource presets, split into compute (cpu, mem, disk) and gpu (a per-model fraction).
_COMPUTE_PRESETS = [
    {"name": "Compute S", "cpu": 4,  "mem_gb": 32,  "disk_gb": 100},
    {"name": "Compute M", "cpu": 8,  "mem_gb": 64,  "disk_gb": 200},
    {"name": "Compute L", "cpu": 16, "mem_gb": 128, "disk_gb": 400},
]
# GPU presets are fractional: VRAM is the chosen model's full card multiplied by gpu_frac. The core
# share is a suggested value derived from the same fraction; exclusive takes 100%.
_GPU_PRESETS = [
    {"name": "GPU XL (1/2)",   "gpu_frac": 0.5,     "gpu_cores": 50,  "mode": "fractional"},
    {"name": "GPU L (1/4)",    "gpu_frac": 0.25,    "gpu_cores": 25,  "mode": "fractional"},
    {"name": "GPU M (1/8)",    "gpu_frac": 0.125,   "gpu_cores": 13,  "mode": "fractional"},
    {"name": "GPU S (1/16)",   "gpu_frac": 0.0625,  "gpu_cores": 6,   "mode": "fractional"},
    {"name": "GPU SS (1/32)",  "gpu_frac": 0.03125, "gpu_cores": 3,   "mode": "fractional"},
    {"name": "GPU Exclusive (full card)", "gpu_frac": 1.0, "gpu_cores": 100, "mode": "exclusive"},
]
# Preset names superseded by the split compute/gpu presets, removed on every re-seed. This also
# covers GPU presets renamed when the ladder was remapped to XL-L-M-S-SS, and the original
# Korean-named presets replaced when the catalogue moved to English.
_LEGACY_PRESET_NAMES = [
    "Small (1/4)", "Medium (1/2)", "Large (full)", "XLarge (48G)",
    "GPU S (1/8)", "GPU M (1/4)", "GPU L (1/2)", "GPU SS (1/16)", "GPU SSS (1/32)",
    "컴퓨트 S", "컴퓨트 M", "컴퓨트 L", "GPU 전용 (풀카드)",
]

# The default global resource policy: the guardrail applied when no more specific policy exists.
# Administrators can change it.
_DEFAULT_POLICY = {
    "max_concurrent": 3, "max_queued": 5, "max_runtime_min": 2880, "idle_timeout_sec": 3600,
    "limits": {"cpu": 64, "mem_gb": 512, "gpu_mem_mb": 196608, "gpu_cores": 800, "storage_gb": 1000},
}


async def seed_presets() -> None:
    """Ensure the default compute and GPU presets exist, idempotently by name, and drop legacy
    ones."""
    from sqlalchemy import delete

    from app.db.base import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as db:
        async with db.begin():
            # Remove superseded presets; the split compute/gpu ones replace them.
            await db.execute(delete(ResourcePreset).where(ResourcePreset.name.in_(_LEGACY_PRESET_NAMES)))
            for spec in _COMPUTE_PRESETS:
                if await db.scalar(select(ResourcePreset.id).where(ResourcePreset.name == spec["name"])) is not None:
                    continue
                db.add(ResourcePreset(
                    id=ids.new("preset"), name=spec["name"], kind="compute",
                    cpu=spec["cpu"], mem=spec["mem_gb"], disk_gb=spec["disk_gb"],
                ))
                log.info("compute preset seeded: %s", spec["name"])
            for spec in _GPU_PRESETS:
                if await db.scalar(select(ResourcePreset.id).where(ResourcePreset.name == spec["name"])) is not None:
                    continue
                db.add(ResourcePreset(
                    id=ids.new("preset"), name=spec["name"], kind="gpu",
                    gpu_frac=spec["gpu_frac"], gpu_cores=spec["gpu_cores"], mode=spec["mode"],
                ))
                log.info("gpu preset seeded: %s", spec["name"])


async def seed_default_policy() -> None:
    """Ensure the global resource policy exists. Idempotent: an existing policy is left alone."""
    from app.db.base import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as db:
        async with db.begin():
            exists = await db.scalar(select(ResourcePolicy.id).where(ResourcePolicy.scope == "global"))
            if exists is not None:
                return
            db.add(ResourcePolicy(
                id=ids.new("policy"), scope="global", scope_id="*",
                max_concurrent=_DEFAULT_POLICY["max_concurrent"],
                max_queued=_DEFAULT_POLICY["max_queued"],
                max_runtime=_DEFAULT_POLICY["max_runtime_min"],
                idle_timeout=_DEFAULT_POLICY["idle_timeout_sec"],
                limits=dict(_DEFAULT_POLICY["limits"]),
            ))
            log.info("default common(global) resource policy seeded")


async def seed_offerings() -> None:
    """Ensure the default GPU offering catalogue exists, skipping duplicates by gpu_model.

    One full-card row per model; the wizard derives the fraction tiers. If your fleet reports GPU
    model names that differ from these, adjust gpu_model or nothing will schedule against them.
    """
    from app.db.base import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as db:
        async with db.begin():
            for spec in _GPU_OFFERINGS:
                existing = await db.scalar(select(Offering).where(Offering.gpu_model == spec["gpu_model"]))
                if existing is not None:
                    # Backfill min_cuda on an existing offering that predates the column.
                    if existing.min_cuda is None and spec.get("min_cuda"):
                        existing.min_cuda = spec["min_cuda"]
                        log.info("offering min_cuda backfilled: %s -> %s", spec["gpu_model"], spec["min_cuda"])
                    continue
                db.add(Offering(
                    id=ids.new("offering"),
                    name=spec["name"],
                    resource_class="gpu",
                    gpu_model=spec["gpu_model"],
                    gpu_mem_mb=spec["gpu_mem_mb"],
                    gpu_cores=100,
                    cpu=spec["cpu"],
                    mem_gb=spec["mem_gb"],
                    disk_gb=spec["disk_gb"],
                    credit_per_hour=Decimal(spec["credit_per_hour"]),
                    min_cuda=spec.get("min_cuda"),
                ))
                log.info("offering seeded: %s (%d MB)", spec["gpu_model"], spec["gpu_mem_mb"])

            # A single CPU offering carrying the default cpu, mem, and disk for CPU sessions.
            # Billing is computed from the configured compute rates times the amounts, so
            # offering.credit_per_hour stays 0. Choosing a compute preset overrides these values.
            cpu_exists = await db.scalar(select(Offering.id).where(Offering.resource_class == "cpu"))
            if cpu_exists is None:
                db.add(Offering(
                    id=ids.new("offering"),
                    name="CPU (compute)",
                    resource_class="cpu",
                    gpu_model=None,
                    gpu_mem_mb=0,
                    gpu_cores=0,
                    cpu=4,
                    mem_gb=16,
                    disk_gb=50,
                    credit_per_hour=Decimal("0"),
                ))
                log.info("offering seeded: CPU (compute) 4vCPU/16GiB/50GiB")


async def seed_system_wallet() -> None:
    """Ensure the system credit wallet, the root of the hierarchy, exists.

    Its monthly_grant is the monthly issuance ceiling: the sum of the organizations' monthly refills
    cannot exceed it. A super_admin sets that ceiling and tops the wallet up when needed."""
    from app.db.base import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as db:
        async with db.begin():
            exists = await db.scalar(select(CreditWallet.id).where(CreditWallet.owner_type == "system"))
            if exists is not None:
                return
            db.add(CreditWallet(
                id=ids.new("wallet"), owner_type="system", owner_id="system",
                balance=Decimal("0"), reserved=Decimal("0"),
            ))
            log.info("system credit wallet seeded (monthly total holder)")


async def seed_base_images() -> None:
    """Ensure the four catalogue images exist, skipping duplicates by registry reference.

    This is what lets users and administrators pick an image on the images screen without importing
    anything first. They are registered with import_status=ready, since the base images already
    exist in the registry.
    """
    from app.db.base import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as db:
        async with db.begin():
            for spec in _BASE_IMAGES:
                existing = await db.scalar(select(Image).where(Image.registry == spec["registry"]))
                if existing is not None:
                    # Backfill cuda_version on an existing image that predates the column.
                    cuda = spec.get("cuda_version")
                    if cuda and not (existing.tags or {}).get("cuda_version"):
                        existing.tags = {**(existing.tags or {}), "cuda_version": cuda}
                        log.info("base image cuda_version backfilled: %s -> %s", spec["registry"], cuda)
                    continue
                tags: dict = {"supported_gpus": [], "base": True}
                if spec.get("cuda_version"):
                    tags["cuda_version"] = spec["cuda_version"]
                db.add(Image(
                    id=ids.new("image"),
                    name=spec["name"],
                    registry=spec["registry"],
                    kind="container",
                    import_status="ready",
                    tags=tags,
                ))
                log.info("base image seeded: %s", spec["registry"])


async def seed_local_cluster() -> None:
    """Ensure the local Cluster row exists in an all-in-one, in-cluster deployment.

    When the control plane runs inside the cluster it drives, the external kubeconfig registration
    flow does not apply, so the Cluster is seeded with the fixed id ``LOCAL_CLUSTER_ID`` — which
    must equal the operator's --cluster-id. Without it the operator's inventory callback fails on
    the gpu_node.cluster_id foreign key. Does nothing when ``BOOTSTRAP_LOCAL_CLUSTER`` is off, as in
    production. """
    if not settings.BOOTSTRAP_LOCAL_CLUSTER:
        return
    from app.db.base import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as db:
        async with db.begin():
            existing = await db.get(Cluster, settings.LOCAL_CLUSTER_ID)
            if existing is not None:
                return
            db.add(Cluster(
                id=settings.LOCAL_CLUSTER_ID,
                name=settings.LOCAL_CLUSTER_NAME,
                role="primary",
                api_server="https://kubernetes.default.svc",   # in-cluster apiserver
                runtime="containerd",
                status="connected",
                kubeconfig_secret_ref="",                       # in-cluster config — no kubeconfig
            ))
            log.info("local cluster seeded: %s (%s)", settings.LOCAL_CLUSTER_ID, settings.LOCAL_CLUSTER_NAME)


async def seed_bootstrap_admin() -> None:
    """Ensure a super_admin account exists, from BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD.

    - No such account: create one as super_admin with a hashed password, must_change_password set,
      and a personal wallet.
    - Existing account: ensure it is super_admin, and set the password from the environment only when
      it has none — a password the user already changed is preserved.

    Does nothing when either the email or the password is empty.
    """
    email = (settings.BOOTSTRAP_ADMIN_EMAIL or "").strip().lower()
    password = settings.BOOTSTRAP_ADMIN_PASSWORD or ""
    if not email or not password:
        return
    from app.db.base import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as db:
        async with db.begin():
            user = (
                await db.scalars(select(User).where(func.lower(User.email) == email))
            ).first()
            if user is None:
                user = User(
                    id=ids.new("user"),
                    email=email,
                    name="Super Admin",  # the display name is editable from the console
                    status="active",
                    global_role="super_admin",
                    global_roles=["super_admin"],
                    password_hash=hash_password(password),
                    must_change_password=True,
                )
                db.add(user)
                await db.flush()
                db.add(CreditWallet(
                    id=ids.new("wallet"), owner_type="user", owner_id=user.id,
                    balance=Decimal("0"), reserved=Decimal("0"),
                ))
                log.info("bootstrap admin created: %s", email)
            else:
                if user.global_role != "super_admin":
                    user.global_role = "super_admin"
                if "super_admin" not in (user.global_roles or []):
                    user.global_roles = sorted({*(user.global_roles or []), "super_admin"})
                if not user.password_hash:
                    user.password_hash = hash_password(password)
                    user.must_change_password = True
                    log.info("bootstrap admin password seeded: %s", email)
