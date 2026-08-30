"""notification.deleted_at — dismissals become soft deletes (my-page keeps the log).

Revision ID: 0049_notification_deleted_at
Revises: 0048_board_timestamps
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_notification_deleted_at"
down_revision = "0048_board_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("notification", "deleted_at")
