"""base image registry tags: drop the -cpu and -gpu suffixes

Realigns the catalogue rows with the simplified build.sh tags (ml-ubuntu24.04-cpu becomes
ml-ubuntu24.04, ml-cuda12.4-cudnn9-gpu becomes ml-cuda12.4-cudnn9). Seeding deduplicates on the
registry reference, so without this the next startup would create duplicate rows.

Operationally: re-run ./build.sh push to publish the new tags to Docker Hub, or pulls will fail.

Revision ID: 0022_rename_base_image_tags
Revises: 0021_image_public
"""
from typing import Union

from alembic import op
from sqlalchemy import text

revision: str = "0022_rename_base_image_tags"
down_revision: Union[str, None] = "0021_image_public"
branch_labels = None
depends_on = None

_RENAMES = [
    ("boanlab/gshare-session:ml-ubuntu24.04-cpu", "boanlab/gshare-session:ml-ubuntu24.04"),
    ("boanlab/gshare-session:ml-cuda12.4-cudnn9-gpu", "boanlab/gshare-session:ml-cuda12.4-cudnn9"),
]


_STMT = text("UPDATE image SET registry = :new WHERE registry = :old")


def upgrade() -> None:
    for old, new in _RENAMES:
        op.execute(_STMT.bindparams(new=new, old=old))


def downgrade() -> None:
    for old, new in _RENAMES:
        op.execute(_STMT.bindparams(new=old, old=new))
