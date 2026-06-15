"""gpu_device.lend_state(''|'yielded'|'lent') + allocation.kind('owner'|'borrow'): yield lending.

Revision ID: 0026_gpu_lending
Revises: 0025_session_yield_mode
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_gpu_lending"
down_revision: Union[str, None] = "0025_session_yield_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gpu_device",
        sa.Column("lend_state", sa.String(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "allocation",
        sa.Column("kind", sa.String(), nullable=False, server_default=sa.text("'owner'")),
    )


def downgrade() -> None:
    op.drop_column("allocation", "kind")
    op.drop_column("gpu_device", "lend_state")
