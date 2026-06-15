"""Principal + RBAC: global_role ∪ Membership.

The ``Membership`` roles are not in the token; they are resolved from the DB and unioned with the
token's ``global_role``. Guests whose membership expired are excluded at resolve time (-> 403).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden
from app.db.models import Membership, Project

# Global roles; currently super_admin is the only one. global_role is the primary derived from
# global_roles, kept in sync so scalar checks and queries against User.global_role keep working.
GLOBAL_ROLES = ("super_admin",)
_GLOBAL_ROLE_RANK = {"super_admin": 2}


def primary_global_role(roles: list[str] | set[str] | None) -> str | None:
    """Pick the single primary role out of several global roles; currently only super_admin. None
    when there is none."""
    best, best_rank = None, 0
    for r in roles or ():
        rank = _GLOBAL_ROLE_RANK.get(r, 0)
        if rank > best_rank:
            best, best_rank = r, rank
    return best


# Project-scoped role rank: higher rank ⊇ lower rank for "member+"/"group_admin+" rules.
# guest is a limited member; member+ therefore EXCLUDES guest.
_RANK = {"guest": 0, "member": 1, "group_admin": 2, "org_admin": 3}

# Action -> required capability, derived from the per-endpoint permission column.
# Each spec is a set of token "tiers" any of which grants the action:
# "super_admin" — global super_admin only
# ("scoped", "<min_role>") — a project-scoped membership of at least <min_role>
# super_admin is always allowed (checked first in rbac_allows).
_ACTION_MATRIX: dict[str, tuple] = {
    # Users / global role
    "user.read": ("super_admin", ("scoped", "group_admin")),
    "user.create": ("super_admin", ("scoped", "org_admin")),
    "user.delete": ("super_admin", ("scoped", "org_admin")),
    "user.set_global_role": ("super_admin",),
    # Organizations / projects(groups) / memberships
    "org.read": ("super_admin", ("scoped", "org_admin")),
    "org.create": ("super_admin",),
    "org.update": ("super_admin",),
    "org.delete": ("super_admin",),
    "org.set_admin": ("super_admin",),   # appoint and remove organization admins; super_admin only
    "group.read": ("super_admin", ("scoped", "member")),
    "group.create": ("super_admin", ("scoped", "org_admin")),
    "group.update": ("super_admin", ("scoped", "group_admin")),
    "group.delete": ("super_admin", ("scoped", "org_admin")),
    "membership.read": ("super_admin", ("scoped", "group_admin")),
    "membership.create": ("super_admin", ("scoped", "group_admin")),
    "membership.update": ("super_admin", ("scoped", "group_admin")),
    "membership.delete": ("super_admin", ("scoped", "group_admin")),
    # Wallets / credits
    "wallet.read": ("super_admin", ("scoped", "org_admin")),
    "credit.topup": ("super_admin",),
    "credit.adjust": ("super_admin",),
    "credit.transfer": ("super_admin", ("scoped", "org_admin")),
    # Catalog: offerings — super_admin only
    "offering.create": ("super_admin",),
    "offering.update": ("super_admin",),
    # Presets — super_admin only, since the resources, offerings, and presets screens are
    "preset.create": ("super_admin",),
    # Policies — super_admin·org_admin·group_admin
    "policy.read": ("super_admin", ("scoped", "member")),
    "policy.create": ("super_admin", ("scoped", "group_admin")),
    "policy.update": ("super_admin", ("scoped", "group_admin")),
    "policy.delete": ("super_admin", ("scoped", "group_admin")),
    # Images — catalogue management is super_admin only; building is project work, member and above
    "image.create": ("super_admin",),
    "image.build": ("super_admin", ("scoped", "member")),
    # Sessions — create/read member+, force-terminate group_admin+, monitor group_admin+
    "session.create": ("super_admin", ("scoped", "member")),
    "session.read": ("super_admin", ("scoped", "member")),
    "session.force_terminate": ("super_admin", ("scoped", "group_admin")),
    "session.monitor": ("super_admin", ("scoped", "group_admin")),
    # Queue — read/update admin
    "queue.read": ("super_admin", ("scoped", "group_admin")),
    "queue.update": ("super_admin", ("scoped", "group_admin")),
    # Clusters / nodes — super_admin only
    "cluster.read": ("super_admin",),
    "cluster.create": ("super_admin",),
    "cluster.update": ("super_admin",),
    "cluster.delete": ("super_admin",),
    "node.read": ("super_admin",),
    "node.create": ("super_admin",),
    "node.cordon": ("super_admin",),
    "node.drain": ("super_admin",),
    # Storage volumes — group_admin+
    "volume.create": ("super_admin", ("scoped", "group_admin")),
    "volume.delete": ("super_admin", ("scoped", "group_admin")),
    # Budgets / FinOps — super_admin·org_admin·group_admin
    "budget.read": ("super_admin", ("scoped", "group_admin")),
    "budget.create": ("super_admin", ("scoped", "group_admin")),
    "budget.update": ("super_admin", ("scoped", "group_admin")),
    "budget.delete": ("super_admin", ("scoped", "group_admin")),
    # Webhooks — super_admin and org_admin. This coarse gate lets org_admin through, and the handler
    # then scopes WebhookSubscription.org_id to the caller's org_admin_orgs. A global subscription
    # (org_id NULL) stays super_admin only.
    "webhook.read": ("super_admin", ("scoped", "org_admin")),
    "webhook.create": ("super_admin", ("scoped", "org_admin")),
    "webhook.delete": ("super_admin", ("scoped", "org_admin")),
    # Audit logs — super_admin·org_admin
    "audit.read": ("super_admin", ("scoped", "group_admin")),
}


@dataclass
class Principal:
    user_id: str
    global_role: str | None = None                 # derived primary: super_admin or None
    global_roles: set[str] = field(default_factory=set)  # every global role granted; there may be several
    memberships: dict[str, str] = field(default_factory=dict)  # {group_id: role}
    email: str | None = None
    org_admin_orgs: set[str] = field(default_factory=set)  # ids of the organizations this principal administers, used to scope list queries

    def require(self, action: str, group_id: str | None = None) -> None:
        """Guard: raise Forbidden (403) unless the principal may perform ``action``."""
        if not rbac_allows(self, action, group_id):
            raise Forbidden(f"not permitted: {action}")


def _scoped_role_satisfies(principal: Principal, min_role: str, group_id: str | None) -> bool:
    """True if the principal holds a project-scoped role of at least ``min_role``.

    org_admin is a tenant-wide scoped role: it satisfies project-scoped checks for ANY project the
    principal is a member of. When a specific ``group_id`` is supplied we require a matching
    membership; otherwise (collection endpoints) we accept any membership of sufficient rank.
    """
    need = _RANK.get(min_role)
    if need is None:
        return False
    if group_id is not None:
        # resolve_principal already expands an org_admin across every project in their organization,
        # so only that project's membership matters here. A global fallback for org_admin would open
        # projects in other organizations, so there deliberately is none.
        role = principal.memberships.get(group_id)
        return role is not None and _RANK.get(role, -1) >= need
    # No project bound: any membership of sufficient rank grants the action, and the handler filters
    # its results by membership.
    if any(_RANK.get(r, -1) >= need for r in principal.memberships.values()):
        return True
    # An org_admin must retain their authority even when the organization has no projects yet, so
    # that they can create the first group and manage users. Membership expansion cannot express
    # that, so org_admin_orgs is used to satisfy group-less checks at the org_admin tier and below.
    return bool(principal.org_admin_orgs) and need <= _RANK["org_admin"]


def rbac_allows(principal: Principal, action: str, group_id: str | None = None) -> bool:
    """Evaluate union of global_role (global) and memberships[group_id] (scoped).

    super_admin is allowed everything. Otherwise the action's spec from ``_ACTION_MATRIX`` derived
    from the per-endpoint permission column is matched against the principal's global_role and
    scoped membership roles. Unknown actions are denied (fail-closed). """
    if "super_admin" in principal.global_roles:
        return True
    spec = _ACTION_MATRIX.get(action)
    if spec is None:
        return False  # fail-closed: unknown action
    for tier in spec:
        if tier == "super_admin":
            continue  # already handled above
        if isinstance(tier, tuple) and tier and tier[0] == "scoped":
            if _scoped_role_satisfies(principal, tier[1], group_id):
                return True
    return False


async def resolve_principal(db: AsyncSession, claims: dict) -> Principal:
    """Build a Principal from verified JWT claims + DB memberships."""
    rows = (await db.scalars(
        select(Membership).where(Membership.user_id == claims["sub"])
    )).all()
    # Exclude memberships (guest or otherwise) whose expires_at has passed -> 403.
    now = datetime.now(UTC)

    def _expired(m: Membership) -> bool:
        if m.expires_at is None:
            return False
        exp = m.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return exp <= now

    memberships: dict[str, str] = {}
    org_admin_orgs: set[str] = set()
    for m in rows:
        if _expired(m):
            continue
        if m.group_id is not None:
            memberships[m.group_id] = m.role
        elif m.org_id is not None and m.role == "org_admin":
            # Organization-level membership: an organization admin, scoped to the whole
            # organization.
            org_admin_orgs.add(m.org_id)

    # Legacy compatibility: an org_admin left on a group membership is absorbed into that group's
    # organization.
    legacy_org_admin_projects = [pid for pid, role in memberships.items() if role == "org_admin"]
    if legacy_org_admin_projects:
        legacy_orgs = (
            await db.scalars(select(Project.org_id).where(Project.id.in_(legacy_org_admin_projects)))
        ).all()
        org_admin_orgs |= {o for o in legacy_orgs if o is not None}

    # Expand org_admin across every live project in the organizations they administer, which keeps
    # the tenant boundary intact. This is what makes the scoped check accurate with a per-project
    # lookup alone, with no authority leaking into other organizations.
    if org_admin_orgs:
        sibling_ids = (
            await db.scalars(
                select(Project.id).where(
                    Project.org_id.in_(org_admin_orgs), Project.deleted_at.is_(None)
                )
            )
        ).all()
        for pid in sibling_ids:
            cur = memberships.get(pid)
            if cur is None or _RANK.get(cur, -1) < _RANK["org_admin"]:
                memberships[pid] = "org_admin"

    # Prefer the token's global_roles list; an older token carrying a single global_role is promoted
    # to a list.
    roles_claim = claims.get("global_roles")
    if isinstance(roles_claim, list):
        global_roles = {r for r in roles_claim if r in GLOBAL_ROLES}
    else:
        single = claims.get("global_role")
        global_roles = {single} if single in GLOBAL_ROLES else set()

    return Principal(
        user_id=claims["sub"],
        global_role=primary_global_role(global_roles),
        global_roles=global_roles,
        memberships=memberships,
        email=claims.get("email"),
        org_admin_orgs=org_admin_orgs,
    )
