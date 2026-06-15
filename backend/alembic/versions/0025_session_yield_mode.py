"""session.pause_mode('cold'|'yield') + session.preemptible: in-place GPU yield.

Revision ID: 0025_session_yield_mode
Revises: 0024_session_lossless_pause
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_session_yield_mode"
down_revision: Union[str, None] = "0024_session_lossless_pause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session",
        sa.Column("pause_mode", sa.String(), nullable=False, server_default=sa.text("'cold'")),
    )
    op.add_column(
        "session",
        sa.Column("preemptible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("session", "preemptible")
    op.drop_column("session", "pause_mode")
