"""add session.name (user-facing display name)

Revision ID: 0002_session_name
Revises: 0001_initial
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_session_name"
down_revision: Union[str, None] = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session", sa.Column("name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("session", "name")
