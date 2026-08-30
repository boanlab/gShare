"""session.status_reason: WHY a session paused or ended, surfaced to the owner

idle | credit_exhausted | admin_stopped | user_stopped | max_runtime | error — set by the state
transition that caused it (grace enforcer, operator callback mapping, stop/terminate endpoints).
Also adds the (owner_user_id, status) index backing the per-user caps and queue-fairness counts.

Revision ID: 0030_session_status_reason
Revises: 0029_alloc_kind_resident_spot
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_session_status_reason"
down_revision: Union[str, None] = "0029_alloc_kind_resident_spot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session", sa.Column("status_reason", sa.String(), nullable=True))
    op.create_index("ix_sessions_owner_status", "session", ["owner_user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_sessions_owner_status", table_name="session")
    op.drop_column("session", "status_reason")
