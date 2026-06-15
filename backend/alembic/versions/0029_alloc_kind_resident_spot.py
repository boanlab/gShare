"""allocation.kind terminology: owner becomes resident, borrow becomes spot

Aligns the code identifier (Allocation.kind) with the terminology used in the design notes. Updates
both the existing rows and the server default. Behaviour is unchanged; only the role labels move.

Revision ID: 0029_alloc_kind_resident_spot
Revises: 0028_webhook_org
"""
from typing import Union

from alembic import op

revision: str = "0029_alloc_kind_resident_spot"
down_revision: Union[str, None] = "0028_webhook_org"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE allocation SET kind = 'resident' WHERE kind = 'owner'")
    op.execute("UPDATE allocation SET kind = 'spot' WHERE kind = 'borrow'")
    op.alter_column("allocation", "kind", server_default="resident")


def downgrade() -> None:
    op.execute("UPDATE allocation SET kind = 'owner' WHERE kind = 'resident'")
    op.execute("UPDATE allocation SET kind = 'borrow' WHERE kind = 'spot'")
    op.alter_column("allocation", "kind", server_default="owner")
