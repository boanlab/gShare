"""Per-group welcome credit + top-up request note.

- group.default_member_credit: credits minted into a member's personal wallet the first time
  they join the group (0 disables).
- topup_request.note: the requester's justification, previously accepted by the API and dropped.

Revision ID: 0034_welcome_credit
Revises: 0033_drop_dead_surfaces
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_welcome_credit"
down_revision = "0033_drop_dead_surfaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "group",
        sa.Column("default_member_credit", sa.Numeric(18, 2), nullable=False, server_default="0"),
    )
    op.add_column("topup_request", sa.Column("note", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("topup_request", "note")
    op.drop_column("group", "default_member_credit")
