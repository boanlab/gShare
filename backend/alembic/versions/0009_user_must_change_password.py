"""user.must_change_password: force a password change at first login

Revision ID: 0009_user_must_change_password
Revises: 0008_rename_project_to_group
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_user_must_change_password"
down_revision: Union[str, None] = "0008_rename_project_to_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("user", "must_change_password")
