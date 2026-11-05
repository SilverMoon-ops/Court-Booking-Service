import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import NotFound
from app.models import Booking, BookingStatus, Member


def lock_member(db: Session, member_id: uuid.UUID) -> None:
    """Take a row lock on the member until the transaction ends.

    Two requests from the same member then run one after the other, so each sees
    the other's bookings when it counts. Without this, two parallel requests could
    both read "2 bookings" and both add a third. Always lock the member BEFORE
    inserting bookings, so the lock order is the same everywhere.
    """
    found = db.scalar(select(Member.id).where(Member.id == member_id).with_for_update())
    if found is None:
        raise NotFound("member not found")


def active_booking_count(db: Session, member_id: uuid.UUID, now: datetime) -> int:
    """Bookings that count towards the limit of 3.

    - confirmed single bookings that have not started yet
    - each recurring series with a future occurrence counts once, not per week
    - unexpired holds (otherwise holds could be used to get round the limit)
    """
    mine = Booking.member_id == member_id
    singles = db.scalar(
        select(func.count())
        .select_from(Booking)
        .where(
            mine,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.series_id.is_(None),
            Booking.start_at > now,
        )
    )
    series = db.scalar(
        select(func.count(func.distinct(Booking.series_id))).where(
            mine,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.series_id.is_not(None),
            Booking.start_at > now,
        )
    )
    holds = db.scalar(
        select(func.count())
        .select_from(Booking)
        .where(mine, Booking.status == BookingStatus.HELD, Booking.hold_expires_at > now)
    )
    return (singles or 0) + (series or 0) + (holds or 0)
