"""Node pools: dedicate nodes to organizations / groups.

- node_pool: a named set of nodes in one cluster, kind shared|dedicated.
- node_pool_grant: (pool, org|group) grants; dedicated pools are usable only by grantees.
- gpu_node.pool_id: membership (NULL = shared).

Revision ID: 0035_node_pools
Revises: 0034_welcome_credit
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_node_pools"
down_revision = "0034_welcome_credit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_pool",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("cluster_id", sa.String(), sa.ForeignKey("cluster.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False, server_default="dedicated"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("cluster_id", "name", name="uq_node_pool_cluster_name"),
    )
    op.create_index("ix_node_pool_cluster_id", "node_pool", ["cluster_id"])

    op.create_table(
        "node_pool_grant",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "pool_id", sa.String(), sa.ForeignKey("node_pool.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("pool_id", "scope", "scope_id", name="uq_node_pool_grant_scope"),
    )
    op.create_index("ix_node_pool_grant_pool_id", "node_pool_grant", ["pool_id"])

    op.add_column("gpu_node", sa.Column("pool_id", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_gpu_node_pool_id", "gpu_node", "node_pool", ["pool_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_gpu_node_pool_id", "gpu_node", ["pool_id"])


def downgrade() -> None:
    op.drop_index("ix_gpu_node_pool_id", table_name="gpu_node")
    op.drop_constraint("fk_gpu_node_pool_id", "gpu_node", type_="foreignkey")
    op.drop_column("gpu_node", "pool_id")
    op.drop_index("ix_node_pool_grant_pool_id", table_name="node_pool_grant")
    op.drop_table("node_pool_grant")
    op.drop_index("ix_node_pool_cluster_id", table_name="node_pool")
    op.drop_table("node_pool")
