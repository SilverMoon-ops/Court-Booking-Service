"""Guarantee G1: a court is never double-booked.

The first group of tests checks the rule one case at a time. The last test
attacks it with many simultaneous requests.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.errors import DeadlockDetected
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Booking, BookingStatus, Court, Member
from tests.conftest import CONCURRENT_REQUESTS, CommittedCourt
from tests.test_schema import make_court

T10 = datetime(2030, 1, 7, 10, 0, tzinfo=UTC)
CONSTRAINT = "ex_bookings_no_overlap"


def at(hour: int, hours: int = 1) -> tuple[datetime, datetime]:
    start = T10 + timedelta(hours=hour - 10)
    return start, start + timedelta(hours=hours)


def book(
    court: Court,
    member: Member,
    hour: int,
    hours: int = 1,
    status: str = BookingStatus.CONFIRMED,
) -> Booking:
    start, end = at(hour, hours)
    return Booking(
        member_id=member.id,
        court_id=court.id,
        start_at=start,
        end_at=end,
        status=status,
        price=20000,
        hold_expires_at=start if status == BookingStatus.HELD else None,
    )


@pytest.fixture
def setup(session: Session) -> tuple[Court, Member, Member]:
    court = make_court(session)
    first, second = Member(name="First"), Member(name="Second")
    session.add_all([first, second])
    session.flush()
    return court, first, second


def test_same_slot_twice_is_rejected(session: Session, setup: tuple[Court, Member, Member]) -> None:
    court, first, second = setup
    session.add(book(court, first, 10))
    session.flush()
    session.add(book(court, second, 10))
    with pytest.raises(IntegrityError, match=CONSTRAINT):
        session.flush()


@pytest.mark.parametrize(
    ("hour", "hours"),
    [(11, 1), (10, 2), (11, 2), (9, 3)],
    ids=["inside", "starts-same-hour", "starts-midway", "wraps-around"],
)
def test_partial_overlap_is_rejected(
    session: Session, setup: tuple[Court, Member, Member], hour: int, hours: int
) -> None:
    court, first, second = setup
    session.add(book(court, first, 10, hours=2))  # 10:00 - 12:00
    session.flush()
    session.add(book(court, second, hour, hours=hours))
    with pytest.raises(IntegrityError, match=CONSTRAINT):
        session.flush()


def test_back_to_back_bookings_are_allowed(
    session: Session, setup: tuple[Court, Member, Member]
) -> None:
    court, first, second = setup
    session.add(book(court, first, 10))  # 10:00 - 11:00
    session.add(book(court, second, 11))  # 11:00 - 12:00
    session.flush()


def test_same_time_on_a_different_court_is_allowed(
    session: Session, setup: tuple[Court, Member, Member]
) -> None:
    court, first, second = setup
    other = Court(venue_id=court.venue_id, name="Court 2")
    session.add(other)
    session.flush()
    session.add(book(court, first, 10))
    session.add(book(other, second, 10))
    session.flush()


def test_an_unconfirmed_hold_blocks_the_slot(
    session: Session, setup: tuple[Court, Member, Member]
) -> None:
    court, first, second = setup
    session.add(book(court, first, 10, status=BookingStatus.HELD))
    session.flush()
    session.add(book(court, second, 10))
    with pytest.raises(IntegrityError, match=CONSTRAINT):
        session.flush()


@pytest.mark.parametrize("status", [BookingStatus.CANCELLED, BookingStatus.EXPIRED])
def test_cancelled_or_expired_bookings_do_not_block(
    session: Session, setup: tuple[Court, Member, Member], status: str
) -> None:
    court, first, second = setup
    session.add(book(court, first, 10, status=status))
    session.add(book(court, second, 10))
    session.flush()


def test_cancelling_frees_the_slot(session: Session, setup: tuple[Court, Member, Member]) -> None:
    court, first, second = setup
    original = book(court, first, 10)
    session.add(original)
    session.flush()
    original.status = BookingStatus.CANCELLED
    session.flush()
    session.add(book(court, second, 10))
    session.flush()


def test_reactivating_a_cancelled_booking_into_a_taken_slot_is_rejected(
    session: Session, setup: tuple[Court, Member, Member]
) -> None:
    court, first, second = setup
    cancelled = book(court, first, 10, status=BookingStatus.CANCELLED)
    session.add(cancelled)
    session.add(book(court, second, 10))
    session.flush()
    cancelled.status = BookingStatus.CONFIRMED
    with pytest.raises(IntegrityError, match=CONSTRAINT):
        session.flush()


def fire_together(
    engine: Engine, court_id: uuid.UUID, member_ids: list[uuid.UUID], start: datetime
) -> list[str]:
    """Every member tries to book the same slot at the same instant.

    There is deliberately no "is it free?" check here. Each client just inserts and
    commits on its own connection, so only the database can prevent a double booking.
    """
    gate = threading.Barrier(len(member_ids))

    def client(member_id: uuid.UUID) -> str:
        with Session(engine) as db:
            db.add(
                Booking(
                    member_id=member_id,
                    court_id=court_id,
                    start_at=start,
                    end_at=start + timedelta(hours=1),
                    status=BookingStatus.CONFIRMED,
                    price=20000,
                )
            )
            gate.wait()  # everyone fires together
            try:
                db.commit()
            except IntegrityError as exc:
                return "conflict" if CONSTRAINT in str(exc) else f"unexpected: {exc}"
            except OperationalError as exc:
                # When two inserts collide, Postgres may stop one with a deadlock error
                # instead of an exclusion error. It is still a refusal, not a booking.
                if isinstance(exc.orig, DeadlockDetected):
                    return "deadlock"
                return f"unexpected: {exc}"
            return "booked"

    with ThreadPoolExecutor(max_workers=len(member_ids)) as pool:
        return list(pool.map(client, member_ids))


def test_many_simultaneous_requests_book_a_slot_exactly_once(
    committed_court: CommittedCourt,
) -> None:
    """50 clients try to book the same court and hour at the same instant."""
    # One connection per client, so all 50 really run at the same time.
    engine = create_engine(
        get_settings().database_url, pool_size=CONCURRENT_REQUESTS, max_overflow=0
    )
    try:
        for round_number in range(5):
            start = T10 + timedelta(days=round_number)  # a fresh slot each round
            outcomes = fire_together(
                engine, committed_court.court_id, committed_court.member_ids, start
            )
            summary = {value: outcomes.count(value) for value in set(outcomes)}
            print(f"round {round_number}: {summary}")

            assert outcomes.count("booked") == 1, summary
            assert set(outcomes) <= {"booked", "conflict", "deadlock"}, summary
            with Session(engine) as db:
                stored = db.scalar(
                    select(func.count())
                    .select_from(Booking)
                    .where(Booking.court_id == committed_court.court_id, Booking.start_at == start)
                )
            assert stored == 1, summary
    finally:
        engine.dispose()
