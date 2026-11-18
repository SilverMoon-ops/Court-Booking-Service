import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from psycopg.errors import UniqueViolation
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AlreadyExists, NotFound
from app.models import Court, Member, PeakWindow, Venue

Window = tuple[int, int, int]  # (weekday, start_hour, end_hour)


@dataclass(frozen=True)
class VenueDetail:
    venue: Venue
    courts: list[Court]
    peak_windows: list[PeakWindow]


def _commit_or_already_exists(db: Session, message: str) -> None:
    """Commit; turn "that name is taken" into a 409.

    The unique constraints in the database decide, so two requests for the same name at the
    same instant cannot both succeed.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        if isinstance(exc.orig, UniqueViolation):
            raise AlreadyExists(message) from exc
        raise


def _flush_or_already_exists(db: Session, message: str) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        if isinstance(exc.orig, UniqueViolation):
            raise AlreadyExists(message) from exc
        raise


def _windows(venue_id: uuid.UUID, windows: Sequence[Window]) -> list[PeakWindow]:
    return [
        PeakWindow(venue_id=venue_id, weekday=w, start_hour=s, end_hour=e) for w, s, e in windows
    ]


def create_venue(
    db: Session,
    name: str,
    timezone: str,
    open_hour: int,
    close_hour: int,
    offpeak_rate: int,
    peak_rate: int,
    windows: Sequence[Window],
) -> VenueDetail:
    """Create a venue and its peak windows together, or neither."""
    venue = Venue(
        name=name,
        timezone=timezone,
        open_hour=open_hour,
        close_hour=close_hour,
        offpeak_rate=offpeak_rate,
        peak_rate=peak_rate,
    )
    db.add(venue)
    _flush_or_already_exists(db, f"a venue named {name!r} already exists")
    peak_windows = _windows(venue.id, windows)
    db.add_all(peak_windows)
    db.commit()
    return VenueDetail(venue, [], peak_windows)


def add_court(db: Session, venue_id: uuid.UUID, name: str) -> Court:
    if db.get(Venue, venue_id) is None:
        raise NotFound("venue not found")
    court = Court(venue_id=venue_id, name=name)
    db.add(court)
    _commit_or_already_exists(db, f"this venue already has a court named {name!r}")
    return court


def create_member(db: Session, name: str) -> Member:
    member = Member(name=name)
    db.add(member)
    db.commit()
    return member


def set_pricing(
    db: Session, venue_id: uuid.UUID, offpeak_rate: int, peak_rate: int, windows: Sequence[Window]
) -> VenueDetail:
    """Replace a venue's rates and peak windows as one unit.

    The venue row is locked first, so two updates at the same moment run one after the other
    and the result is always one complete request, never a mixture of two.
    Existing holds and bookings keep the price they were given.
    """
    venue = db.scalar(select(Venue).where(Venue.id == venue_id).with_for_update())
    if venue is None:
        raise NotFound("venue not found")
    venue.offpeak_rate = offpeak_rate
    venue.peak_rate = peak_rate
    db.execute(delete(PeakWindow).where(PeakWindow.venue_id == venue_id))
    db.add_all(_windows(venue_id, windows))
    db.commit()
    # Answer from what is actually stored, not from what was just sent.
    courts = list(db.scalars(select(Court).where(Court.venue_id == venue_id).order_by(Court.name)))
    stored = list(
        db.scalars(
            select(PeakWindow)
            .where(PeakWindow.venue_id == venue_id)
            .order_by(PeakWindow.weekday, PeakWindow.start_hour)
            .execution_options(populate_existing=True)
        )
    )
    return VenueDetail(venue, courts, stored)


def list_venues(db: Session) -> list[VenueDetail]:
    venues = list(db.scalars(select(Venue).order_by(Venue.name)))
    ids = [v.id for v in venues]
    courts = db.scalars(select(Court).where(Court.venue_id.in_(ids)).order_by(Court.name)).all()
    windows = db.scalars(
        select(PeakWindow)
        .where(PeakWindow.venue_id.in_(ids))
        .order_by(PeakWindow.weekday, PeakWindow.start_hour)
    ).all()
    return [
        VenueDetail(
            venue=v,
            courts=[c for c in courts if c.venue_id == v.id],
            peak_windows=[w for w in windows if w.venue_id == v.id],
        )
        for v in venues
    ]
