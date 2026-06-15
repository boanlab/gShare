"""rename project_id -> group_id, role project_admin -> group_admin

Unify the internal identifiers and role values on "group". The physical table name (project) and the
scope discriminator value ("project") are left alone so code and database stay consistent. Postgres
updates indexes, foreign keys, and unique constraints automatically on a column RENAME.

Revision ID: 0008_rename_project_to_group
Revises: 0007_audit_scope
"""
from typing import Union

from alembic import op

revision: str = "0008_rename_project_to_group"
down_revision: Union[str, None] = "0007_audit_scope"
branch_labels = None
depends_on = None

_TABLES = ["membership", "session", "image_build"]


def upgrade() -> None:
    for t in _TABLES:
        op.alter_column(t, "project_id", new_column_name="group_id")
    # Migrate the role values.
    op.execute("UPDATE membership SET role='group_admin' WHERE role='project_admin'")


def downgrade() -> None:
    op.execute("UPDATE membership SET role='project_admin' WHERE role='group_admin'")
    for t in _TABLES:
        op.alter_column(t, "group_id", new_column_name="project_id")
