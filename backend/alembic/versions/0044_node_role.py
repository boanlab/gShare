"""Node role for the console's node list: master | gpu | cpu | storage.

Derived by the operator's inventory controller from node labels (control-plane, gshare.io/role)
and the presence of GPU devices; purely informational.

Revision ID: 0044_node_role
Revises: 0043_resource_request_gpu
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0044_node_role"
down_revision = "0043_resource_request_gpu"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gpu_node", sa.Column("role", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("gpu_node", "role")
