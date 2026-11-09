import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.models import Booking, BookingStatus
from app.policies import day_slots, price_for
from app.services.venues import load_venue_rules


@dataclass(frozen=True)
class Slot:
    start_at: datetime
    end_at: datetime
    status: str  # "free" or "unavailable"
    price: int


@dataclass(frozen=True)
class Availability:
    court_id: uuid.UUID
    date: date
    timezone: str
    slots: list[Slot]


def court_availability(
    db: Session, clock: Clock, court_id: uuid.UUID, day: date | None
) -> Availability:
    """Hourly slots for one venue-local day, each free or unavailable, with its price.

    A slot is unavailable if it has already started, or overlaps a confirmed booking,
    or overlaps a hold that has not expired. Nothing says WHO holds it. An expired hold
    shows as free, exactly as POST /holds would treat it.
    """
    rules = load_venue_rules(db, court_id)
    now = clock.now()
    local_day = day if day is not None else now.astimezone(rules.tz).date()

    slots = day_slots(rules, local_day)
    if not slots:
        return Availability(court_id, local_day, rules.timezone, [])

    window_start, window_end = slots[0][0], slots[-1][1]
    busy = db.execute(
        select(Booking.start_at, Booking.end_at).where(
            Booking.court_id == court_id,
            Booking.start_at < window_end,
            Booking.end_at > window_start,
            or_(
                Booking.status == BookingStatus.CONFIRMED,
                and_(Booking.status == BookingStatus.HELD, Booking.hold_expires_at > now),
            ),
        )
    ).all()

    result: list[Slot] = []
    for start, end in slots:
        taken = start <= now or any(b_start < end and b_end > start for b_start, b_end in busy)
        result.append(
            Slot(
                start_at=start.astimezone(rules.tz),
                end_at=end.astimezone(rules.tz),
                status="unavailable" if taken else "free",
                price=price_for(rules, start, end),
            )
        )
    return Availability(court_id, local_day, rules.timezone, result)
