"""offering: add a min_cuda column, e.g. '12.8'

The minimum CUDA toolkit a GPU model requires. Compared against an image's cuda_version so the
session wizard and the scheduler only allow compatible images
(image.cuda_version >= offering.min_cuda).

Revision ID: 0020_offering_min_cuda
Revises: 0019_offering_status
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_offering_min_cuda"
down_revision: Union[str, None] = "0019_offering_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offering", sa.Column("min_cuda", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("offering", "min_cuda")
