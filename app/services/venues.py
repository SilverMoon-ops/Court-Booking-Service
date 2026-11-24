import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFound
from app.models import Court, PeakWindow, Venue
from app.policies import PeakRule, VenueRules


def load_venue_rules(db: Session, court_id: uuid.UUID) -> VenueRules:
    """Read a court's venue settings from the database as plain values for the policies."""
    court = db.get(Court, court_id)
    if court is None:
        raise NotFound("court not found")
    venue = db.get(Venue, court.venue_id)
    if venue is None:  # cannot happen: courts.venue_id is a foreign key
        raise NotFound("venue not found")
    return rules_for_venue(db, venue)


def rules_for_venue(db: Session, venue: Venue) -> VenueRules:
    windows = db.scalars(select(PeakWindow).where(PeakWindow.venue_id == venue.id)).all()
    return VenueRules(
        timezone=venue.timezone,
        open_hour=venue.open_hour,
        close_hour=venue.close_hour,
        offpeak_rate=venue.offpeak_rate,
        peak_rate=venue.peak_rate,
        peak_windows=tuple(PeakRule(w.weekday, w.start_hour, w.end_hour) for w in windows),
    )


@dataclass(frozen=True)
class CourtListing:
    id: uuid.UUID
    name: str
    venue_id: uuid.UUID
    venue_name: str
    timezone: str
    open_hour: int
    close_hour: int


def list_courts(db: Session, venue_id: uuid.UUID | None) -> list[CourtListing]:
    """Every court with its venue, so a member can pick one. Public information only."""
    if venue_id is not None and db.get(Venue, venue_id) is None:
        raise NotFound("venue not found")
    query = (
        select(Court, Venue)
        .join(Venue, Court.venue_id == Venue.id)
        .order_by(Venue.name, Court.name)
    )
    if venue_id is not None:
        query = query.where(Venue.id == venue_id)
    return [
        CourtListing(
            id=court.id,
            name=court.name,
            venue_id=venue.id,
            venue_name=venue.name,
            timezone=venue.timezone,
            open_hour=venue.open_hour,
            close_hour=venue.close_hour,
        )
        for court, venue in db.execute(query)
    ]
