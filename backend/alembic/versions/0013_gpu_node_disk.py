"""add gpu_node.disk (node ephemeral-storage GiB) for capacity admission

Revision ID: 0013_gpu_node_disk
Revises: 0012_session_cpu_mem_disk
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_gpu_node_disk"
down_revision: Union[str, None] = "0012_session_cpu_mem_disk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gpu_node", sa.Column("disk", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("gpu_node", "disk")
