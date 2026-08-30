"""Prometheus metrics for the API process.

Mounted at GET /metrics by create_app. The route is unauthenticated but deliberately NOT exposed
through the public ingress — Prometheus scrapes the pod directly (the chart adds the scrape
annotations). Labels are the ROUTE TEMPLATE (``/api/v1/sessions/{session_id}``), never the raw
path, so cardinality stays bounded.
"""
from __future__ import annotations

import time

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REQUEST_LATENCY = Histogram(
    "gshare_http_request_duration_seconds",
    "API request latency by route template and status class.",
    labelnames=("route", "method", "status"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
SSE_STREAMS = Gauge(
    "gshare_sse_streams",
    "Currently open SSE streams (notification bell + session events + admin monitor).",
)
QUEUE_DEPTH = Gauge(
    "gshare_queue_depth",
    "Queued GPU sessions (updated by the queue ticker and queue reads).",
)
WORKER_JOB_FAILURES = Counter(
    "gshare_worker_job_failures_total",
    "Background worker job failures.",
    labelnames=("job",),
)


def instrument(app: FastAPI) -> None:
    """Attach the request-latency middleware and the /metrics route."""

    @app.middleware("http")
    async def _timing(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", None)
        # Unrouted paths (404 scans) fall into one bucket so they cannot explode cardinality.
        REQUEST_LATENCY.labels(
            route=template or "unmatched",
            method=request.method,
            status=f"{response.status_code // 100}xx",
        ).observe(time.perf_counter() - start)
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
