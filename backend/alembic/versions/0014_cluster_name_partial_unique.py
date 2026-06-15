"""cluster.name: make uniqueness partial, excluding soft-deleted rows

A soft-deleted cluster used to block re-registering one under the same name.

Revision ID: 0014_cluster_name_partial_unique
Revises: 0013_gpu_node_disk
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_cluster_name_partial_unique"
down_revision: Union[str, None] = "0013_gpu_node_disk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_cluster_name", "cluster", type_="unique")
    op.create_index(
        "uq_cluster_name_active",
        "cluster",
        ["name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_cluster_name_active", table_name="cluster")
    op.create_unique_constraint("uq_cluster_name", "cluster", ["name"])
