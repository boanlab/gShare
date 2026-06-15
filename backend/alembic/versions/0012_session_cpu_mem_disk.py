"""add cpu/mem_gb/disk_gb — offering flavor + session snapshot (Pod request=limit)

Revision ID: 0012_session_cpu_mem_disk
Revises: 0011_topup_decided_reason
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_session_cpu_mem_disk"
down_revision: Union[str, None] = "0011_topup_decided_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # offering: ephemeral scratch disk in GiB. cpu and mem_gb already exist.
    op.add_column("offering", sa.Column("disk_gb", sa.Integer(), nullable=True))
    # session: snapshot the offering flavor's cpu, mem_gb, and disk_gb at creation time.
    op.add_column("session", sa.Column("cpu", sa.Integer(), nullable=True))
    op.add_column("session", sa.Column("mem_gb", sa.Integer(), nullable=True))
    op.add_column("session", sa.Column("disk_gb", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("session", "disk_gb")
    op.drop_column("session", "mem_gb")
    op.drop_column("session", "cpu")
    op.drop_column("offering", "disk_gb")
