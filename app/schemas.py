import datetime as dt
import uuid
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


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


class CourtUtilization(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    court_id: uuid.UUID
    court_name: str
    open_hours: int  # hours the court was open that week
    booked_hours: float  # hours covered by confirmed bookings
    utilization: float  # booked_hours / open_hours, between 0 and 1


class VenueUtilization(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    venue_id: uuid.UUID
    venue_name: str
    timezone: str
    week: str  # e.g. 2030-W02
    week_start: dt.date  # Monday, venue-local
    week_end: dt.date  # Sunday, venue-local
    courts: list[CourtUtilization]


class UtilizationOut(BaseModel):
    venues: list[VenueUtilization]


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class PeakWindowData(BaseModel):
    """A weekly peak-price window, in the venue's local time."""

    model_config = ConfigDict(from_attributes=True)

    weekday: int = Field(ge=0, le=6, description="Monday = 0 ... Sunday = 6")
    start_hour: int = Field(ge=0, le=23, description="Inclusive")
    end_hour: int = Field(ge=1, le=24, description="Exclusive")

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> "PeakWindowData":
        if self.end_hour <= self.start_hour:
            raise ValueError("end_hour must be after start_hour")
        return self


class PricingIn(BaseModel):
    offpeak_rate: int = Field(ge=0, description="Per hour, in minor units (paise)")
    peak_rate: int = Field(ge=0, description="Per hour, in minor units (paise)")
    peak_windows: list[PeakWindowData] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _windows_do_not_overlap(self) -> "PricingIn":
        by_day: dict[int, list[PeakWindowData]] = {}
        for window in self.peak_windows:
            by_day.setdefault(window.weekday, []).append(window)
        for windows in by_day.values():
            windows.sort(key=lambda w: w.start_hour)
            for earlier, later in zip(windows, windows[1:], strict=False):
                if later.start_hour < earlier.end_hour:
                    raise ValueError("peak windows on the same weekday must not overlap")
        return self


class VenueIn(PricingIn):
    name: Name
    timezone: str = Field(description="IANA timezone name, for example Asia/Kolkata")
    open_hour: int = Field(ge=0, le=23)
    close_hour: int = Field(ge=1, le=24, description="24 means midnight")

    @field_validator("timezone")
    @classmethod
    def _timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("not a valid IANA timezone, for example Asia/Kolkata") from None
        return value

    @model_validator(mode="after")
    def _closes_after_it_opens(self) -> "VenueIn":
        if self.close_hour <= self.open_hour:
            raise ValueError("close_hour must be after open_hour")
        return self


class CourtIn(BaseModel):
    name: Name


class MemberIn(BaseModel):
    name: Name


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class CourtOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    venue_id: uuid.UUID
    name: str


class VenueOut(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str
    open_hour: int
    close_hour: int
    offpeak_rate: int
    peak_rate: int
    peak_windows: list[PeakWindowData]
    courts: list[CourtOut]
