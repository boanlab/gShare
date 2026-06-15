"""SQLAlchemy 2.x models for the entities + Cluster.

Conventions:
- IDs are prefixed ULIDs (``app.core.ids``); PK is a plain String here (caller assigns).
- Money is ``Numeric(18, 2)``. VRAM in MB, cores in %.
- Soft-delete via nullable ``deleted_at`` on entities that support it.
- Key invariants are encoded as CHECK / partial UNIQUE constraints:
  wallet Σ-invariant, txn idempotency UNIQUE, gpu_device overcommit CHECK,
  one live Allocation per session/device, etc.

Some relationships/back_populates and secondary columns are intentionally omitted to keep
the model compact.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


# ── Identity / tenancy ──
class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "user"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # usr_ULID
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")
    # global_roles is the source of truth and may hold several roles. global_role is the primary one
    # derived from it, kept in sync so scalar checks and queries (User.global_role.in_([...])) work.
    global_role: Mapped[str | None] = mapped_column(String, default=None)  # derived primary role: super_admin or None
    # server_default '[]' relies on the implicit cast for a jsonb column. No explicit ::jsonb cast,
    # so SQLite create_all keeps working in tests.
    global_roles: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    # local password login (AUTH_ALLOW_LOCAL_PASSWORD).
    password_hash: Mapped[str | None] = mapped_column(String, default=None)
    # Accounts created by an administrator, and the bootstrap account, must change their password at
    # first login.
    must_change_password: Mapped[bool] = mapped_column(default=False, server_default=text("false"))


class Notification(Base, TimestampMixin):
    __tablename__ = "notification"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # ntf_ULID
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Organization(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "organization"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # org_ULID
    name: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, default="active")
    timezone: Mapped[str] = mapped_column(String, default="Asia/Seoul")


class Project(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "group"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # prj_ULID
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")


class Membership(Base, TimestampMixin):
    __tablename__ = "membership"
    # One table holds two kinds of membership:
    #  - Group membership: group_id set, org_id NULL, role in {group_admin, member, guest}
    #  - Organization membership: org_id set, group_id NULL, role = org_admin, since organization
    #    admins are appointed at the organization level
    id: Mapped[str] = mapped_column(String, primary_key=True)            # mbr_ULID
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("group.id"), index=True, default=None)
    org_id: Mapped[str | None] = mapped_column(ForeignKey("organization.id"), index=True, default=None)
    role: Mapped[str] = mapped_column(String)  # org_admin(org)|group_admin|member|guest(group)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)  # expiry, for guest memberships
    __table_args__ = (
        Index("uq_membership_user_group", "user_id", "group_id", unique=True, postgresql_where=text("group_id IS NOT NULL")),
        Index("uq_membership_user_org", "user_id", "org_id", unique=True, postgresql_where=text("org_id IS NOT NULL")),
    )


# ── Credit ──
class CreditWallet(Base, TimestampMixin):
    __tablename__ = "credit_wallet"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # wal_ULID
    owner_type: Mapped[str] = mapped_column(String)                      # user|project
    owner_id: Mapped[str] = mapped_column(String)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    monthly_grant: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)  # monthly refill amount; 0 disables refills
    version: Mapped[int] = mapped_column(Integer, default=0)             # optimistic lock
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id"),
        # Σ-invariant: 0 <= reserved <= balance.
        CheckConstraint(
            "balance >= 0 AND reserved >= 0 AND reserved <= balance", name="wallet_sigma"
        ),
    )


class CreditTransaction(Base, TimestampMixin):
    __tablename__ = "credit_transaction"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # txn_ULID
    wallet_id: Mapped[str] = mapped_column(ForeignKey("credit_wallet.id"), index=True)
    type: Mapped[str] = mapped_column(String)            # topup|hold|consume|refund|settle|adjust
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    ref: Mapped[str | None] = mapped_column(String, index=True, default=None)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    __table_args__ = (Index("ix_txn_wallet_created", "wallet_id", "created_at"),)


class TopupRequest(Base, TimestampMixin):
    __tablename__ = "topup_request"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # top_ULID
    wallet_id: Mapped[str] = mapped_column(ForeignKey("credit_wallet.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    status: Mapped[str] = mapped_column(String, default="pending")       # pending|approved|rejected
    requester_id: Mapped[str] = mapped_column(String)
    decided_by: Mapped[str | None] = mapped_column(String, default=None)
    decided_reason: Mapped[str | None] = mapped_column(String, default=None)  # decision note, such as the reason for a rejection


class CreditAllocationRequest(Base, TimestampMixin):
    """A hierarchical credit allocation request, with escalation from user to group to organization
    to super_admin.

    fulfiller_scope and fulfiller_id identify who approves (and therefore allocates):
      target=user    -> group_admin (fulfiller_scope=project, fulfiller_id = the project)
      target=project -> org_admin   (fulfiller_scope=org,     fulfiller_id = the organization)
      target=org → super/billing (fulfiller_scope=system, fulfiller_id=NULL)
    parent_id points at the parent request created when this one is escalated upwards.
    """
    __tablename__ = "credit_allocation_request"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # car_ULID
    requester_id: Mapped[str] = mapped_column(String, index=True)
    target_wallet_id: Mapped[str] = mapped_column(ForeignKey("credit_wallet.id"), index=True)
    level: Mapped[str] = mapped_column(String)                           # user|project|org
    fulfiller_scope: Mapped[str] = mapped_column(String)                 # project|org|system
    fulfiller_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    status: Mapped[str] = mapped_column(String, default="pending")       # pending|approved|rejected|escalated
    note: Mapped[str | None] = mapped_column(String, default=None)
    parent_id: Mapped[str | None] = mapped_column(String, default=None)
    decided_by: Mapped[str | None] = mapped_column(String, default=None)


# ── Catalog ──
class Offering(Base, TimestampMixin):
    __tablename__ = "offering"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # off_ULID
    name: Mapped[str] = mapped_column(String)
    resource_class: Mapped[str] = mapped_column(String)                  # gpu|cpu
    gpu_model: Mapped[str | None] = mapped_column(String, default=None)
    gpu_mem_mb: Mapped[int | None] = mapped_column(Integer, default=None)
    gpu_cores: Mapped[int | None] = mapped_column(Integer, default=None)
    cpu: Mapped[int | None] = mapped_column(Integer, default=None)        # vCPU cores; the pod sets request = limit
    mem_gb: Mapped[int | None] = mapped_column(Integer, default=None)     # RAM(GiB) — Pod request=limit
    disk_gb: Mapped[int | None] = mapped_column(Integer, default=None)    # ephemeral scratch in GiB; the pod sets request = limit
    credit_per_hour: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    status: Mapped[str] = mapped_column(String, default="active", server_default=text("'active'"))  # active|inactive
    min_cuda: Mapped[str | None] = mapped_column(String, default=None)   # minimum CUDA, e.g. '12.8'; GPU offerings only
    # Administrator opt-in for lossless pause. Session creation additionally requires the cluster to
    # have a lossless-capable node.
    lossless_pause: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class OfferingPriceHistory(Base, TimestampMixin):
    __tablename__ = "offering_price_history"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    offering_id: Mapped[str] = mapped_column(ForeignKey("offering.id"), index=True)
    credit_per_hour: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    changed_by: Mapped[str | None] = mapped_column(String, default=None)


class ResourcePreset(Base, TimestampMixin):
    __tablename__ = "resource_preset"
    # Two kinds: compute (cpu, mem, disk) and gpu (a per-model fraction of VRAM and cores).
    id: Mapped[str] = mapped_column(String, primary_key=True)            # pst_ULID
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="gpu", server_default=text("'gpu'"))  # compute|gpu
    # compute preset
    cpu: Mapped[int | None] = mapped_column(Integer, default=None)
    mem: Mapped[int | None] = mapped_column(Integer, default=None)       # RAM(GiB), API surfaces mem_gb
    disk_gb: Mapped[int | None] = mapped_column(Integer, default=None)
    # gpu preset; VRAM is derived as the chosen model's full card multiplied by gpu_frac
    gpu_frac: Mapped[float | None] = mapped_column(Numeric(5, 3), default=None)
    mode: Mapped[str | None] = mapped_column(String, default=None)       # fractional|exclusive
    gpu_mem_mb: Mapped[int | None] = mapped_column(Integer, default=None)
    gpu_cores: Mapped[int | None] = mapped_column(Integer, default=None)


class ResourcePolicy(Base, TimestampMixin):
    __tablename__ = "resource_policy"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # pol_ULID
    scope: Mapped[str] = mapped_column(String)                           # org|group|user
    scope_id: Mapped[str] = mapped_column(String, index=True)
    max_concurrent: Mapped[int | None] = mapped_column(Integer, default=None)
    max_runtime: Mapped[int | None] = mapped_column(Integer, default=None)
    idle_timeout: Mapped[int | None] = mapped_column(Integer, default=None)
    max_queued: Mapped[int | None] = mapped_column(Integer, default=None)
    limits: Mapped[dict] = mapped_column(JSONB, default=dict)


class Image(Base, TimestampMixin):
    __tablename__ = "image"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # img_ULID
    name: Mapped[str] = mapped_column(String)
    registry: Mapped[str | None] = mapped_column(String, default=None)
    tags: Mapped[dict] = mapped_column(JSONB, default=dict)
    kind: Mapped[str] = mapped_column(String, default="container")       # container|iso|template
    import_status: Mapped[str | None] = mapped_column(String, default=None)  # pulling|ready|failed
    public: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))  # whether the session wizard offers this image


class ImageBuild(Base, TimestampMixin):
    __tablename__ = "image_build"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # bld_ULID
    group_id: Mapped[str] = mapped_column(ForeignKey("group.id"), index=True)
    source: Mapped[str] = mapped_column(String)                          # dockerfile|git
    status: Mapped[str] = mapped_column(String, default="pending")
    image_ref: Mapped[str | None] = mapped_column(String, default=None)


# ── Clusters / nodes / devices ──
class Cluster(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "cluster"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # clu_ULID
    # Name unique only among live clusters → re-registration after soft-delete works.
    name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="primary")         # primary|standby
    api_server: Mapped[str] = mapped_column(String)
    runtime: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    # Secret reference only — never store plaintext kubeconfig.
    kubeconfig_secret_ref: Mapped[str] = mapped_column(String)
    __table_args__ = (
        Index(
            "uq_cluster_name_active",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class GpuNode(Base, TimestampMixin):
    __tablename__ = "gpu_node"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # nod_ULID
    cluster_id: Mapped[str] = mapped_column(ForeignKey("cluster.id"), index=True)
    hostname: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="ready")         # ready|cordoned|offline
    cpu: Mapped[int | None] = mapped_column(Integer, default=None)        # node CPU cores
    mem: Mapped[int | None] = mapped_column(Integer, default=None)        # node RAM in GiB
    disk: Mapped[int | None] = mapped_column(Integer, default=None)       # node ephemeral storage in GiB
    region: Mapped[str | None] = mapped_column(String, default=None)
    # Whether the lossless-pause prerequisites (cuda-checkpoint plus CRIU) are labelled ready on the
    # node; reported by the operator's inventory controller.
    lossless_capable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class GpuDevice(Base, TimestampMixin):
    __tablename__ = "gpu_device"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # dev_ULID
    node_id: Mapped[str] = mapped_column(ForeignKey("gpu_node.id"), index=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("cluster.id"), index=True)
    model: Mapped[str] = mapped_column(String)
    gpu_uuid: Mapped[str] = mapped_column(String, unique=True)
    total_mem_mb: Mapped[int] = mapped_column(Integer)
    used_mem_mb: Mapped[int] = mapped_column(Integer, default=0)
    total_cores: Mapped[int] = mapped_column(Integer, default=100)
    used_cores: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="ready")
    mode: Mapped[str | None] = mapped_column(String, default=None)       # fractional|exclusive|mig
    # In-place GPU yield lending state: "" (normal), "yielded" (the owner yielded, so the physical
    # card is free and lendable), or "lent" (a preemptible spot session is using it). used_* always
    # reflects resident occupancy; borrows are not counted.
    # (docs/paper/manuscript, §Design)
    lend_state: Mapped[str] = mapped_column(String, default="", server_default=text("''"))
    __table_args__ = (
        # Final overcommit defense.
        CheckConstraint(
            "used_mem_mb <= total_mem_mb AND used_cores <= total_cores", name="no_overcommit"
        ),
        Index("ix_dev_node_status_mode", "node_id", "status", "mode"),
    )


class NodeHealthEvent(Base, TimestampMixin):
    # Written via the operator audit/health callbacks; this plane only reads/ledgers.
    __tablename__ = "node_health_event"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # nhe_ULID
    node_id: Mapped[str] = mapped_column(ForeignKey("gpu_node.id"), index=True)
    kind: Mapped[str] = mapped_column(String)                            # xid|ecc|temp|down
    severity: Mapped[str] = mapped_column(String)
    action: Mapped[str | None] = mapped_column(String, default=None)     # cordon|alert


# ── Sessions / allocation / queue ──
class Session(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "session"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # ses_ULID
    name: Mapped[str | None] = mapped_column(String, default=None)       # user-chosen display name
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("group.id"), index=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("cluster.id"), index=True)
    cluster_mode: Mapped[str] = mapped_column(String, default="single")  # single|multi
    offering_id: Mapped[str] = mapped_column(ForeignKey("offering.id"))
    image_id: Mapped[str] = mapped_column(ForeignKey("image.id"))
    resource_class: Mapped[str] = mapped_column(String)                  # gpu|cpu
    mode: Mapped[str | None] = mapped_column(String, default=None)       # fractional|exclusive|mig
    gpu_mem_mb: Mapped[int | None] = mapped_column(Integer, default=None)
    gpu_cores: Mapped[int | None] = mapped_column(Integer, default=None)
    # CPU, RAM, and disk snapshot the offering's fixed values at creation time, so later changes to
    # the offering do not move a running session. The pod sets request = limit.
    cpu: Mapped[int | None] = mapped_column(Integer, default=None)
    mem_gb: Mapped[int | None] = mapped_column(Integer, default=None)
    disk_gb: Mapped[int | None] = mapped_column(Integer, default=None)
    bound_gpu_uuid: Mapped[str | None] = mapped_column(String, default=None)
    billing_wallet_id: Mapped[str | None] = mapped_column(
        ForeignKey("credit_wallet.id"), default=None
    )  # NULL allowed for cpu (free)
    # Lossless-pause eligibility, gated at creation on offering.lossless_pause and the cluster
    # having a lossless-capable node. Passed through as spec.losslessPause so the operator attempts
    # a checkpoint on pause, falling back to cold when it cannot.
    lossless_pause: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # In-place GPU yield: "cold" (the default, deletes the pod) or "yield" (keeps the pod and evicts
    # VRAM, so the resume is lossless). Under yield, stop keeps the allocation because the live pod
    # still holds the card, and start skips readmission — the operator just toggles VRAM back.
    # (See docs/paper/manuscript, §Design.)
    pause_mode: Mapped[str] = mapped_column(String, default="cold", server_default=text("'cold'"))
    # Spot eligibility: this session may borrow a yielded card, and is reclaimed when the resident
    # comes back.
    preemptible: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Scheduling priority; higher wins. Active preemption: when no capacity is free, a request can
    # make a lower-priority yieldable session yield and borrow its card. Reclaim respects priority
    # too, so a lower-priority victim cannot preempt a higher-priority spot session back.
    # (See docs/paper/manuscript, §Design.)
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # status: pending|preparing|running|paused|terminating|terminated|error
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    # snapshot of unit price at admission so price changes don't re-bill
    credit_per_hour_snapshot: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    device_total_mem_mb: Mapped[int | None] = mapped_column(Integer, default=None)
    pod_ref: Mapped[str | None] = mapped_column(String, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Allocation(Base, TimestampMixin):
    """Session<->GPU occupancy ledger; one live row per session idempotency."""
    __tablename__ = "allocation"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # alc_ULID
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), index=True)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("gpu_device.id"), default=None)
    gpu_uuid: Mapped[str | None] = mapped_column(String, default=None)
    gpu_mem_mb: Mapped[int | None] = mapped_column(Integer, default=None)
    gpu_cores: Mapped[int | None] = mapped_column(Integer, default=None)
    status: Mapped[str] = mapped_column(String, default="reserved")      # reserved|bound|released
    # "resident" is a normal allocation and counts towards device.used_*. "spot" is a preemptible
    # session borrowing a yielded card: the physical card is free, so it is excluded from used_*,
    # from drift reconciliation, and from capacity sums. (See docs/paper/manuscript, §Design.)
    kind: Mapped[str] = mapped_column(String, default="resident", server_default=text("'resident'"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    __table_args__ = (
        # One live allocation per session: partial UNIQUE WHERE ended_at IS NULL.
        Index(
            "uq_alloc_session_live", "session_id",
            unique=True, postgresql_where=(ended_at.is_(None)),
        ),
        # There is deliberately no UNIQUE constraint on live allocations per device: fractional
        # sharing requires several sessions to coexist on one physical GPU. Exclusive isolation is
        # instead guaranteed by the scheduler reserving the card's whole capacity (used = total) for
        # an exclusive session, together with the no_overcommit check.
    )


class QueueEntry(Base, TimestampMixin):
    __tablename__ = "queue_entry"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # que_ULID
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), unique=True)
    session_req: Mapped[dict] = mapped_column(JSONB, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_queue_priority_enqueued", "priority", "enqueued_at"),)


class SessionCheckpoint(Base, TimestampMixin):
    __tablename__ = "session_checkpoint"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # ckp_ULID
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), index=True)
    type: Mapped[str] = mapped_column(String)                            # criu|app
    storage_ref: Mapped[str | None] = mapped_column(String, default=None)
    size_bytes: Mapped[int | None] = mapped_column(Integer, default=None)


# ── Connection token also lives in Redis; row optional for audit ──
class ConnectionToken(Base, TimestampMixin):
    __tablename__ = "connection_token"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # cnx_ULID
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), index=True)
    kind: Mapped[str] = mapped_column(String)                            # vscode|jupyter|webapp
    token_hash: Mapped[str] = mapped_column(String)                      # hash only, never plaintext
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ── Storage ──
class StorageVolume(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "storage_volume"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # vol_ULID
    scope: Mapped[str] = mapped_column(String)                           # user|project
    scope_id: Mapped[str] = mapped_column(String, index=True)
    type: Mapped[str] = mapped_column(String)                            # home|group|dataset|scratch
    name: Mapped[str | None] = mapped_column(String, default=None)       # user-chosen display name
    access_mode: Mapped[str] = mapped_column(String)                     # RWO|RWX|ROX
    owner_id: Mapped[str | None] = mapped_column(String, default=None)
    host: Mapped[str | None] = mapped_column(String, default=None)
    quota_gb: Mapped[int] = mapped_column(Integer, default=0)
    used_gb: Mapped[int] = mapped_column(Integer, default=0)
    # Partial unique index including the name, excluding soft-deleted rows, so one scope can hold
    # several volumes as long as their names differ.
    __table_args__ = (
        Index(
            "uq_storage_volume_scope_active",
            "scope", "scope_id", "type", "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class StorageFolder(Base, TimestampMixin):
    __tablename__ = "storage_folder"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # fld_ULID
    volume_id: Mapped[str] = mapped_column(ForeignKey("storage_volume.id"), index=True)
    path: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)


class VolumeMount(Base, TimestampMixin):
    __tablename__ = "volume_mount"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), index=True)
    volume_id: Mapped[str] = mapped_column(ForeignKey("storage_volume.id"), index=True)
    mount_path: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String)                            # ro|rw


class VolumePermission(Base, TimestampMixin):
    __tablename__ = "volume_permission"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # vpm_ULID
    volume_id: Mapped[str] = mapped_column(ForeignKey("storage_volume.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    role: Mapped[str] = mapped_column(String)                            # owner|rw|ro
    __table_args__ = (UniqueConstraint("volume_id", "user_id"),)


class VolumeQuotaRequest(Base, TimestampMixin):
    __tablename__ = "volume_quota_request"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # qrq_ULID
    volume_id: Mapped[str] = mapped_column(ForeignKey("storage_volume.id"), index=True)
    requested_gb: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="pending")       # pending|approved|rejected
    requester_id: Mapped[str] = mapped_column(String)
    decided_by: Mapped[str | None] = mapped_column(String, default=None)


class VolumeSnapshot(Base, TimestampMixin):
    __tablename__ = "volume_snapshot"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # snp_ULID
    volume_id: Mapped[str] = mapped_column(ForeignKey("storage_volume.id"), index=True)
    status: Mapped[str] = mapped_column(String, default="creating")
    size_bytes: Mapped[int | None] = mapped_column(Integer, default=None)


# ── Budgets / FinOps ──
class Budget(Base, TimestampMixin):
    __tablename__ = "budget"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # bdg_ULID
    scope: Mapped[str] = mapped_column(String)                           # org|project
    scope_id: Mapped[str] = mapped_column(String, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period: Mapped[str] = mapped_column(String, default="monthly")
    limit_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    spent_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    action: Mapped[str] = mapped_column(String, default="alert")         # alert|block
    __table_args__ = (UniqueConstraint("scope", "scope_id", "period_start"),)


class BudgetAlert(Base, TimestampMixin):
    __tablename__ = "budget_alert"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # alr_ULID
    budget_id: Mapped[str] = mapped_column(ForeignKey("budget.id"), index=True)
    threshold_pct: Mapped[int] = mapped_column(Integer)                  # 80|100
    channel: Mapped[str | None] = mapped_column(String, default=None)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


# ── Webhooks / audit ──
class WebhookSubscription(Base, TimestampMixin):
    __tablename__ = "webhook_subscription"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # wbh_ULID
    # Owning organization; an org_admin manages only their own subscriptions. NULL means global,
    # which is super_admin only.
    org_id: Mapped[str | None] = mapped_column(String, default=None, index=True)
    scope: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    events: Mapped[dict] = mapped_column(JSONB, default=dict)
    secret: Mapped[str | None] = mapped_column(String, default=None)
    status: Mapped[str] = mapped_column(String, default="active")


class AuditLog(Base, TimestampMixin):
    """Hash-chained audit log; this plane is the only writer."""
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # aud_ULID
    actor: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String, index=True)
    target: Mapped[str | None] = mapped_column(String, default=None)
    result: Mapped[str | None] = mapped_column(String, default=None)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String, default=None)
    # Hierarchy scope, used when querying the audit log. Deliberately outside the hash-chain
    # payload, which keeps append-only inserts safe.
    org_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    group_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    prev_hash: Mapped[str | None] = mapped_column(String, default=None)  # hash chain
    entry_hash: Mapped[str | None] = mapped_column(String, default=None)
