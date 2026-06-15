"""user: add global_roles (JSONB) for multiple global roles, and backfill from the single value

global_roles becomes the source of truth, alongside the singular global_role which is kept as the
derived primary. Existing rows are backfilled with [global_role], or [] when it is null.

Revision ID: 0016_user_global_roles
Revises: 0015_storage_volume_name
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_user_global_roles"
down_revision: Union[str, None] = "0015_storage_volume_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "global_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Backfill the existing single global_role into the list.
    op.execute(
        """
        UPDATE "user"
        SET global_roles = CASE
            WHEN global_role IS NULL OR global_role = '' THEN '[]'::jsonb
            ELSE jsonb_build_array(global_role)
        END
        """
    )


def downgrade() -> None:
    op.drop_column("user", "global_roles")
