"""image: add a public column

Private images are hidden from the session wizard, while the administrative catalogue always shows
them. Existing rows are marked public.

Revision ID: 0021_image_public
Revises: 0020_offering_min_cuda
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_image_public"
down_revision: Union[str, None] = "0020_offering_min_cuda"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("image", sa.Column("public", sa.Boolean(), nullable=False, server_default=sa.text("true")))


def downgrade() -> None:
    op.drop_column("image", "public")
