"""storage_volume: add a user-chosen name and include it in the partial unique index

Widening the partial unique index from (scope, scope_id, type) to (scope, scope_id, type, name) lets
one scope hold several volumes of the same type as long as their names differ.

Revision ID: 0015_storage_volume_name
Revises: 0014_cluster_name_partial_unique
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_storage_volume_name"
down_revision: Union[str, None] = "0014_cluster_name_partial_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("storage_volume", sa.Column("name", sa.String(), nullable=True))
    op.drop_index("uq_storage_volume_scope_active", table_name="storage_volume")
    op.create_index(
        "uq_storage_volume_scope_active",
        "storage_volume",
        ["scope", "scope_id", "type", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_storage_volume_scope_active", table_name="storage_volume")
    op.create_index(
        "uq_storage_volume_scope_active",
        "storage_volume",
        ["scope", "scope_id", "type"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_column("storage_volume", "name")
