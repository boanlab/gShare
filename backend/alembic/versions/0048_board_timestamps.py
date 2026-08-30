"""server_default now() for notice/inquiry timestamps + backfill (rows inserted NULL).

Revision ID: 0048_board_timestamps
Revises: 0047_notice_inquiry
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_board_timestamps"
down_revision = "0047_notice_inquiry"
branch_labels = None
depends_on = None

_TABLES = ("notice", "inquiry", "inquiry_reply", "system_setting")


def upgrade() -> None:
    for t in _TABLES:
        for col in ("created_at", "updated_at"):
            op.alter_column(t, col, server_default=sa.text("now()"))
        op.execute(f"UPDATE \"{t}\" SET created_at = now() WHERE created_at IS NULL")
        op.execute(f"UPDATE \"{t}\" SET updated_at = COALESCE(updated_at, created_at)")


def downgrade() -> None:
    for t in _TABLES:
        for col in ("created_at", "updated_at"):
            op.alter_column(t, col, server_default=None)
