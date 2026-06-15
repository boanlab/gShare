"""storage_volume: make the scope/type uniqueness partial, excluding soft-deleted rows

A soft-deleted volume used to block recreating one with the same scope, scope_id, and type.

Revision ID: 0003_volume_partial_unique
Revises: 0002_session_name
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_volume_partial_unique"
down_revision: Union[str, None] = "0002_session_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_storage_volume_scope", "storage_volume", type_="unique")
    op.create_index(
        "uq_storage_volume_scope_active",
        "storage_volume",
        ["scope", "scope_id", "type"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_storage_volume_scope_active", table_name="storage_volume")
    op.create_unique_constraint(
        "uq_storage_volume_scope", "storage_volume", ["scope", "scope_id", "type"]
    )
