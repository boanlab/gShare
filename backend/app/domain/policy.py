"""Effective-resource-policy resolution — the single implementation.

Policies live at four scopes (user > group > org > global). Resolution is **per-field**
most-specific-wins: each field comes from the most specific policy that sets it, so a user-scoped
policy that only lowers ``max_concurrent`` no longer silently discards the group's runtime and
idle limits (the old first-matching-ROW-wins behaviour, which was also implemented three times in
three modules). ``limits`` merges per key the same way.

Consumers: the scheduler admission gate, the ``/resource-policies/effective`` endpoint, and the
CR annotation stamping in app/cluster/crd.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ResourcePolicy

# Keys carried inside ResourcePolicy.limits (JSONB).
LIMIT_KEYS = (
    "cpu", "mem_gb", "gpu_mem_mb", "gpu_cores", "storage_gb", "volume_gb",
    "cpu_session_max_concurrent", "cpu_session_max_runtime_min", "cpu_session_idle_timeout_sec",
    # bool: may sessions spill onto shared-pool nodes when the tenant holds a dedicated pool?
    # (default True; see app.domain.node_pools)
    "shared_pool",
)

_FIELDS = ("max_concurrent", "max_queued", "max_runtime", "idle_timeout")


@dataclass
class EffectivePolicy:
    """The merged policy that actually applies to one user in one group context."""

    max_concurrent: int | None = None
    max_queued: int | None = None
    max_runtime: int | None = None      # minutes
    idle_timeout: int | None = None     # seconds
    limits: dict[str, int] = field(default_factory=dict)
    # scope name that supplied each field — "user"/"group"/"org"/"global" — for display/debugging.
    sources: dict[str, str] = field(default_factory=dict)


async def resolve_effective_policy(
    db: AsyncSession, user_id: str | None, group_id: str | None
) -> EffectivePolicy | None:
    """Merge the policy chain for (user, group). None when no policy row exists at any scope."""
    chain: list[tuple[str, str]] = []
    if user_id:
        chain.append(("user", user_id))
    if group_id:
        chain.append(("group", group_id))
        project = await db.get(Project, group_id)
        if project is not None and project.org_id:
            chain.append(("org", project.org_id))
    chain.append(("global", "*"))

    merged: EffectivePolicy | None = None
    for scope, scope_id in chain:
        pol = (
            await db.scalars(
                select(ResourcePolicy).where(
                    ResourcePolicy.scope == scope, ResourcePolicy.scope_id == scope_id
                )
            )
        ).first()
        if pol is None:
            continue
        if merged is None:
            merged = EffectivePolicy()
        for f in _FIELDS:
            val = getattr(pol, f, None)
            if val is not None and getattr(merged, f) is None:
                setattr(merged, f, int(val))
                merged.sources[f] = scope
        for key, val in (pol.limits or {}).items():
            if key not in merged.limits and val is not None:
                merged.limits[key] = val
                merged.sources[f"limits.{key}"] = scope
    return merged
