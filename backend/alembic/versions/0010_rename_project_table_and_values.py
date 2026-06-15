"""rename project table -> group, and stored 'project' discriminator values -> 'group'

Rename the table project to "group" — a reserved word, hence the quoting — and migrate the stored
'project' values in scope, owner_type, level, fulfiller_scope, and volume.type to 'group'. The models,
schemas, and routers use group throughout as well.

Revision ID: 0010_rename_project_table_and_values
Revises: 0009_user_must_change_password
"""
from typing import Union

from alembic import op

revision: str = "0010_project_to_group"
down_revision: Union[str, None] = "0009_user_must_change_password"
branch_labels = None
depends_on = None

_VALUE_UPDATES = [
    ("credit_wallet", "owner_type"),
    ("budget", "scope"),
    ("resource_policy", "scope"),
    ("credit_allocation_request", "level"),
    ("credit_allocation_request", "fulfiller_scope"),
    ("storage_volume", "scope"),
    ("storage_volume", "type"),
]


def upgrade() -> None:
    op.execute('ALTER TABLE project RENAME TO "group"')
    for table, col in _VALUE_UPDATES:
        op.execute(f"UPDATE {table} SET {col}='group' WHERE {col}='project'")


def downgrade() -> None:
    for table, col in _VALUE_UPDATES:
        op.execute(f"UPDATE {table} SET {col}='project' WHERE {col}='group'")
    op.execute('ALTER TABLE "group" RENAME TO project')
