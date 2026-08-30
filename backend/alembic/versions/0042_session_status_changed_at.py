"""Stamp when a session's status last changed, for the admin monitor's last-action column.

The ORM sets it on every transition; existing rows are backfilled with the best timestamp the row
already carries — terminated_at for finished sessions, started_at for running ones, otherwise the
row's updated_at/created_at.

Revision ID: 0042_session_status_changed_at
Revises: 0041_drop_volume_quota_request
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0042_session_status_changed_at"
down_revision = "0041_drop_volume_quota_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session", sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE session SET status_changed_at = COALESCE(terminated_at, started_at, updated_at, created_at)"
    )


def downgrade() -> None:
    op.drop_column("session", "status_changed_at")
