"""session.node_hostname: the k8s node the pod landed on (operator-reported).

Revision ID: 0045_session_node_hostname
Revises: 0044_node_role
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_session_node_hostname"
down_revision = "0044_node_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session", sa.Column("node_hostname", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("session", "node_hostname")
