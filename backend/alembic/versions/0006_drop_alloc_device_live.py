"""allocation: drop uq_alloc_device_live so fractional sharing works

A UNIQUE index of one live allocation per device stopped fractional sessions from coexisting on the
same physical GPU, which made sharing impossible. Exclusive isolation is guaranteed instead by the
scheduler reserving the card's whole capacity together with gpu_device's no_overcommit check
constraint, so this index is both unnecessary and harmful.

Revision ID: 0006_drop_alloc_device_live
Revises: 0005_wallet_monthly_grant
"""
from typing import Union

from alembic import op

revision: str = "0006_drop_alloc_device_live"
down_revision: Union[str, None] = "0005_wallet_monthly_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_alloc_device_live", table_name="allocation")


def downgrade() -> None:
    op.create_index(
        "uq_alloc_device_live", "allocation", ["device_id"], unique=True,
        postgresql_where=op.f("ended_at IS NULL"),
    )
