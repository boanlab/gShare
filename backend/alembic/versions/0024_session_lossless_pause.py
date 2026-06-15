"""session.lossless_pause: mark a session eligible for lossless pause

Phase 2 of lossless pause. Snapshots the creation-time gate — offering.lossless_pause and a
lossless-capable node in the cluster — onto the session. It is passed through as spec.losslessPause,
so the operator attempts a checkpoint on pause and falls back to cold when it cannot. Existing rows
are false.

Revision ID: 0024_session_lossless_pause
Revises: 0023_lossless_prereqs
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_session_lossless_pause"
down_revision: Union[str, None] = "0023_lossless_prereqs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session",
        sa.Column("lossless_pause", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("session", "lossless_pause")
