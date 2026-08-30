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

from datetime import UTC, datetime
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
    Text,
    UniqueConstraint,
    event,
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
    # Soft delete: the bell hides dismissed rows; the my-page notification log keeps them.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


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
    # Credits minted into a member's personal wallet the first time they join this group
    # (welcome grant; 0 disables). Idempotent per (group, user) — re-adding never double-pays.
    default_member_credit: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), server_default=text("0")
    )


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
    note: Mapped[str | None] = mapped_column(String, default=None)            # requester's justification


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


class ResourceRequest(Base, TimestampMixin):
    """A user's ask for a bigger compute quota (cpu / mem_gb / storage_gb targets).

    Approval upserts a USER-scope ResourcePolicy carrying ONLY the granted keys — the per-field
    most-specific merge inherits everything else from group/org/global, so the base policy stays
    the single default and per-user rows never fork the whole policy."""

    __tablename__ = "resource_request"
    id: Mapped[str] = mapped_column(String, primary_key=True)             # rrq_ULID
    user_id: Mapped[str] = mapped_column(String, index=True)
    group_id: Mapped[str | None] = mapped_column(String, index=True, default=None)  # approver scope
    cpu: Mapped[int | None] = mapped_column(Integer, default=None)        # target vCPU sum
    mem_gb: Mapped[int | None] = mapped_column(Integer, default=None)     # target GiB sum
    storage_gb: Mapped[int | None] = mapped_column(Integer, default=None) # target GB sum
    gpu_mem_mb: Mapped[int | None] = mapped_column(Integer, default=None)  # target VRAM MB sum
    gpu_cores: Mapped[int | None] = mapped_column(Integer, default=None)   # target core % sum
    note: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")        # pending|approved|rejected
    decided_by: Mapped[str | None] = mapped_column(String, default=None)
    decided_reason: Mapped[str | None] = mapped_column(String, default=None)


class Image(Base, TimestampMixin):
    __tablename__ = "image"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # img_ULID
    name: Mapped[str] = mapped_column(String)
    registry: Mapped[str | None] = mapped_column(String, default=None)
    tags: Mapped[dict] = mapped_column(JSONB, default=dict)
    kind: Mapped[str] = mapped_column(String, default="container")       # container|iso|template
    import_status: Mapped[str | None] = mapped_column(String, default=None)  # pulling|ready|failed
    public: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))  # whether the session wizard offers this image
    # null owner = shared catalog entry; set = a user-built private image (owner + admins only)
    owner_user_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    # The same public ref may be imported by many members, so uniqueness is per owner. Postgres
    # treats NULL owners as distinct rows, so shared catalogue refs need the second, partial index
    # to stay unique among themselves.
    __table_args__ = (
        Index("uq_image_registry_owner", "registry", "owner_user_id", unique=True),
        Index(
            "uq_image_registry_shared", "registry", unique=True,
            postgresql_where=text("owner_user_id IS NULL"),
            sqlite_where=text("owner_user_id IS NULL"),
        ),
    )


