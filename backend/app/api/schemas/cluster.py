"""Cluster response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.api.schemas.common import PageMeta


class ClusterRead(BaseModel):
    """One cluster as projected by ``_serialize_cluster`` (list rows)."""

    id: str
    name: str
    role: str
    api_server: str | None = None
    runtime: str | None = None
    status: str
    node_count: int
    gpu_count: int
    registered_at: datetime | None = None


class ClusterList(BaseModel):
    """GET /clusters envelope."""

    data: list[ClusterRead]
    pagination: PageMeta
