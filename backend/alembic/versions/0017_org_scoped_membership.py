"""membership: support organization-level memberships (org_admin)

Aligns the hierarchy so an org_admin is appointed at the organization level rather than as a role on
a group membership.
- group_id becomes nullable and a nullable org_id foreign key is added.
- The (user_id, group_id) unique constraint is split into two partial unique indexes, one for group
  rows and one for organization rows.
- Existing org_admin rows on group memberships are migrated to organization memberships, with org_id
  set and group_id NULL.

Revision ID: 0017_org_scoped_membership
Revises: 0016_user_global_roles
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_org_scoped_membership"
down_revision: Union[str, None] = "0016_user_global_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Make group_id nullable and add org_id with its foreign key and index.
    op.alter_column("membership", "group_id", existing_type=sa.String(), nullable=True)
    op.add_column("membership", sa.Column("org_id", sa.String(), nullable=True))
    op.create_index("ix_membership_org_id", "membership", ["org_id"], unique=False)
    op.create_foreign_key(
        "fk_membership_org_id_organization", "membership", "organization", ["org_id"], ["id"]
    )

    # 2. Drop the (user_id, group_id) unique constraint in favour of two partial unique indexes.
    op.drop_constraint("uq_membership_user_id", "membership", type_="unique")
    op.create_index(
        "uq_membership_user_group", "membership", ["user_id", "group_id"],
        unique=True, postgresql_where=sa.text("group_id IS NOT NULL"),
    )
    op.create_index(
        "uq_membership_user_org", "membership", ["user_id", "org_id"],
        unique=True, postgresql_where=sa.text("org_id IS NOT NULL"),
    )

    # 3. Migrate org_admin group memberships to organization memberships, once per organization.
    op.execute(
        """
        INSERT INTO membership (id, user_id, org_id, group_id, role, created_at, updated_at)
        SELECT DISTINCT ON (m.user_id, g.org_id)
               'mbr_' || substr(md5(m.user_id || g.org_id || random()::text), 1, 26),
               m.user_id, g.org_id, NULL, 'org_admin', now(), now()
        FROM membership m
        JOIN "group" g ON g.id = m.group_id
        WHERE m.role = 'org_admin' AND m.group_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM membership x
            WHERE x.user_id = m.user_id AND x.org_id = g.org_id AND x.role = 'org_admin'
          )
        """
    )
    op.execute("DELETE FROM membership WHERE role = 'org_admin' AND group_id IS NOT NULL")


def downgrade() -> None:
    # Mapping organization memberships back to group rows is not deterministic, so the downgrade
    # restores the schema only and skips the data migration.
    op.drop_index("uq_membership_user_org", table_name="membership")
    op.drop_index("uq_membership_user_group", table_name="membership")
    op.create_unique_constraint("uq_membership_user_id", "membership", ["user_id", "group_id"])
    op.drop_constraint("fk_membership_org_id_organization", "membership", type_="foreignkey")
    op.drop_index("ix_membership_org_id", table_name="membership")
    op.drop_column("membership", "org_id")
    op.alter_column("membership", "group_id", existing_type=sa.String(), nullable=False)
