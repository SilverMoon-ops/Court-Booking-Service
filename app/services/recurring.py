import uuid
from datetime import datetime

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.orm import Session

from app.clock import Clock
from app.errors import BookingLimitReached, SlotUnavailable
from app.models import Booking, BookingStatus, RecurringSeries
from app.policies import HOUR, can_add_bookings, price_for, validate_slot, weekly_occurrences
from app.services.conflicts import flush_or_slot_unavailable
from app.services.limits import active_booking_count, lock_member
from app.services.venues import load_venue_rules


def create_recurring(
    db: Session,
    clock: Clock,
    member_id: uuid.UUID,
    court_id: uuid.UUID,
    start_at: datetime,
    end_at: datetime,
    occurrences: int,
) -> tuple[RecurringSeries, list[Booking], bool]:
    """Book the same court at the same local time every week, all or nothing.

    Every week is checked and inserted inside ONE transaction. If any week is taken, or
    anything else goes wrong before the final commit, nothing at all is saved.
    Returns the series, its bookings, and whether it was newly created.
    """
    rules = load_venue_rules(db, court_id)
    now = clock.now()
    validate_slot(rules, start_at, end_at, now)  # the first week

    local_start = start_at.astimezone(rules.tz)
    duration_hours = (end_at.astimezone(local_start.tzinfo) - local_start) // HOUR
    starts = weekly_occurrences(rules, local_start.date(), local_start.hour, occurrences)
    windows = [(s, s + duration_hours * HOUR) for s in starts]
    for start, end in windows[1:]:
        validate_slot(rules, start, end, now)  # every later week too

    # 1. Serialise this member's requests, as for a single hold.
    lock_member(db, member_id)

    # 2. A retry of the identical request returns the series that already exists.
    same = (
        RecurringSeries.member_id == member_id,
        RecurringSeries.court_id == court_id,
        RecurringSeries.weekday == local_start.weekday(),
        RecurringSeries.start_hour == local_start.hour,
        RecurringSeries.duration_hours == duration_hours,
        RecurringSeries.first_date == local_start.date(),
        RecurringSeries.occurrences == occurrences,
    )
    existing = db.scalar(
        select(RecurringSeries)
        .where(
            *same,
            exists().where(
                Booking.series_id == RecurringSeries.id,
                Booking.status == BookingStatus.CONFIRMED,
            ),
        )
        .limit(1)
    )
    if existing is not None:
        return existing, _series_bookings(db, existing.id), False

    # 3. Expired holds in the way must not block any week.
    overlaps_a_week = or_(*[and_(Booking.start_at < e, Booking.end_at > s) for s, e in windows])
    db.execute(
        update(Booking)
        .where(
            Booking.court_id == court_id,
            Booking.status == BookingStatus.HELD,
            Booking.hold_expires_at <= now,
            overlaps_a_week,
        )
        .values(status=BookingStatus.EXPIRED)
        .execution_options(synchronize_session=False)
    )

    # 4. A whole series counts as ONE of the member's three bookings.
    if not can_add_bookings(active_booking_count(db, member_id, now)):
        raise BookingLimitReached("you already have 3 active bookings")

    # 5. Tell the member which weeks are taken (the constraint below is the real guard).
    busy = db.execute(
        select(Booking.start_at, Booking.end_at).where(
            Booking.court_id == court_id,
            overlaps_a_week,
            or_(
                Booking.status == BookingStatus.CONFIRMED,
                and_(Booking.status == BookingStatus.HELD, Booking.hold_expires_at > now),
            ),
        )
    ).all()
    taken = [
        start.astimezone(rules.tz).date().isoformat()
        for start, end in windows
        if any(b_start < end and b_end > start for b_start, b_end in busy)
    ]
    if taken:
        raise SlotUnavailable(
            f"nothing was booked; the court is already taken on: {', '.join(taken)}"
        )

    # 6. Insert the series and every week, then commit once.
    series = RecurringSeries(
        member_id=member_id,
        court_id=court_id,
        weekday=local_start.weekday(),
        start_hour=local_start.hour,
        duration_hours=duration_hours,
        first_date=local_start.date(),
        occurrences=occurrences,
    )
    db.add(series)
    db.flush()
    bookings = [
        Booking(
            member_id=member_id,
            court_id=court_id,
            series_id=series.id,
            start_at=start,
            end_at=end,
            status=BookingStatus.CONFIRMED,
            price=price_for(rules, start, end),
            confirmed_at=now,
        )
        for start, end in windows
    ]
    db.add_all(bookings)
    flush_or_slot_unavailable(db, "nothing was booked; one of the weeks was taken a moment ago")
    db.commit()
    return series, bookings, True


def _series_bookings(db: Session, series_id: uuid.UUID) -> list[Booking]:
    return list(
        db.scalars(
            select(Booking)
            .where(Booking.series_id == series_id)
            .order_by(Booking.start_at)
            .execution_options(populate_existing=True)
        )
    )
