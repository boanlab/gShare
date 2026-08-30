"""record_session_event — append one row to a session\'s lifecycle timeline.

Callers add it next to their publish_session_event() call; it never raises (a failed log line
must not break admission or settlement) and never commits — the caller\'s unit of work owns that.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ids
from app.core.logging import get_logger
from app.db.models import SessionEvent

log = get_logger(__name__)


def record_session_event(
    db: AsyncSession,
    session_id: str,
    kind: str,
    reason: str | None = None,
    message: str | None = None,
) -> None:
    try:
        db.add(SessionEvent(
            id=ids.new("sessionevent"), session_id=session_id, kind=kind,
            reason=reason, message=(message or None) and str(message)[:500],
        ))
    except Exception:  # noqa: BLE001 — advisory log only
        log.warning("session event skipped session=%s kind=%s", session_id, kind)
