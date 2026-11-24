import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.errors import NotFound
from app.models import Booking, BookingStatus, Member

StatusName = Literal["held", "confirmed", "cancelled", "expired"]


def effective_status(booking: Booking, now: datetime) -> str:
    """A hold whose time is up is shown as expired, even if no request has tidied the row yet."""
    if (
        booking.status == BookingStatus.HELD
        and booking.hold_expires_at is not None
        and booking.hold_expires_at <= now
    ):
        return BookingStatus.EXPIRED.value
    return booking.status


def member_bookings(
    db: Session, clock: Clock, member_id: uuid.UUID, status: StatusName | None, limit: int
) -> list[tuple[Booking, str]]:
    """A member's own holds and bookings, earliest first, each with its true current status."""
    if db.get(Member, member_id) is None:
        raise NotFound("member not found")
    now = clock.now()

    query = select(Booking).where(Booking.member_id == member_id)
    live_hold = and_(Booking.status == BookingStatus.HELD, Booking.hold_expires_at > now)
    timed_out_hold = and_(Booking.status == BookingStatus.HELD, Booking.hold_expires_at <= now)
    if status == "held":
        query = query.where(live_hold)
    elif status == "expired":
        query = query.where(or_(Booking.status == BookingStatus.EXPIRED, timed_out_hold))
    elif status is not None:
        query = query.where(Booking.status == status)

    rows = db.scalars(
        query.order_by(Booking.start_at, Booking.id)
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    return [(booking, effective_status(booking, now)) for booking in rows]
