"""audit_log: org_id and project_id scope columns, for hierarchical audit queries

These sit outside the hash-chain payload (actor, action, target, result, detail, created_at), so
append-only integrity is unaffected. They let an org_admin see their organization's log and a
group_admin their group's.

Revision ID: 0007_audit_scope
Revises: 0006_drop_alloc_device_live
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_audit_scope"
down_revision: Union[str, None] = "0006_drop_alloc_device_live"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("org_id", sa.String(), nullable=True))
    op.add_column("audit_log", sa.Column("group_id", sa.String(), nullable=True))
    op.create_index("ix_audit_log_org_id", "audit_log", ["org_id"])
    op.create_index("ix_audit_log_group_id", "audit_log", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_group_id", table_name="audit_log")
    op.drop_index("ix_audit_log_org_id", table_name="audit_log")
    op.drop_column("audit_log", "group_id")
    op.drop_column("audit_log", "org_id")
