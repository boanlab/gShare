"""offering: add a status column (active or inactive)

Actually persists whether an offering is active; without the column everything was implicitly active.
The session wizard hides inactive offerings.

Revision ID: 0019_offering_status
Revises: 0018_preset_kind_split
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_offering_status"
down_revision: Union[str, None] = "0018_preset_kind_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offering", sa.Column("status", sa.String(), nullable=False, server_default="active"))


def downgrade() -> None:
    op.drop_column("offering", "status")
