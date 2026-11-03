from datetime import UTC, datetime, timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import engine
from app.models import Base, Booking, BookingStatus, Court, Member, Venue

START = datetime(2030, 1, 7, 10, 0, tzinfo=UTC)


def make_court(db: Session) -> Court:
    venue = Venue(
        name="Main Hall",
        timezone="Asia/Kolkata",
        open_hour=6,
        close_hour=22,
        offpeak_rate=20000,
        peak_rate=40000,
    )
    db.add(venue)
    db.flush()
    court = Court(venue_id=venue.id, name="Court 1")
    db.add(court)
    db.flush()
    return court


def make_booking(
    court: Court,
    member: Member,
    *,
    status: str = BookingStatus.CONFIRMED,
    hours: int = 1,
    hold_expires_at: datetime | None = None,
) -> Booking:
    return Booking(
        member_id=member.id,
        court_id=court.id,
        start_at=START,
        end_at=START + timedelta(hours=hours),
        status=status,
        price=20000,
        hold_expires_at=hold_expires_at,
    )


def test_all_tables_exist(migrated_db: None) -> None:
    tables = set(inspect(engine).get_table_names())
    assert {
        "venues",
        "peak_windows",
        "courts",
        "members",
        "recurring_series",
        "bookings",
    } <= tables


def test_models_match_migrations(migrated_db: None) -> None:
    """If a model changes without a new migration, this fails."""
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_valid_booking_is_stored(session: Session) -> None:
    court = make_court(session)
    member = Member(name="Asha")
    session.add(member)
    session.flush()
    booking = make_booking(court, member)
    session.add(booking)
    session.flush()
    assert booking.id is not None


@pytest.mark.parametrize(
    "hours",
    [0, 4],
    ids=["zero-length", "too-long"],
)
def test_booking_duration_must_be_one_to_three_hours(session: Session, hours: int) -> None:
    court = make_court(session)
    member = Member(name="Asha")
    session.add(member)
    session.flush()
    bad = make_booking(court, member, hours=hours)
    session.add(bad)
    with pytest.raises(IntegrityError, match="duration_one_to_three_hours"):
        session.flush()


def test_booking_cannot_last_ninety_minutes(session: Session) -> None:
    court = make_court(session)
    member = Member(name="Asha")
    session.add(member)
    session.flush()
    bad = make_booking(court, member)
    bad.end_at = START + timedelta(minutes=90)
    session.add(bad)
    with pytest.raises(IntegrityError, match="duration_one_to_three_hours"):
        session.flush()


def test_held_booking_needs_an_expiry(session: Session) -> None:
    court = make_court(session)
    member = Member(name="Asha")
    session.add(member)
    session.flush()
    session.add(make_booking(court, member, status=BookingStatus.HELD))
    with pytest.raises(IntegrityError, match="held_has_expiry"):
        session.flush()


def test_unknown_status_is_rejected(session: Session) -> None:
    court = make_court(session)
    member = Member(name="Asha")
    session.add(member)
    session.flush()
    session.add(make_booking(court, member, status="pending"))
    with pytest.raises(IntegrityError, match="status_valid"):
        session.flush()


def test_refund_cannot_exceed_price(session: Session) -> None:
    court = make_court(session)
    member = Member(name="Asha")
    session.add(member)
    session.flush()
    booking = make_booking(court, member)
    booking.refund_amount = booking.price + 1
    session.add(booking)
    with pytest.raises(IntegrityError, match="refund_within_price"):
        session.flush()


def test_venue_must_close_after_it_opens(session: Session) -> None:
    session.add(
        Venue(
            name="Backwards",
            timezone="Asia/Kolkata",
            open_hour=20,
            close_hour=8,
            offpeak_rate=1,
            peak_rate=1,
        )
    )
    with pytest.raises(IntegrityError, match="closes_after_open"):
        session.flush()
