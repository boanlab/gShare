"""Error envelope + domain exceptions + FastAPI exception handlers.

Envelope shape::

    {"error": {"code", "message", "details", "request_id", "timestamp"}}

Domain exception -> HTTP/code mapping:
    InsufficientCredit -> 402 insufficient_credit
    BudgetExceeded -> 409 budget_exceeded
    QuotaExceeded -> 409 quota_exceeded
    NoCapacity -> 409 no_capacity
    InvalidStateTransition -> 409 invalid_state_transition
    IdempotencyConflict -> 409 idempotency_key_conflict
    IdempotencyInProgress -> 409 idempotency_in_progress
    IdempotencyKeyRequired -> 400 idempotency_key_required
    Unauthenticated -> 401 unauthenticated
    Forbidden -> 403 forbidden
    NotFound -> 404 not_found
    (pydantic ValidationError) -> 422 validation_failed
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class DomainError(Exception):
    """Base domain error carrying code/http/message/details (-> envelope)."""

    code: str = "internal_error"
    http: int = 500

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None):
        self.message = message or self.code
        self.details = details or {}
        super().__init__(self.message)


# ── Credit / budget ──
class InsufficientCredit(DomainError):
    code, http = "insufficient_credit", 402

    def __init__(self, available: Any = None, need: Any = None):
        super().__init__("insufficient credit", {"available": str(available), "need": str(need)})


class BudgetExceeded(DomainError):
    code, http = "budget_exceeded", 409


# ── Scheduling / capacity ──
class QuotaExceeded(DomainError):
    code, http = "quota_exceeded", 409


class NoCapacity(DomainError):
    code, http = "no_capacity", 409


class ImbalancedAllocation(DomainError):
    # When a fractional request's core and VRAM shares diverge too far — 1 GB with 100% of the
    # cores, say — it makes the physical GPU useless for compute to every other session on it.
    # Rejected as anti-fragmentation.
    code, http = "imbalanced_allocation", 422


class VramBelowMinimum(DomainError):
    # A fractional slice below GPU_MIN_FRACTIONAL_MEM_MB cannot even hold a CUDA context, so it is
    # unusable in practice. The server-side counterpart to the console disabling tiers under 1 GB
    # for the selected GPU.
    code, http = "vram_below_minimum", 422


class IncompatibleImage(DomainError):
    # The image's CUDA version is below the selected offering's minimum, so it cannot run on that
    # card.
    code, http = "incompatible_image", 422


# ── Lifecycle / idempotency ──
class InvalidStateTransition(DomainError):
    code, http = "invalid_state_transition", 409


class IdempotencyConflict(DomainError):
    code, http = "idempotency_key_conflict", 409


class IdempotencyInProgress(DomainError):
    code, http = "idempotency_in_progress", 409


class IdempotencyKeyRequired(DomainError):
    code, http = "idempotency_key_required", 400


# ── Auth ──
class Unauthenticated(DomainError):
    code, http = "unauthenticated", 401


class Forbidden(DomainError):
    code, http = "forbidden", 403


class PasswordChangeRequired(DomainError):
    # Blocks every API call with 403 until the password is changed after a first login or a reset.
    # The console keys off this code to send the user to the change-password screen.
    code, http = "password_change_required", 403


class NotFound(DomainError):
    code, http = "not_found", 404


def register_exception_handlers(app: FastAPI) -> None:
    """Install envelope handlers on the app."""

    @app.exception_handler(DomainError)
    async def _domain(req: Request, exc: DomainError):  # noqa: ANN202
        return JSONResponse(
            status_code=exc.http,
            content={"error": {
                "code": exc.code, "message": exc.message, "details": exc.details,
                "request_id": getattr(req.state, "request_id", None),
                "timestamp": _utcnow_iso(),
            }},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(req: Request, exc: RequestValidationError):  # noqa: ANN202
        return JSONResponse(
            status_code=422,
            content={"error": {
                "code": "validation_failed", "message": "validation failed",
                "details": {"errors": exc.errors()},
                "request_id": getattr(req.state, "request_id", None),
                "timestamp": _utcnow_iso(),
            }},
        )
