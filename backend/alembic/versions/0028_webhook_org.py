"""webhook_subscription.org_id: organization-scoped ownership. NULL means global, super_admin only.

Revision ID: 0028_webhook_org
Revises: 0027_session_priority
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_webhook_org"
down_revision: Union[str, None] = "0027_session_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("webhook_subscription", sa.Column("org_id", sa.String(), nullable=True))
    op.create_index("ix_webhook_subscription_org_id", "webhook_subscription", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_webhook_subscription_org_id", table_name="webhook_subscription")
    op.drop_column("webhook_subscription", "org_id")
