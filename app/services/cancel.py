import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.errors import HoldExpired, InvalidState, NotFound
from app.models import Booking, BookingStatus
from app.policies import refund_for


def cancel_booking(
    db: Session, clock: Clock, member_id: uuid.UUID, booking_id: uuid.UUID
) -> Booking:
    """Cancel a booking or release a hold. Safe to retry.

    The row is locked first (SELECT ... FOR UPDATE), then everything is decided from
    what the row says now. Two requests for the same booking run one after the other:
    the second finds it already cancelled and returns that result unchanged.
    That also protects a cancel racing a confirm: whichever gets the lock first
    wins, and the other sees the outcome and behaves accordingly.
    """
    now = clock.now()
    booking = db.scalar(
        select(Booking)
        .where(Booking.id == booking_id, Booking.member_id == member_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if booking is None:
        raise NotFound("booking not found")  # missing, or not this member's

    # A retry gets the original result back. The refund is NOT worked out again,
    # because the 24-hour window may have closed since the first cancel.
    if booking.status == BookingStatus.CANCELLED:
        return booking

    expires = booking.hold_expires_at
    if booking.status == BookingStatus.EXPIRED or (
        booking.status == BookingStatus.HELD and (expires is None or expires <= now)
    ):
        raise HoldExpired("this hold has already expired")

    if booking.start_at <= now:
        raise InvalidState("a booking that has already started cannot be cancelled")

    # An unconfirmed hold was never paid for, so there is nothing to refund.
    paid = booking.status == BookingStatus.CONFIRMED
    booking.refund_amount = refund_for(booking.price, booking.start_at, now) if paid else 0
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now
    db.commit()
    return booking
