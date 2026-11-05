import uuid
from datetime import UTC, datetime, timedelta

from psycopg.errors import DeadlockDetected, ExclusionViolation
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.clock import Clock
from app.errors import BookingLimitReached, SlotUnavailable
from app.models import Booking, BookingStatus
from app.policies import can_add_bookings, price_for, validate_slot
from app.services.limits import active_booking_count, lock_member
from app.services.venues import load_venue_rules


def create_hold(
    db: Session,
    clock: Clock,
    member_id: uuid.UUID,
    court_id: uuid.UUID,
    start_at: datetime,
    end_at: datetime,
    ttl: timedelta,
) -> tuple[Booking, bool]:
    """Reserve a slot briefly. Returns the hold and whether it was newly created.

    Everything happens in ONE transaction, committed at the end. Any error rolls
    all of it back, so a failed request leaves nothing behind.
    """
    rules = load_venue_rules(db, court_id)
    now = clock.now()
    validate_slot(rules, start_at, end_at, now)

    start, end = start_at.astimezone(UTC), end_at.astimezone(UTC)

    # 1. Serialise this member's requests (and make the limit check race-free).
    lock_member(db, member_id)

    # 2. A retry of the same request returns the hold the member already has.
    existing = db.scalar(
        select(Booking).where(
            Booking.member_id == member_id,
            Booking.court_id == court_id,
            Booking.start_at == start,
            Booking.end_at == end,
            Booking.status == BookingStatus.HELD,
            Booking.hold_expires_at > now,
        )
    )
    if existing is not None:
        return existing, False

    # 3. Expired holds must never block a slot. The exclusion constraint cannot see
    #    the clock, so mark overlapping expired holds as expired before inserting.
    db.execute(
        update(Booking)
        .where(
            Booking.court_id == court_id,
            Booking.status == BookingStatus.HELD,
            Booking.hold_expires_at <= now,
            Booking.start_at < end,
            Booking.end_at > start,
        )
        .values(status=BookingStatus.EXPIRED)
        .execution_options(synchronize_session=False)
    )

    # 4. The member may hold at most 3 active future bookings.
    if not can_add_bookings(active_booking_count(db, member_id, now)):
        raise BookingLimitReached("you already have 3 active bookings")

    # 5. Insert. The database constraint has the final say on double-booking.
    hold = Booking(
        member_id=member_id,
        court_id=court_id,
        start_at=start,
        end_at=end,
        status=BookingStatus.HELD,
        price=price_for(rules, start, end),
        hold_expires_at=now + ttl,
    )
    try:
        db.add(hold)
        db.flush()
    except IntegrityError as exc:
        if isinstance(exc.orig, ExclusionViolation):
            raise SlotUnavailable("that court is already booked at that time") from exc
        raise
    except OperationalError as exc:
        # When two inserts collide Postgres may stop one with a deadlock error
        # instead of an exclusion error. Either way the slot was taken.
        if isinstance(exc.orig, DeadlockDetected):
            raise SlotUnavailable("that court is already booked at that time") from exc
        raise

    db.commit()
    return hold, True
