"""Notices (announcements) and inquiries (user questions + replies).

Revision ID: 0047_notice_inquiry
Revises: 0046_system_setting
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0047_notice_inquiry"
down_revision = "0046_system_setting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notice",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("scope", sa.String(), nullable=False, server_default="global"),
        sa.Column("group_id", sa.String(), sa.ForeignKey("group.id"), nullable=True, index=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("author_id", sa.String(), sa.ForeignKey("user.id"), nullable=False, index=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "inquiry",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("author_id", sa.String(), sa.ForeignKey("user.id"), nullable=False, index=True),
        sa.Column("group_id", sa.String(), sa.ForeignKey("group.id"), nullable=True, index=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="open", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "inquiry_reply",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("inquiry_id", sa.String(), sa.ForeignKey("inquiry.id"), nullable=False, index=True),
        sa.Column("author_id", sa.String(), sa.ForeignKey("user.id"), nullable=False, index=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("inquiry_reply")
    op.drop_table("inquiry")
    op.drop_table("notice")
