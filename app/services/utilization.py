import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.errors import NotFound
from app.models import Booking, BookingStatus, Court, Venue
from app.policies import (
    day_slots,
    iso_week_label,
    monday_of,
    parse_iso_week,
    week_window,
)
from app.services.venues import rules_for_venue


@dataclass(frozen=True)
class CourtReport:
    court_id: uuid.UUID
    court_name: str
    open_hours: int
    booked_hours: float
    utilization: float


@dataclass(frozen=True)
class VenueReport:
    venue_id: uuid.UUID
    venue_name: str
    timezone: str
    week: str
    week_start: date
    week_end: date
    courts: list[CourtReport]


def weekly_utilization(
    db: Session, clock: Clock, week: str | None, venue_id: uuid.UUID | None
) -> list[VenueReport]:
    """For each court: booked hours divided by open hours, for one Monday-to-Sunday week.

    - Only CONFIRMED bookings count. Holds, cancelled and expired ones do not.
    - The week runs from Monday 00:00 to the next Monday 00:00 in the VENUE's timezone.
    - "Open hours" are the hours the venue is open that week (a daylight-saving change can
      make a week one hour shorter or longer).
    - With no `week`, each venue reports its own current week.
    """
    chosen_monday = parse_iso_week(week) if week is not None else None
    now = clock.now()

    venues_query = select(Venue).order_by(Venue.name)
    if venue_id is not None:
        venues_query = venues_query.where(Venue.id == venue_id)
    venues = list(db.scalars(venues_query))
    if venue_id is not None and not venues:
        raise NotFound("venue not found")

    reports: list[VenueReport] = []
    for venue in venues:
        rules = rules_for_venue(db, venue)
        monday = chosen_monday or monday_of(now.astimezone(rules.tz).date())
        start, end = week_window(rules, monday)
        open_hours = sum(len(day_slots(rules, monday + timedelta(days=i))) for i in range(7))

        courts = list(
            db.scalars(select(Court).where(Court.venue_id == venue.id).order_by(Court.name))
        )
        booked: dict[uuid.UUID, float] = {court.id: 0.0 for court in courts}
        rows = db.execute(
            select(Booking.court_id, Booking.start_at, Booking.end_at).where(
                Booking.court_id.in_(booked),
                Booking.status == BookingStatus.CONFIRMED,
                Booking.start_at < end,
                Booking.end_at > start,
            )
        ).all()
        for court_id, booking_start, booking_end in rows:
            overlap = min(booking_end, end) - max(booking_start, start)
            booked[court_id] += overlap.total_seconds() / 3600

        reports.append(
            VenueReport(
                venue_id=venue.id,
                venue_name=venue.name,
                timezone=venue.timezone,
                week=iso_week_label(monday),
                week_start=monday,
                week_end=monday + timedelta(days=6),
                courts=[
                    CourtReport(
                        court_id=court.id,
                        court_name=court.name,
                        open_hours=open_hours,
                        booked_hours=round(booked[court.id], 2),
                        utilization=round(booked[court.id] / open_hours, 4) if open_hours else 0.0,
                    )
                    for court in courts
                ],
            )
        )
    return reports
