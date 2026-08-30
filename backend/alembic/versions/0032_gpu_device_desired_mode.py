"""Per-card pool modes: gpu_device.desired_mode + mode_state, and the cluster placement index

`mode` becomes the observed per-card state (HAMi reports hami-core vs mig per card);
`desired_mode` is the admin-set target applied through the drain state machine, and
`mode_state` gates placement while a card drains or a mode change is applying.

Revision ID: 0032_gpu_device_desired_mode
Revises: 0031_ops_polish
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_gpu_device_desired_mode"
down_revision: Union[str, None] = "0031_ops_polish"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gpu_device", sa.Column("desired_mode", sa.String(), nullable=True))
    op.add_column(
        "gpu_device",
        sa.Column("mode_state", sa.String(), nullable=False, server_default=sa.text("'ready'")),
    )
    op.create_index("ix_dev_cluster_status_mode", "gpu_device", ["cluster_id", "status", "mode"])


def downgrade() -> None:
    op.drop_index("ix_dev_cluster_status_mode", table_name="gpu_device")
    op.drop_column("gpu_device", "mode_state")
    op.drop_column("gpu_device", "desired_mode")
