import uuid

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
