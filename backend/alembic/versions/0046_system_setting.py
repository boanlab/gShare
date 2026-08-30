"""system_setting key-value table (refill schedule).

Revision ID: 0046_system_setting
Revises: 0045_session_node_hostname
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046_system_setting"
down_revision = "0045_session_node_hostname"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_setting",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("system_setting")
