"""Session lifecycle event log (timeline on the session detail screen).

Revision ID: 0036_session_event
Revises: 0035_node_pools
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_session_event"
down_revision = "0035_node_pools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_event",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_session_event_session_id", "session_event", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_session_event_session_id", table_name="session_event")
    op.drop_table("session_event")
