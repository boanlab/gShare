"""User-facing response schemas (auth/me identity payload)."""
from __future__ import annotations

from pydantic import BaseModel


class MeMembership(BaseModel):
    """One membership row resolved for the context switcher / RBAC."""

    group_id: str
    project_name: str
    org_id: str | None = None
    org_name: str | None = None
    role: str
    has_group_admin: bool = True               # someone (group_admin/org_admin) can approve requests here


class MeResponse(BaseModel):
    """Identity/role/memberships of the current principal (GET /auth/me)."""

    user_id: str
    global_role: str | None = None             # derived primary role (super_admin)
    global_roles: list[str] = []               # every global role granted; there may be several
    org_admin_orgs: list[str] = []             # organizations this user administers; authority survives even with no groups yet
    memberships: list[MeMembership]
    email: str | None = None
    name: str | None = None                    # display name, editable on the account screen
    must_change_password: bool
