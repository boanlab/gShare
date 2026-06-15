"""resource_preset: split into compute and gpu kinds (kind, gpu_frac, mode, disk_gb)

Presets are separated into compute (cpu, mem, disk) and gpu (a per-model fraction of VRAM and cores).
- kind: 'compute' or 'gpu'. Existing rows are backfilled as gpu, since the original seed data had the
  shape of GPU resources.
- gpu_frac: the card fraction of a gpu preset, such as 0.125, 0.25, 0.5, or 1.0.
- mode: the sharing mode of a gpu preset, fractional or exclusive.
- disk_gb: the disk of a compute preset, in GiB.

Revision ID: 0018_preset_kind_split
Revises: 0017_org_scoped_membership
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_preset_kind_split"
down_revision: Union[str, None] = "0017_org_scoped_membership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resource_preset", sa.Column("kind", sa.String(), nullable=False, server_default="gpu"))
    op.add_column("resource_preset", sa.Column("gpu_frac", sa.Numeric(5, 3), nullable=True))
    op.add_column("resource_preset", sa.Column("mode", sa.String(), nullable=True))
    op.add_column("resource_preset", sa.Column("disk_gb", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("resource_preset", "disk_gb")
    op.drop_column("resource_preset", "mode")
    op.drop_column("resource_preset", "gpu_frac")
    op.drop_column("resource_preset", "kind")
