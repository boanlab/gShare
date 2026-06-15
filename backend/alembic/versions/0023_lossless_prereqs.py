"""lossless pause prerequisites: gpu_node.lossless_capable and offering.lossless_pause

Phase 0 of lossless pause. The node-side prerequisites (cuda-checkpoint plus CRIU) are detected from
node labels by the operator's inventory controller and reported as gpu_node.lossless_capable, while
offering.lossless_pause is an administrator opt-in per offering. Existing rows are false.

Revision ID: 0023_lossless_prereqs
Revises: 0022_rename_base_image_tags
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_lossless_prereqs"
down_revision: Union[str, None] = "0022_rename_base_image_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gpu_node",
        sa.Column("lossless_capable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "offering",
        sa.Column("lossless_pause", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("offering", "lossless_pause")
    op.drop_column("gpu_node", "lossless_capable")
