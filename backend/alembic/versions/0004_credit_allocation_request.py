"""credit_allocation_request: hierarchical credit allocation requests and escalation

Revision ID: 0004_credit_allocation_request
Revises: 0003_volume_partial_unique
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_credit_allocation_request"
down_revision: Union[str, None] = "0003_volume_partial_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credit_allocation_request",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("requester_id", sa.String(), nullable=False),
        sa.Column("target_wallet_id", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("fulfiller_scope", sa.String(), nullable=False),
        sa.Column("fulfiller_id", sa.String(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["target_wallet_id"], ["credit_wallet.id"],
                                name="fk_credit_allocation_request_target_wallet_id_credit_wallet"),
        sa.PrimaryKeyConstraint("id", name="pk_credit_allocation_request"),
    )
    op.create_index("ix_credit_allocation_request_requester_id", "credit_allocation_request", ["requester_id"])
    op.create_index("ix_credit_allocation_request_fulfiller_id", "credit_allocation_request", ["fulfiller_id"])
    op.create_index("ix_credit_allocation_request_target_wallet_id", "credit_allocation_request", ["target_wallet_id"])


def downgrade() -> None:
    op.drop_table("credit_allocation_request")
