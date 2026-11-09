import datetime as dt
import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict


class HoldRequest(BaseModel):
    court_id: uuid.UUID
    # AwareDatetime refuses a time without a timezone offset, instead of guessing one.
    start_at: AwareDatetime
    end_at: AwareDatetime


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    court_id: uuid.UUID
    member_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    status: str
    price: int
    refund_amount: int | None
    hold_expires_at: datetime | None
    confirmed_at: datetime | None
    cancelled_at: datetime | None


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class SlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_at: datetime  # venue-local time, with its offset
    end_at: datetime
    status: str  # "free" or "unavailable"
    price: int  # minor units (paise)


class AvailabilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    court_id: uuid.UUID
    date: dt.date  # the venue-local day
    timezone: str
    slots: list[SlotOut]
