"""Audit-log response schemas (hash-chained log envelope)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.api.schemas.common import PageMeta


class AuditLogEntry(BaseModel):
    """One audit-log row as projected by ``_audit_view`` (+ resolved actor_name)."""

    id: str
    actor_id: str | None = None
    action: str
    target: str | None = None
    result: str | None = None
    detail: dict[str, Any]
    trace_id: str | None = None
    prev_hash: str | None = None
    entry_hash: str | None = None
    at: str | None = None
    actor_name: str | None = None
    actor_email: str | None = None   # resolved for the detail view (5-6); operator actors have none
    target_name: str | None = None   # the target id resolved to a human-readable name: whose wallet, session, or volume


class AuditChainResult(BaseModel):
    """Optional integrity attestation appended when ``verify=true``."""

    ok: bool
    checked: int
    broken_at: str | None = None
    reason: str | None = None


class AuditLogList(BaseModel):
    """GET /audit-logs envelope (+ optional ``chain`` when verify=true)."""

    data: list[AuditLogEntry]
    pagination: PageMeta
    names: dict[str, str] = {}        # id-to-name map covering the detail, target, and actor, for display in the detail view
    chain: AuditChainResult | None = None
