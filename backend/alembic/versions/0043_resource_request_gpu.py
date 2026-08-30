"""GPU targets on quota requests: users can ask for a bigger VRAM / GPU-core allowance.

The approval path upserts the same user-scope ResourcePolicy keys (gpu_mem_mb / gpu_cores) the
admission quota gate already reads; only the request row needed the two columns.

Revision ID: 0043_resource_request_gpu
Revises: 0042_session_status_changed_at
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0043_resource_request_gpu"
down_revision = "0042_session_status_changed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resource_request", sa.Column("gpu_mem_mb", sa.Integer(), nullable=True))
    op.add_column("resource_request", sa.Column("gpu_cores", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("resource_request", "gpu_cores")
    op.drop_column("resource_request", "gpu_mem_mb")
