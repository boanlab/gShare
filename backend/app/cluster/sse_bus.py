"""Session SSE bus — publishes state changes over Redis pub/sub.

The channel is ``session:events:{session_id}``, which the session detail view subscribes to; the
administrators' monitoring screen subscribes to the ``session:events:*`` pattern instead. The
payload carries no ``event`` key, so the shared _sse_events helper emits the default message event
and the browser receives it through EventSource.onmessage. Publishing is best-effort: a Redis
failure must never block a state transition. """
from __future__ import annotations

import json

from app.core.redis import get_redis


async def publish_session_event(session_id: str, payload: dict) -> None:
    try:
        await get_redis().publish(f"session:events:{session_id}", json.dumps(payload))
    except Exception:  # noqa: BLE001
        pass
