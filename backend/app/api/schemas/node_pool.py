"""Node pool schemas (admin API: /admin/node-pools, /admin/nodes/{id}/pool)."""
from __future__ import annotations

from pydantic import BaseModel, Field

POOL_KIND_PATTERN = "^(shared|dedicated)$"
GRANT_SCOPE_PATTERN = "^(org|group)$"


class PoolNodeRow(BaseModel):
    id: str
    hostname: str
    status: str
    device_count: int


class PoolGrantRead(BaseModel):
    id: str
    scope: str                      # org | group
    scope_id: str
    name: str | None = None         # organization / group display name
    created_at: str | None = None


class PoolRead(BaseModel):
    id: str
    cluster_id: str
    cluster_name: str | None = None
    name: str
    description: str | None = None
    kind: str                       # shared | dedicated
    node_count: int
    nodes: list[PoolNodeRow]
    grants: list[PoolGrantRead]


class PoolList(BaseModel):
    data: list[PoolRead]
    total: int


class PoolCreate(BaseModel):
    cluster_id: str
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    kind: str = Field(default="dedicated", pattern=POOL_KIND_PATTERN)


class PoolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    kind: str | None = Field(default=None, pattern=POOL_KIND_PATTERN)


class NodePoolSet(BaseModel):
    # null moves the node back to the shared (unassigned) pool.
    pool_id: str | None = None


class PoolGrantCreate(BaseModel):
    scope: str = Field(pattern=GRANT_SCOPE_PATTERN)
    scope_id: str = Field(min_length=1)
