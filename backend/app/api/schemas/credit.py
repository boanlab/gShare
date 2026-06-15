"""Credit/wallet schemas."""
from __future__ import annotations

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


class AllocationRequestCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    level: str = Field(default="user", pattern="^(user|group|org)$")
    group_id: str | None = None     # for level=user, the group funding it; for level=project, that group
    org_id: str | None = None         # level=org
    note: str | None = None


class AllocationRejectBody(BaseModel):
    reason: str


class AllocationEscalateBody(BaseModel):
    amount: Decimal | None = None     # when omitted, the escalated request uses the original amount
    note: str | None = None


class TransactionRead(ORMModel):
    id: str
    type: str                 # topup|hold|consume|refund|settle|adjust
    amount: Decimal
    balance_after: Decimal
    ref: str | None = None


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
    decided_by: str | None = None
    created_at: str                 # ISO 8601 string


class TopupRequestListResponse(BaseModel):
    data: list[TopupRequestRead]
