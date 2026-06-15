"""session.priority: preemption priority, higher wins, default 0.

Revision ID: 0027_session_priority
Revises: 0026_gpu_lending
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_session_priority"
down_revision: Union[str, None] = "0026_gpu_lending"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session",
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("session", "priority")
