import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.clock import Clock
from app.errors import BookingLimitReached, HoldExpired, InvalidState, NotFound
from app.models import Booking, BookingStatus
from app.policies import can_add_bookings, price_for, validate_slot
from app.services.conflicts import flush_or_slot_unavailable
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
    db.add(hold)
    flush_or_slot_unavailable(db, "that court is already booked at that time")

    db.commit()
    return hold, True


def _load(db: Session, booking_id: uuid.UUID) -> Booking | None:
    # populate_existing: always read the row as it is in the database now, never a
    # copy this session loaded earlier.
    return db.scalar(
        select(Booking).where(Booking.id == booking_id).execution_options(populate_existing=True)
    )


def confirm_hold(db: Session, clock: Clock, member_id: uuid.UUID, hold_id: uuid.UUID) -> Booking:
    """Turn a live hold into a confirmed booking. Safe to retry.

    The whole decision is ONE guarded UPDATE: it only changes a row that is still
    held, still unexpired, not yet started, and owned by this member. Two requests
    cannot both succeed, because the second waits for the first and then no longer
    matches. If it changed nothing, a read tells us why:

    - already confirmed -> this is a retry: return the same booking, change nothing
    - expired           -> 410
    - cancelled         -> 409
    - missing or not yours -> 404
    """
    now = clock.now()
    changed = db.execute(
        update(Booking)
        .where(
            Booking.id == hold_id,
            Booking.member_id == member_id,
            Booking.status == BookingStatus.HELD,
            Booking.hold_expires_at > now,
            Booking.start_at > now,
        )
        .values(status=BookingStatus.CONFIRMED, confirmed_at=now)
        .returning(Booking.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()

    booking = _load(db, hold_id)
    if changed is not None and booking is not None:
        db.commit()
        return booking

    if booking is None or booking.member_id != member_id:
        raise NotFound("hold not found")
    if booking.status == BookingStatus.CONFIRMED:
        return booking  # a retry of a confirm that already worked
    if booking.status == BookingStatus.CANCELLED:
        raise InvalidState("this booking was cancelled")
    raise HoldExpired("this hold has expired; make a new one")
