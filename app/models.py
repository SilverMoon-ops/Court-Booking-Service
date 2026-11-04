import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

__all__ = [
    "ACTIVE_STATUSES",
    "Base",
    "Booking",
    "BookingStatus",
    "Court",
    "Member",
    "PeakWindow",
    "RecurringSeries",
    "Venue",
]


class BookingStatus(enum.StrEnum):
    HELD = "held"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


# Statuses that occupy a court. The exclusion constraint (step 4) uses the same list.
ACTIVE_STATUSES = (BookingStatus.HELD, BookingStatus.CONFIRMED)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class Venue(Base):
    __tablename__ = "venues"
    __table_args__ = (
        CheckConstraint("length(timezone) > 0", name="timezone_not_empty"),
        CheckConstraint("open_hour >= 0 AND open_hour <= 23", name="open_hour_range"),
        CheckConstraint("close_hour >= 1 AND close_hour <= 24", name="close_hour_range"),
        CheckConstraint("close_hour > open_hour", name="closes_after_open"),
        CheckConstraint("offpeak_rate >= 0 AND peak_rate >= 0", name="rates_not_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), unique=True)
    timezone: Mapped[str] = mapped_column(String(64))  # IANA name, e.g. "Asia/Kolkata"
    open_hour: Mapped[int] = mapped_column(SmallInteger)  # venue-local
    close_hour: Mapped[int] = mapped_column(SmallInteger)  # venue-local, 24 = midnight
    offpeak_rate: Mapped[int] = mapped_column(Integer)  # minor units (paise) per hour
    peak_rate: Mapped[int] = mapped_column(Integer)


class PeakWindow(Base):
    __tablename__ = "peak_windows"
    __table_args__ = (
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="weekday_range"),
        CheckConstraint("start_hour >= 0 AND start_hour <= 23", name="start_hour_range"),
        CheckConstraint("end_hour >= 1 AND end_hour <= 24", name="end_hour_range"),
        CheckConstraint("end_hour > start_hour", name="ends_after_start"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id"), index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)  # Monday = 0 ... Sunday = 6
    start_hour: Mapped[int] = mapped_column(SmallInteger)  # venue-local
    end_hour: Mapped[int] = mapped_column(SmallInteger)  # venue-local, exclusive


class Court(Base):
    __tablename__ = "courts"
    __table_args__ = (UniqueConstraint("venue_id", "name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))


class Member(Base):
    __tablename__ = "members"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100))


class RecurringSeries(Base):
    __tablename__ = "recurring_series"
    __table_args__ = (
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="weekday_range"),
        CheckConstraint("start_hour >= 0 AND start_hour <= 23", name="start_hour_range"),
        CheckConstraint("duration_hours >= 1 AND duration_hours <= 3", name="duration_range"),
        CheckConstraint("occurrences >= 1 AND occurrences <= 12", name="occurrences_range"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id"), index=True)
    court_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courts.id"))
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_hour: Mapped[int] = mapped_column(SmallInteger)  # venue-local
    duration_hours: Mapped[int] = mapped_column(SmallInteger)
    first_date: Mapped[date]  # venue-local date of the first occurrence
    occurrences: Mapped[int] = mapped_column(SmallInteger)


class Booking(Base):
    """A hold and a booking are the same row; status tells them apart."""

    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint(
            "status IN ('held', 'confirmed', 'cancelled', 'expired')",
            name="status_valid",
        ),
        CheckConstraint(
            "(end_at - start_at) IN (interval '1 hour', interval '2 hours', interval '3 hours')",
            name="duration_one_to_three_hours",
        ),
        CheckConstraint("price >= 0", name="price_not_negative"),
        CheckConstraint(
            "refund_amount IS NULL OR (refund_amount >= 0 AND refund_amount <= price)",
            name="refund_within_price",
        ),
        CheckConstraint(
            "status <> 'held' OR hold_expires_at IS NOT NULL",
            name="held_has_expiry",
        ),
        # The rule against overlapping bookings is the exclusion constraint
        # ex_bookings_no_overlap. It is created in migration 0003 and is not
        # declared here because SQLAlchemy's ExcludeConstraint has no type hints,
        # which strict mypy rejects.
        Index("ix_bookings_member_status_start", "member_id", "status", "start_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id"))
    court_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courts.id"))
    series_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recurring_series.id"), index=True
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    price: Mapped[int] = mapped_column(Integer)  # snapshot taken when the hold is created
    refund_amount: Mapped[int | None] = mapped_column(Integer)
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
