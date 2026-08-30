"""Credit/wallet schemas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, computed_field

from app.api.schemas.common import ORMModel


class WalletRead(ORMModel):
    id: str
    owner_type: str
    owner_id: str
    balance: Decimal
    reserved: Decimal
    monthly_grant: Decimal = Decimal("0")   # monthly refill amount; 0 disables refills
    owner_name: str | None = None           # owner name (user, group, or organization), for display

    @computed_field  # available = balance - reserved. Computed here so the wallet screen and the
    # dashboard show the same number.
    @property
    def available(self) -> Decimal:
        return self.balance - self.reserved


class MonthlyGrantBody(BaseModel):
    # Set a child wallet's monthly refill, from the administrator one level up. 0 disables it.
    amount: Decimal = Field(ge=0)


class TopupRequestBody(BaseModel):
    amount: Decimal = Field(gt=0)
    note: str | None = None
    wallet_id: str | None = None   # the console sends it in the body; absent means the caller's personal wallet


class AdjustBody(BaseModel):
    amount: Decimal           # signed
    reason: str


class TransferBody(BaseModel):
    to_wallet_id: str
    amount: Decimal = Field(gt=0)


class AllocateBody(BaseModel):
    # Hierarchical allocation and reclaim: organization to project for an org_admin, project to user
    # for a group_admin. super_admin may do either.
    from_wallet_id: str
    to_wallet_id: str
    amount: Decimal = Field(gt=0)
    reason: str | None = None


class BulkAllocateBody(BaseModel):
    # Allocate the same amount from the group's wallet to EVERY member's personal wallet in one
    # idempotent operation — the start-of-term "give the whole class N credits" action.
    group_id: str
    amount: Decimal = Field(gt=0)
    reason: str | None = None


class BulkMonthlyGrantBody(BaseModel):
    # Set the same monthly refill on every member wallet of a group. 0 disables it.
    group_id: str
    amount: Decimal = Field(ge=0)


class AllocationRequestCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    level: str = Field(default="user", pattern="^(user|group|org)$")
    group_id: str | None = None     # for level=user, the group funding it; for level=project, that group
    org_id: str | None = None         # level=org
    note: str | None = None


class AllocationRejectBody(BaseModel):
    reason: str


class TransactionRead(ORMModel):
    id: str
    type: str                 # topup|hold|consume|refund|settle|adjust
    amount: Decimal
    balance_after: Decimal
    ref: str | None = None
    # Human name for the ref: the session's name for session refs, a short label otherwise -
    # so the ledger reads "which session spent this", not an opaque ULID.
    ref_name: str | None = None
    created_at: datetime | None = None
    # Set when this row is a rollup of several transactions (per-minute consume for one session).
    # entry_count == 1 means a single transaction and the period fields are absent.
    entry_count: int = 1
    period_start: datetime | None = None
    period_end: datetime | None = None
    # The session's billing is closed (its zero-amount settle marker folded into this row).
    settled: bool = False
    # The stream behind this rollup is still accruing: the session is running (consume) or the
    # volume still exists (storage bills for provisioned capacity until deletion).
    live: bool = False


class TopupRejectBody(BaseModel):
    reason: str = Field(min_length=1)   # rejection reason; required, persisted, and included in the notification and audit entry


class TopupRequestRead(BaseModel):
    # Mirrors the serialised shape of GET /credits/topup-requests, which is assembled as a dict
    # rather than mapped from the ORM.
    id: str
    wallet_id: str
    amount: str                     # serialised as a string, from Decimal
    status: str
    requester_id: str | None = None
    requester_name: str | None = None
    note: str | None = None            # requester's justification
    wallet_owner_type: str | None = None   # user|group — a member's own top-up vs group funding
    wallet_owner_name: str | None = None
    decided_reason: str | None = None  # approver's note / rejection reason
    decided_by: str | None = None
    created_at: str                 # ISO 8601 string


class TopupRequestListResponse(BaseModel):
    data: list[TopupRequestRead]


class SpendDayRead(BaseModel):
    """One day of spend (consume + storage), for the wallet's usage chart."""
    date: str      # YYYY-MM-DD in the caller's timezone (tz_offset_min)
    amount: float  # credits drained that day, absolute
