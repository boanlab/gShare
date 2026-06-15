"""credit_wallet.monthly_grant: the monthly automatic refill amount

Revision ID: 0005_wallet_monthly_grant
Revises: 0004_credit_allocation_request
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_wallet_monthly_grant"
down_revision: Union[str, None] = "0004_credit_allocation_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "credit_wallet",
        sa.Column("monthly_grant", sa.Numeric(18, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("credit_wallet", "monthly_grant")
