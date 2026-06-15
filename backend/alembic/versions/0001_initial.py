"""init schema (core entities + Cluster) — hand-authored.

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-06

Hand-written DDL mirroring ``app.db.models``. Table order is parents-before-children so
FKs resolve; downgrade drops in exact reverse. Constraint/index names follow the
NAMING_CONVENTION in ``app.db.base`` (pk_/fk_/uq_/ck_/ix_).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Identity / tenancy ──
    op.create_table(
        "user",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("global_role", sa.String(), nullable=True),
        sa.Column("password_hash", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user"),
        sa.UniqueConstraint("email", name="uq_user_email"),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=False)

    op.create_table(
        "organization",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_organization"),
        sa.UniqueConstraint("name", name="uq_organization_name"),
    )

    op.create_table(
        "project",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("org_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], name="fk_project_org_id_organization"),
        sa.PrimaryKeyConstraint("id", name="pk_project"),
    )
    op.create_index("ix_project_org_id", "project", ["org_id"], unique=False)

    op.create_table(
        "membership",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_membership_user_id_user"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_membership_project_id_project"),
        sa.PrimaryKeyConstraint("id", name="pk_membership"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_membership_user_id"),
    )
    op.create_index("ix_membership_user_id", "membership", ["user_id"], unique=False)
    op.create_index("ix_membership_project_id", "membership", ["project_id"], unique=False)

    op.create_table(
        "notification",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_notification_user_id_user"),
        sa.PrimaryKeyConstraint("id", name="pk_notification"),
    )
    op.create_index("ix_notification_user_id", "notification", ["user_id"], unique=False)

    # ── Credit ──
    op.create_table(
        "credit_wallet",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_type", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("balance", sa.Numeric(18, 2), nullable=False),
        sa.Column("reserved", sa.Numeric(18, 2), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "balance >= 0 AND reserved >= 0 AND reserved <= balance", name="ck_credit_wallet_wallet_sigma"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_credit_wallet"),
        sa.UniqueConstraint("owner_type", "owner_id", name="uq_credit_wallet_owner_type"),
    )

    op.create_table(
        "credit_transaction",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("wallet_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 2), nullable=False),
        sa.Column("ref", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["wallet_id"], ["credit_wallet.id"], name="fk_credit_transaction_wallet_id_credit_wallet"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_credit_transaction"),
        sa.UniqueConstraint("idempotency_key", name="uq_credit_transaction_idempotency_key"),
    )
    op.create_index("ix_credit_transaction_wallet_id", "credit_transaction", ["wallet_id"], unique=False)
    op.create_index("ix_credit_transaction_ref", "credit_transaction", ["ref"], unique=False)
    op.create_index("ix_txn_wallet_created", "credit_transaction", ["wallet_id", "created_at"], unique=False)

    op.create_table(
        "topup_request",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("wallet_id", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("requester_id", sa.String(), nullable=False),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["wallet_id"], ["credit_wallet.id"], name="fk_topup_request_wallet_id_credit_wallet"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_topup_request"),
    )
    op.create_index("ix_topup_request_wallet_id", "topup_request", ["wallet_id"], unique=False)

    # ── Catalog ──
    op.create_table(
        "offering",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("resource_class", sa.String(), nullable=False),
        sa.Column("gpu_model", sa.String(), nullable=True),
        sa.Column("gpu_mem_mb", sa.Integer(), nullable=True),
        sa.Column("gpu_cores", sa.Integer(), nullable=True),
        sa.Column("cpu", sa.Integer(), nullable=True),
        sa.Column("mem_gb", sa.Integer(), nullable=True),
        sa.Column("credit_per_hour", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_offering"),
    )

    op.create_table(
        "offering_price_history",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("offering_id", sa.String(), nullable=False),
        sa.Column("credit_per_hour", sa.Numeric(18, 2), nullable=False),
        sa.Column("changed_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["offering_id"], ["offering.id"], name="fk_offering_price_history_offering_id_offering"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_offering_price_history"),
    )
    op.create_index(
        "ix_offering_price_history_offering_id", "offering_price_history", ["offering_id"], unique=False
    )

    op.create_table(
        "resource_preset",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("cpu", sa.Integer(), nullable=True),
        sa.Column("mem", sa.Integer(), nullable=True),
        sa.Column("gpu_mem_mb", sa.Integer(), nullable=True),
        sa.Column("gpu_cores", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_resource_preset"),
    )

    op.create_table(
        "resource_policy",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("max_concurrent", sa.Integer(), nullable=True),
        sa.Column("max_runtime", sa.Integer(), nullable=True),
        sa.Column("idle_timeout", sa.Integer(), nullable=True),
        sa.Column("max_queued", sa.Integer(), nullable=True),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_resource_policy"),
    )
    op.create_index("ix_resource_policy_scope_id", "resource_policy", ["scope_id"], unique=False)

    op.create_table(
        "image",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("registry", sa.String(), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("import_status", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_image"),
    )

    op.create_table(
        "image_build",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("image_ref", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_image_build_project_id_project"),
        sa.PrimaryKeyConstraint("id", name="pk_image_build"),
    )
    op.create_index("ix_image_build_project_id", "image_build", ["project_id"], unique=False)

    # ── Clusters / nodes / devices ──
    op.create_table(
        "cluster",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("api_server", sa.String(), nullable=False),
        sa.Column("runtime", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("kubeconfig_secret_ref", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_cluster"),
        sa.UniqueConstraint("name", name="uq_cluster_name"),
    )

    op.create_table(
        "gpu_node",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("cluster_id", sa.String(), nullable=False),
        sa.Column("hostname", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("cpu", sa.Integer(), nullable=True),
        sa.Column("mem", sa.Integer(), nullable=True),
        sa.Column("region", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["cluster.id"], name="fk_gpu_node_cluster_id_cluster"),
        sa.PrimaryKeyConstraint("id", name="pk_gpu_node"),
    )
    op.create_index("ix_gpu_node_cluster_id", "gpu_node", ["cluster_id"], unique=False)

    op.create_table(
        "gpu_device",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("cluster_id", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("gpu_uuid", sa.String(), nullable=False),
        sa.Column("total_mem_mb", sa.Integer(), nullable=False),
        sa.Column("used_mem_mb", sa.Integer(), nullable=False),
        sa.Column("total_cores", sa.Integer(), nullable=False),
        sa.Column("used_cores", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "used_mem_mb <= total_mem_mb AND used_cores <= total_cores", name="ck_gpu_device_no_overcommit"
        ),
        sa.ForeignKeyConstraint(["node_id"], ["gpu_node.id"], name="fk_gpu_device_node_id_gpu_node"),
        sa.ForeignKeyConstraint(["cluster_id"], ["cluster.id"], name="fk_gpu_device_cluster_id_cluster"),
        sa.PrimaryKeyConstraint("id", name="pk_gpu_device"),
        sa.UniqueConstraint("gpu_uuid", name="uq_gpu_device_gpu_uuid"),
    )
    op.create_index("ix_gpu_device_node_id", "gpu_device", ["node_id"], unique=False)
    op.create_index("ix_gpu_device_cluster_id", "gpu_device", ["cluster_id"], unique=False)
    op.create_index("ix_dev_node_status_mode", "gpu_device", ["node_id", "status", "mode"], unique=False)

    op.create_table(
        "node_health_event",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["gpu_node.id"], name="fk_node_health_event_node_id_gpu_node"),
        sa.PrimaryKeyConstraint("id", name="pk_node_health_event"),
    )
    op.create_index("ix_node_health_event_node_id", "node_health_event", ["node_id"], unique=False)

    # ── Sessions / allocation / queue ──
    op.create_table(
        "session",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("cluster_id", sa.String(), nullable=False),
        sa.Column("cluster_mode", sa.String(), nullable=False),
        sa.Column("offering_id", sa.String(), nullable=False),
        sa.Column("image_id", sa.String(), nullable=False),
        sa.Column("resource_class", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("gpu_mem_mb", sa.Integer(), nullable=True),
        sa.Column("gpu_cores", sa.Integer(), nullable=True),
        sa.Column("bound_gpu_uuid", sa.String(), nullable=True),
        sa.Column("billing_wallet_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("credit_per_hour_snapshot", sa.Numeric(18, 2), nullable=False),
        sa.Column("device_total_mem_mb", sa.Integer(), nullable=True),
        sa.Column("pod_ref", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["user.id"], name="fk_session_owner_user_id_user"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_session_project_id_project"),
        sa.ForeignKeyConstraint(["cluster_id"], ["cluster.id"], name="fk_session_cluster_id_cluster"),
        sa.ForeignKeyConstraint(["offering_id"], ["offering.id"], name="fk_session_offering_id_offering"),
        sa.ForeignKeyConstraint(["image_id"], ["image.id"], name="fk_session_image_id_image"),
        sa.ForeignKeyConstraint(
            ["billing_wallet_id"], ["credit_wallet.id"], name="fk_session_billing_wallet_id_credit_wallet"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session"),
    )
    op.create_index("ix_session_owner_user_id", "session", ["owner_user_id"], unique=False)
    op.create_index("ix_session_project_id", "session", ["project_id"], unique=False)
    op.create_index("ix_session_cluster_id", "session", ["cluster_id"], unique=False)
    op.create_index("ix_session_status", "session", ["status"], unique=False)

    op.create_table(
        "allocation",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=True),
        sa.Column("gpu_uuid", sa.String(), nullable=True),
        sa.Column("gpu_mem_mb", sa.Integer(), nullable=True),
        sa.Column("gpu_cores", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"], name="fk_allocation_session_id_session"),
        sa.ForeignKeyConstraint(["device_id"], ["gpu_device.id"], name="fk_allocation_device_id_gpu_device"),
        sa.PrimaryKeyConstraint("id", name="pk_allocation"),
    )
    op.create_index("ix_allocation_session_id", "allocation", ["session_id"], unique=False)
    # One live allocation per session/device: partial UNIQUE WHERE ended_at IS NULL
    op.create_index(
        "uq_alloc_session_live", "allocation", ["session_id"], unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "uq_alloc_device_live", "allocation", ["device_id"], unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "queue_entry",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("session_req", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"], name="fk_queue_entry_session_id_session"),
        sa.PrimaryKeyConstraint("id", name="pk_queue_entry"),
        sa.UniqueConstraint("session_id", name="uq_queue_entry_session_id"),
    )
    op.create_index("ix_queue_priority_enqueued", "queue_entry", ["priority", "enqueued_at"], unique=False)

    op.create_table(
        "session_checkpoint",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("storage_ref", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["session.id"], name="fk_session_checkpoint_session_id_session"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session_checkpoint"),
    )
    op.create_index("ix_session_checkpoint_session_id", "session_checkpoint", ["session_id"], unique=False)

    op.create_table(
        "connection_token",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["session.id"], name="fk_connection_token_session_id_session"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_connection_token"),
    )
    op.create_index("ix_connection_token_session_id", "connection_token", ["session_id"], unique=False)

    # ── Storage ──
    op.create_table(
        "storage_volume",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("access_mode", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=True),
        sa.Column("host", sa.String(), nullable=True),
        sa.Column("quota_gb", sa.Integer(), nullable=False),
        sa.Column("used_gb", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_storage_volume"),
        sa.UniqueConstraint("scope", "scope_id", "type", name="uq_storage_volume_scope"),
    )
    op.create_index("ix_storage_volume_scope_id", "storage_volume", ["scope_id"], unique=False)

    op.create_table(
        "storage_folder",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("volume_id", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["volume_id"], ["storage_volume.id"], name="fk_storage_folder_volume_id_storage_volume"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_storage_folder"),
    )
    op.create_index("ix_storage_folder_volume_id", "storage_folder", ["volume_id"], unique=False)

    op.create_table(
        "volume_mount",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("volume_id", sa.String(), nullable=False),
        sa.Column("mount_path", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"], name="fk_volume_mount_session_id_session"),
        sa.ForeignKeyConstraint(
            ["volume_id"], ["storage_volume.id"], name="fk_volume_mount_volume_id_storage_volume"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_volume_mount"),
    )
    op.create_index("ix_volume_mount_session_id", "volume_mount", ["session_id"], unique=False)
    op.create_index("ix_volume_mount_volume_id", "volume_mount", ["volume_id"], unique=False)

    op.create_table(
        "volume_permission",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("volume_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["volume_id"], ["storage_volume.id"], name="fk_volume_permission_volume_id_storage_volume"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_volume_permission_user_id_user"),
        sa.PrimaryKeyConstraint("id", name="pk_volume_permission"),
        sa.UniqueConstraint("volume_id", "user_id", name="uq_volume_permission_volume_id"),
    )
    op.create_index("ix_volume_permission_volume_id", "volume_permission", ["volume_id"], unique=False)
    op.create_index("ix_volume_permission_user_id", "volume_permission", ["user_id"], unique=False)

    op.create_table(
        "volume_quota_request",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("volume_id", sa.String(), nullable=False),
        sa.Column("requested_gb", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("requester_id", sa.String(), nullable=False),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["volume_id"], ["storage_volume.id"], name="fk_volume_quota_request_volume_id_storage_volume"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_volume_quota_request"),
    )
    op.create_index(
        "ix_volume_quota_request_volume_id", "volume_quota_request", ["volume_id"], unique=False
    )

    op.create_table(
        "volume_snapshot",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("volume_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["volume_id"], ["storage_volume.id"], name="fk_volume_snapshot_volume_id_storage_volume"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_volume_snapshot"),
    )
    op.create_index("ix_volume_snapshot_volume_id", "volume_snapshot", ["volume_id"], unique=False)

    # ── Budgets / FinOps ──
    op.create_table(
        "budget",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column("limit_credit", sa.Numeric(18, 2), nullable=False),
        sa.Column("spent_credit", sa.Numeric(18, 2), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_budget"),
        sa.UniqueConstraint("scope", "scope_id", "period_start", name="uq_budget_scope"),
    )
    op.create_index("ix_budget_scope_id", "budget", ["scope_id"], unique=False)

    op.create_table(
        "budget_alert",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("budget_id", sa.String(), nullable=False),
        sa.Column("threshold_pct", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["budget_id"], ["budget.id"], name="fk_budget_alert_budget_id_budget"),
        sa.PrimaryKeyConstraint("id", name="pk_budget_alert"),
    )
    op.create_index("ix_budget_alert_budget_id", "budget_alert", ["budget_id"], unique=False)

    # ── Webhooks / audit ──
    op.create_table(
        "webhook_subscription",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("events", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secret", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_subscription"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("prev_hash", sa.String(), nullable=True),
        sa.Column("entry_hash", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index("ix_audit_log_actor", "audit_log", ["actor"], unique=False)
    op.create_index("ix_audit_log_action", "audit_log", ["action"], unique=False)


def downgrade() -> None:
    # Reverse order of upgrade(): children before parents.
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_actor", table_name="audit_log")
    op.drop_table("audit_log")


    op.drop_table("webhook_subscription")

    op.drop_index("ix_budget_alert_budget_id", table_name="budget_alert")
    op.drop_table("budget_alert")

    op.drop_index("ix_budget_scope_id", table_name="budget")
    op.drop_table("budget")

    op.drop_index("ix_volume_snapshot_volume_id", table_name="volume_snapshot")
    op.drop_table("volume_snapshot")

    op.drop_index("ix_volume_quota_request_volume_id", table_name="volume_quota_request")
    op.drop_table("volume_quota_request")

    op.drop_index("ix_volume_permission_user_id", table_name="volume_permission")
    op.drop_index("ix_volume_permission_volume_id", table_name="volume_permission")
    op.drop_table("volume_permission")

    op.drop_index("ix_volume_mount_volume_id", table_name="volume_mount")
    op.drop_index("ix_volume_mount_session_id", table_name="volume_mount")
    op.drop_table("volume_mount")

    op.drop_index("ix_storage_folder_volume_id", table_name="storage_folder")
    op.drop_table("storage_folder")

    op.drop_index("ix_storage_volume_scope_id", table_name="storage_volume")
    op.drop_table("storage_volume")

    op.drop_index("ix_connection_token_session_id", table_name="connection_token")
    op.drop_table("connection_token")

    op.drop_index("ix_session_checkpoint_session_id", table_name="session_checkpoint")
    op.drop_table("session_checkpoint")

    op.drop_index("ix_queue_priority_enqueued", table_name="queue_entry")
    op.drop_table("queue_entry")

    op.drop_index("uq_alloc_device_live", table_name="allocation")
    op.drop_index("uq_alloc_session_live", table_name="allocation")
    op.drop_index("ix_allocation_session_id", table_name="allocation")
    op.drop_table("allocation")

    op.drop_index("ix_session_status", table_name="session")
    op.drop_index("ix_session_cluster_id", table_name="session")
    op.drop_index("ix_session_project_id", table_name="session")
    op.drop_index("ix_session_owner_user_id", table_name="session")
    op.drop_table("session")

    op.drop_index("ix_node_health_event_node_id", table_name="node_health_event")
    op.drop_table("node_health_event")

    op.drop_index("ix_dev_node_status_mode", table_name="gpu_device")
    op.drop_index("ix_gpu_device_cluster_id", table_name="gpu_device")
    op.drop_index("ix_gpu_device_node_id", table_name="gpu_device")
    op.drop_table("gpu_device")

    op.drop_index("ix_gpu_node_cluster_id", table_name="gpu_node")
    op.drop_table("gpu_node")

    op.drop_table("cluster")

    op.drop_index("ix_image_build_project_id", table_name="image_build")
    op.drop_table("image_build")

    op.drop_table("image")

    op.drop_index("ix_resource_policy_scope_id", table_name="resource_policy")
    op.drop_table("resource_policy")

    op.drop_table("resource_preset")

    op.drop_index("ix_offering_price_history_offering_id", table_name="offering_price_history")
    op.drop_table("offering_price_history")

    op.drop_table("offering")

    op.drop_index("ix_topup_request_wallet_id", table_name="topup_request")
    op.drop_table("topup_request")

    op.drop_index("ix_txn_wallet_created", table_name="credit_transaction")
    op.drop_index("ix_credit_transaction_ref", table_name="credit_transaction")
    op.drop_index("ix_credit_transaction_wallet_id", table_name="credit_transaction")
    op.drop_table("credit_transaction")

    op.drop_table("credit_wallet")

    op.drop_index("ix_notification_user_id", table_name="notification")
    op.drop_table("notification")

    op.drop_index("ix_membership_project_id", table_name="membership")
    op.drop_index("ix_membership_user_id", table_name="membership")
    op.drop_table("membership")

    op.drop_index("ix_project_org_id", table_name="project")
    op.drop_table("project")

    op.drop_table("organization")

    op.drop_index("ix_user_email", table_name="user")
    op.drop_table("user")
