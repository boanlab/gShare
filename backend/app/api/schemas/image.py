"""Image catalog + image-build response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.api.schemas.common import PageMeta


class ImageRead(BaseModel):
    """One catalog image as projected by ``_serialize_image``.

    ``import_status`` is only present for imported/registered rows, so it is Optional.
    """

    id: str
    name: str
    registry: str
    kind: str
    tags: dict[str, Any]
    supported_gpus: list[Any]
    cuda_version: str | None = None
    public: bool = True
    # null = shared catalogue entry; set = a private image its owner (and admins) can see. The
    # console distinguishes the caller's own images with it, so it has to survive the projection.
    owner_user_id: str | None = None
    created_at: datetime | None = None
    import_status: str | None = None


class ImageList(BaseModel):
    """GET /images envelope."""

    data: list[ImageRead]
    pagination: PageMeta


class ImageBuildRead(BaseModel):
    """One build row as projected by ``_serialize_build``."""

    id: str
    group_id: str | None = None
    owner_user_id: str | None = None
    name: str | None = None
    source: str
    status: str
    error: str | None = None
    image_ref: str | None = None
    image_id: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ImageBuildList(BaseModel):
    """GET /image-builds envelope."""

    data: list[ImageBuildRead]
    pagination: PageMeta
