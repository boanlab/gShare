"""Drop the volume quota-request outbox: quota changes are self-service now.

Expansion used to be a request an administrator approved, but no console screen ever surfaced the
approvals, so requests sat pending forever. The owner now sets the quota directly (PATCH
/storage/volumes/{id}), in both directions, and storage bills max(quota, used) — so the approval
table has nothing left to hold.

Revision ID: 0041_drop_volume_quota_request
Revises: 0040_image_registry_per_owner
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0041_drop_volume_quota_request"
down_revision = "0040_image_registry_per_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("volume_quota_request")


def downgrade() -> None:
    op.create_table(
        "volume_quota_request",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("volume_id", sa.String(), sa.ForeignKey("storage_volume.id"), nullable=False),
        sa.Column("requested_gb", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("requester_id", sa.String(), nullable=False),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_volume_quota_request_volume_id", "volume_quota_request", ["volume_id"])
