"""Drop dead persistence surfaces found by the full-code audit.

- connection_token: connect tokens live only in Redis; the table never had an INSERT site.
- storage_volume.host: never written, never read.
- budget_alert.channel: never written, never read (alerts deliver via notifications).

Revision ID: 0033_drop_dead_surfaces
Revises: 0032_gpu_device_desired_mode
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_drop_dead_surfaces"
down_revision = "0032_gpu_device_desired_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("connection_token")
    with op.batch_alter_table("storage_volume") as batch:
        batch.drop_column("host")
    with op.batch_alter_table("budget_alert") as batch:
        batch.drop_column("channel")


def downgrade() -> None:
    with op.batch_alter_table("budget_alert") as batch:
        batch.add_column(sa.Column("channel", sa.String(), nullable=True))
    with op.batch_alter_table("storage_volume") as batch:
        batch.add_column(sa.Column("host", sa.String(), nullable=True))
    op.create_table(
        "connection_token",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("session.id"), index=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
