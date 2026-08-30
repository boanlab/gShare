"""Registry uniqueness is per owner: members import the same public ref independently.

Imports used to be admin-only, so one row per registry ref was enough. Members may now import a
public image for themselves, which means two users can legitimately hold the same ref. Uniqueness
therefore moves to (registry, owner_user_id); because Postgres considers NULL owners distinct, the
shared catalogue keeps its own partial unique index on registry alone.

Any older single-column UNIQUE on image.registry is dropped first (constraint or index — earlier
deployments differ), so this migration is safe on a database that never had one.

Revision ID: 0040_image_registry_per_owner
Revises: 0039_image_build_pipeline
"""
from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "0040_image_registry_per_owner"
down_revision = "0039_image_build_pipeline"
branch_labels = None
depends_on = None

# Drop whatever single-column uniqueness on image.registry a given deployment happens to carry.
_DROP_LEGACY = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'image' AND c.contype = 'u'
          AND c.conkey = ARRAY[(SELECT attnum FROM pg_attribute
                                WHERE attrelid = t.oid AND attname = 'registry')]::smallint[]
    LOOP
        EXECUTE format('ALTER TABLE image DROP CONSTRAINT %I', r.conname);
    END LOOP;
    FOR r IN
        SELECT i.relname
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        JOIN pg_class t ON t.oid = x.indrelid
        WHERE t.relname = 'image' AND x.indisunique AND x.indnatts = 1
          AND x.indpred IS NULL
          AND x.indkey[0] = (SELECT attnum FROM pg_attribute
                             WHERE attrelid = t.oid AND attname = 'registry')
    LOOP
        EXECUTE format('DROP INDEX %I', r.relname);
    END LOOP;
END $$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(text(_DROP_LEGACY))
    op.create_index("uq_image_registry_owner", "image", ["registry", "owner_user_id"], unique=True)
    op.create_index(
        "uq_image_registry_shared", "image", ["registry"], unique=True,
        postgresql_where=text("owner_user_id IS NULL"),
        sqlite_where=text("owner_user_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_image_registry_shared", table_name="image")
    op.drop_index("uq_image_registry_owner", table_name="image")
