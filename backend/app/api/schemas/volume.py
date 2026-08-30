"""Storage volume schemas."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.common import ORMModel


class VolumeCreate(BaseModel):
    scope: str = Field(pattern="^(user|group)$")
    scope_id: str
    type: str = Field(pattern="^(home|group|dataset|scratch)$")
    name: str = Field(min_length=1, max_length=80)   # user-chosen volume name
    access_mode: str = Field(pattern="^(RWO|RWX|ROX)$")
    quota_gb: int = Field(ge=0)


class VolumeRead(ORMModel):
    id: str
    scope: str
    scope_id: str
    type: str
    name: str | None = None
    access_mode: str
    quota_gb: int
    used_gb: int
    role: str | None = None  # the caller's role on this volume: owner, rw, or ro; None when they have none
    owner_id: str | None = None
    owner_name: str | None = None  # display name of the creator, for shared/admin listings
    shared_count: int = 0          # users granted access besides the owner (rw/ro)
    # Sessions currently mounting this volume — filled on the single-volume read only
    # (response_model strips undeclared keys, so the field must exist here).
    active_mounts: list[dict] | None = None


class VolumePatch(BaseModel):
    """Owner-side edits. The quota is self-service in both directions (bounded below by usage and
    above by the scope's storage policy on the server)."""
    model_config = ConfigDict(extra="forbid")
    quota_gb: int | None = Field(default=None, ge=1)
    access_mode: str | None = Field(default=None, pattern="^(RWO|RWX|ROX)$")


class PermissionBody(BaseModel):
    # Give either a user_id or an email. With an email, the server resolves it to a user_id.
    user_id: str | None = None
    email: str | None = None
    role: str = Field(pattern="^(owner|rw|ro)$")
