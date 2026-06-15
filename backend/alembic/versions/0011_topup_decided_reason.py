"""add topup_request.decided_reason, persisting the reason for a decision

Stores the reason an administrator gave when approving or rejecting a top-up.

Revision ID: 0011_topup_decided_reason
Revises: 0010_project_to_group
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_topup_decided_reason"
down_revision: Union[str, None] = "0010_project_to_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("topup_request", sa.Column("decided_reason", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("topup_request", "decided_reason")