class ImageBuild(Base, TimestampMixin):
    """A console-triggered container build.

    The control plane records desired state and applies a GShareImageBuild CR; the operator runs a
    kaniko Job and reports phase/log back via /internal/image-builds/{id}/status. On success an
    Image row is created owned by the builder (visible only to them + admins)."""

    __tablename__ = "image_build"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # bld_ULID
    group_id: Mapped[str] = mapped_column(ForeignKey("group.id"), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    name: Mapped[str | None] = mapped_column(String, default=None)       # image name (repo leaf)
    source: Mapped[str] = mapped_column(String)                          # dockerfile|git
    dockerfile: Mapped[str | None] = mapped_column(Text, default=None)
    git_url: Mapped[str | None] = mapped_column(String, default=None)
    git_ref: Mapped[str | None] = mapped_column(String, default=None)
    context: Mapped[str | None] = mapped_column(String, default=None)
    build_args: Mapped[dict | None] = mapped_column(JSONB, default=None)
    target_tag: Mapped[str | None] = mapped_column(String, default=None)
    cluster_id: Mapped[str | None] = mapped_column(String, default=None)
    status: Mapped[str] = mapped_column(String, default="pending")       # queued|running|succeeded|failed|cancelled
    error: Mapped[str | None] = mapped_column(String, default=None)
    log_tail: Mapped[str | None] = mapped_column(Text, default=None)     # last ~16KB of kaniko log
    image_ref: Mapped[str | None] = mapped_column(String, default=None)  # pushed ref on success
    image_id: Mapped[str | None] = mapped_column(String, default=None)   # created Image row
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


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
    # Real heartbeat: stamped on EVERY operator inventory report, even when nothing changed —
    # updated_at only moves on actual column changes and reads as a fake heartbeat.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    cpu: Mapped[int | None] = mapped_column(Integer, default=None)        # node CPU cores
    mem: Mapped[int | None] = mapped_column(Integer, default=None)        # node RAM in GiB
    disk: Mapped[int | None] = mapped_column(Integer, default=None)       # node ephemeral storage in GiB
    role: Mapped[str | None] = mapped_column(String, default=None)       # master|gpu|cpu|storage (operator-derived)
    region: Mapped[str | None] = mapped_column(String, default=None)
    # Whether the lossless-pause prerequisites (cuda-checkpoint plus CRIU) are labelled ready on the
    # node; reported by the operator's inventory controller.
    lossless_capable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Node pool membership. NULL behaves as "shared": usable by every tenant. See NodePool.
    pool_id: Mapped[str | None] = mapped_column(
        ForeignKey("node_pool.id", ondelete="SET NULL"), index=True, default=None
    )


class NodePool(Base, TimestampMixin):
    """A named set of nodes in one cluster.

    kind "shared": usable by everyone (grants are accepted but meaningless). kind "dedicated":
    usable ONLY by tenants holding a NodePoolGrant. Placement picks group-granted pools first,
    then org-granted, then shared (see app.domain.node_pools). CPU-only sessions ignore pools.
    """
    __tablename__ = "node_pool"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # npl_ULID
    cluster_id: Mapped[str] = mapped_column(ForeignKey("cluster.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String, default=None)
    kind: Mapped[str] = mapped_column(String, default="dedicated")       # shared|dedicated
    __table_args__ = (UniqueConstraint("cluster_id", "name", name="uq_node_pool_cluster_name"),)


class NodePoolGrant(Base, TimestampMixin):
    """Grants a tenant (organization or group) the use of a pool's nodes."""
    __tablename__ = "node_pool_grant"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # pgr_ULID
    pool_id: Mapped[str] = mapped_column(
        ForeignKey("node_pool.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String)                           # org|group
    scope_id: Mapped[str] = mapped_column(String)                        # organization.id | group.id
    created_by: Mapped[str | None] = mapped_column(String, default=None)
    __table_args__ = (
        UniqueConstraint("pool_id", "scope", "scope_id", name="uq_node_pool_grant_scope"),
    )


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
    # Current per-CARD pool mode: fractional|exclusive|mig. The hami-core↔mig axis is observed
    # from HAMi's per-card register annotation; within hami-core, fractional vs exclusive is
    # GShare policy carried by desired_mode. (Historically this was a node-label echo.)
    mode: Mapped[str | None] = mapped_column(String, default=None)
    # Admin-set target pool for this card. NULL follows the observed mode. Applied through the
    # drain state machine — never flipped under running sessions.
    desired_mode: Mapped[str | None] = mapped_column(String, default=None)
    # Drain state machine: ready (placeable) | draining (no new placements; waiting for the last
    # allocation to end) | applying (mode change in progress on the node) | error.
    mode_state: Mapped[str] = mapped_column(String, default="ready", server_default=text("'ready'"))
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
        Index("ix_dev_cluster_status_mode", "cluster_id", "status", "mode"),
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
    # The k8s node the pod actually landed on, reported by the operator on Running. The only
    # placement record for CPU sessions (no bound GPU); for GPU sessions it matches the bound
    # device's node.
    node_hostname: Mapped[str | None] = mapped_column(String, default=None)
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
    # When the status last changed — stamped by the ORM listener at the end of this module on
    # every transition, so the admin monitor can show "when did it error / pause / start".
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # WHY the session paused or ended, surfaced to the owner:
    # idle | credit_exhausted | admin_stopped | user_stopped | max_runtime | error
    status_reason: Mapped[str | None] = mapped_column(String, default=None)
    # snapshot of unit price at admission so price changes don't re-bill
    credit_per_hour_snapshot: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    device_total_mem_mb: Mapped[int | None] = mapped_column(Integer, default=None)
    pod_ref: Mapped[str | None] = mapped_column(String, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Backs the per-user caps and queue-fairness counts (always filtered by owner + status).
    __table_args__ = (Index("ix_sessions_owner_status", "owner_user_id", "status"),)


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


class SessionEvent(Base, TimestampMixin):
    """Append-only lifecycle log per session: what happened, when, and why.

    Written wherever the session transitions (scheduler admission, service actions, operator
    callbacks); the detail screen renders it as a timeline so error states are readable without
    a separate message box."""

    __tablename__ = "session_event"
    id: Mapped[str] = mapped_column(String, primary_key=True)             # sev_ULID
    session_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)    # created|queued|promoted|preparing|running|paused|resumed|terminated|error
    reason: Mapped[str | None] = mapped_column(String, default=None)      # status_reason vocabulary
    message: Mapped[str | None] = mapped_column(String, default=None)     # raw operator text (OOM, evicted, ...)


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


class WebhookDelivery(Base, TimestampMixin):
    """Outbox row for one webhook delivery attempt chain.

    Emitters insert a row in the same transaction as the domain change; the webhook_dispatcher
    worker POSTs it with retries and marks the outcome. This is what makes webhook subscriptions
    real — rows used to be stored with no dispatcher at all.
    """
    __tablename__ = "webhook_delivery"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # wbd_ULID
    subscription_id: Mapped[str] = mapped_column(ForeignKey("webhook_subscription.id"), index=True)
    event: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, index=True)
    status: Mapped[str] = mapped_column(String, default="pending")       # pending|delivered|failed
    last_error: Mapped[str | None] = mapped_column(String, default=None)


class Notice(Base, TimestampMixin, SoftDeleteMixin):
    """An announcement. scope=global (super_admin, everyone sees it) or scope=group
    (group_admin; visible to that group's members and super_admin only)."""
    __tablename__ = "notice"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # ntc_ULID
    scope: Mapped[str] = mapped_column(String, default="global")         # global|group
    group_id: Mapped[str | None] = mapped_column(ForeignKey("group.id"), index=True, default=None)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Inquiry(Base, TimestampMixin, SoftDeleteMixin):
    """A user question to the operators. Visible to its author, super_admin, and the
    group_admins of the author's group (captured at creation)."""
    __tablename__ = "inquiry"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # inq_ULID
    author_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey("group.id"), index=True, default=None)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="open", index=True)  # open|answered|closed


class InquiryReply(Base, TimestampMixin):
    __tablename__ = "inquiry_reply"
    id: Mapped[str] = mapped_column(String, primary_key=True)            # irp_ULID
    inquiry_id: Mapped[str] = mapped_column(ForeignKey("inquiry.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    body: Mapped[str] = mapped_column(Text)


class SystemSetting(Base, TimestampMixin):
    """One system-wide setting per row. Small, typed-by-convention key-value store —
    currently: credit_refill_day ("1".."28") and credit_refill_hour ("0".."23", KST)."""
    __tablename__ = "system_setting"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


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


@event.listens_for(Session.status, "set", propagate=True)
def _stamp_session_status_change(target: Session, value: str, oldvalue: object, _initiator: object) -> None:
    """Stamp status_changed_at on every real status transition (21 call sites, one listener)."""
    if value != oldvalue:
        target.status_changed_at = datetime.now(UTC)
