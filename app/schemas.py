import datetime as dt
import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


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
    series_id: uuid.UUID | None
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


class RecurringRequest(BaseModel):
    """The first booking of the series. It then repeats every week at the same local time."""

    court_id: uuid.UUID
    start_at: AwareDatetime
    end_at: AwareDatetime
    occurrences: int = Field(ge=1, le=12, description="How many weeks, including the first")


class SeriesOut(BaseModel):
    id: uuid.UUID
    court_id: uuid.UUID
    member_id: uuid.UUID
    weekday: int  # Monday = 0
    start_hour: int  # venue-local
    duration_hours: int
    first_date: dt.date  # venue-local
    occurrences: int
    bookings: list[BookingOut]
