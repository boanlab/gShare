"""gpu_node.last_seen_at — real operator heartbeat (updated_at only moves on changes).

Revision ID: 0037_node_last_seen
Revises: 0036_session_event
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_node_last_seen"
down_revision = "0036_session_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gpu_node", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("gpu_node", "last_seen_at")
