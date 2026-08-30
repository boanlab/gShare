"""Console image builds become real: build request fields + private image ownership.

Revision ID: 0039_image_build_pipeline
Revises: 0038_resource_request
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039_image_build_pipeline"
down_revision = "0038_resource_request"
branch_labels = None
depends_on = None

_BUILD_COLS = [
    sa.Column("owner_user_id", sa.String(), nullable=True),
    sa.Column("name", sa.String(), nullable=True),
    sa.Column("dockerfile", sa.Text(), nullable=True),
    sa.Column("git_url", sa.String(), nullable=True),
    sa.Column("git_ref", sa.String(), nullable=True),
    sa.Column("context", sa.String(), nullable=True),
    sa.Column("build_args", sa.JSON(), nullable=True),
    sa.Column("target_tag", sa.String(), nullable=True),
    sa.Column("cluster_id", sa.String(), nullable=True),
    sa.Column("error", sa.String(), nullable=True),
    sa.Column("log_tail", sa.Text(), nullable=True),
    sa.Column("image_id", sa.String(), nullable=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    for col in _BUILD_COLS:
        op.add_column("image_build", col)
    op.create_index("ix_image_build_owner_user_id", "image_build", ["owner_user_id"])
    op.add_column("image", sa.Column("owner_user_id", sa.String(), nullable=True))
    op.create_index("ix_image_owner_user_id", "image", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_image_owner_user_id", table_name="image")
    op.drop_column("image", "owner_user_id")
    op.drop_index("ix_image_build_owner_user_id", table_name="image_build")
    for col in reversed(_BUILD_COLS):
        op.drop_column("image_build", col.name)
