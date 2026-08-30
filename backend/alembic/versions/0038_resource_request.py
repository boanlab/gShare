"""Per-user compute quota requests (approve -> user-scope policy upsert).

Revision ID: 0038_resource_request
Revises: 0037_node_last_seen
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038_resource_request"
down_revision = "0037_node_last_seen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resource_request",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=True),
        sa.Column("cpu", sa.Integer(), nullable=True),
        sa.Column("mem_gb", sa.Integer(), nullable=True),
        sa.Column("storage_gb", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_resource_request_user_id", "resource_request", ["user_id"])
    op.create_index("ix_resource_request_group_id", "resource_request", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_resource_request_group_id", table_name="resource_request")
    op.drop_index("ix_resource_request_user_id", table_name="resource_request")
    op.drop_table("resource_request")
