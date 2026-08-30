"""Ops polish: webhook delivery outbox + audit_log created_at index

The webhook_delivery table is the outbox behind the dispatcher worker (subscriptions used to be
stored with no delivery at all). The audit index backs the retention sweep and time-window reads.

Revision ID: 0031_ops_polish
Revises: 0030_session_status_reason
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0031_ops_polish"
down_revision: Union[str, None] = "0030_session_status_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_delivery",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("subscription_id", sa.String(), sa.ForeignKey("webhook_subscription.id"), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_delivery_subscription_id", "webhook_delivery", ["subscription_id"])
    op.create_index("ix_webhook_delivery_next_attempt_at", "webhook_delivery", ["next_attempt_at"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_webhook_delivery_next_attempt_at", table_name="webhook_delivery")
    op.drop_index("ix_webhook_delivery_subscription_id", table_name="webhook_delivery")
    op.drop_table("webhook_delivery")
