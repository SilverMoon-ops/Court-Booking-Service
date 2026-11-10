"""Guarantee G3: a failed multi-step operation leaves no partial state.

A recurring booking is the multi-step operation: several weeks, all saved or none.
These tests try to catch it half-done: with simultaneous clashing requests, with a
crash just before the commit, and with single holds racing the series.
"""

from collections import Counter
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import SystemClock
from app.db import SessionLocal, engine
from app.main import app
from app.models import Booking, RecurringSeries
from app.services.recurring import create_recurring
from tests.conftest import CommittedCourt
from tests.helpers import IST, fire_together, future_body, future_recurring_body

WEEKS = 4


def bookings_of(court_id: UUID, member_id: UUID | None = None) -> int:
    query = select(func.count()).select_from(Booking).where(Booking.court_id == court_id)
    if member_id is not None:
        query = query.where(Booking.member_id == member_id)
    with SessionLocal() as db:
        return db.scalar(query) or 0


def series_of(court_id: UUID) -> int:
    with SessionLocal() as db:
        return (
            db.scalar(
                select(func.count())
                .select_from(RecurringSeries)
                .where(RecurringSeries.court_id == court_id)
            )
            or 0
        )


def test_ten_members_ask_for_the_same_series_at_once_and_exactly_one_gets_it(
    committed_court: CommittedCourt,
) -> None:
    body = future_recurring_body(committed_court.court_id, days_ahead=3, hour=10, occurrences=WEEKS)
    responses = fire_together(
        [(member, "/recurring-bookings", body) for member in committed_court.member_ids[:10]]
    )

    assert Counter(r.status_code for r in responses) == {201: 1, 409: 9}
    assert bookings_of(committed_court.court_id) == WEEKS  # one complete series, nothing extra
    assert series_of(committed_court.court_id) == 1


def test_clashing_series_never_leave_a_half_booked_series(committed_court: CommittedCourt) -> None:
    """Two series overlap in two of their weeks. Whoever loses must have NOTHING saved."""
    for round_number in range(8):
        # Fresh members each round, so the limit of three bookings never gets in the way.
        first, second = committed_court.member_ids[2 * round_number : 2 * round_number + 2]
        hour = 6 + round_number  # a different hour each round, so rounds never interfere
        early = future_recurring_body(committed_court.court_id, 3, hour, occurrences=WEEKS)
        late = future_recurring_body(committed_court.court_id, 3 + 14, hour, occurrences=WEEKS)
        responses = fire_together(
            [(first, "/recurring-bookings", early), (second, "/recurring-bookings", late)]
        )

        statuses = sorted(r.status_code for r in responses)
        assert statuses == [201, 409], statuses  # exactly one winner
        for member, response in zip((first, second), responses, strict=True):
            saved = len(_bookings_at_hour(committed_court.court_id, member, hour))
            # all four weeks for the winner, none for the loser; never in between
            assert saved == (WEEKS if response.status_code == 201 else 0), (round_number, saved)


def _bookings_at_hour(court_id: UUID, member_id: UUID, hour: int) -> list[Booking]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(Booking).where(Booking.court_id == court_id, Booking.member_id == member_id)
        ).all()
    return [b for b in rows if b.start_at.astimezone(IST).hour == hour]


def test_ten_parallel_retries_of_one_series_make_one_series(
    committed_court: CommittedCourt,
) -> None:
    member = committed_court.member_ids[0]
    body = future_recurring_body(committed_court.court_id, days_ahead=3, hour=10, occurrences=WEEKS)
    responses = fire_together([(member, "/recurring-bookings", body)] * 10)

    assert Counter(r.status_code for r in responses) == {201: 1, 200: 9}
    assert len({r.json()["id"] for r in responses}) == 1
    assert series_of(committed_court.court_id) == 1
    assert bookings_of(committed_court.court_id) == WEEKS


def test_a_crash_just_before_the_commit_saves_nothing(committed_court: CommittedCourt) -> None:
    """Every row of the series has been inserted when the server dies. None may survive."""

    class CrashOnCommit(Session):
        def commit(self) -> None:
            raise RuntimeError("the server died just before the commit")

    body = future_recurring_body(committed_court.court_id, days_ahead=3, hour=10, occurrences=WEEKS)
    from datetime import datetime

    with CrashOnCommit(engine) as db, pytest.raises(RuntimeError):
        create_recurring(
            db,
            SystemClock(),
            committed_court.member_ids[0],
            committed_court.court_id,
            datetime.fromisoformat(body["start_at"]),
            datetime.fromisoformat(body["end_at"]),
            WEEKS,
        )

    assert series_of(committed_court.court_id) == 0
    assert bookings_of(committed_court.court_id) == 0


def test_a_series_racing_single_holds_for_one_of_its_weeks_stays_all_or_nothing(
    committed_court: CommittedCourt,
) -> None:
    """The series and five single holds all want the same week-3 slot at the same instant."""
    for round_number in range(6):
        # Six fresh members each round: one asks for the series, five ask for a single hold.
        owner, *others = committed_court.member_ids[6 * round_number : 6 * round_number + 6]
        hour = 6 + round_number
        series = future_recurring_body(committed_court.court_id, 3, hour, occurrences=WEEKS)
        third_week = future_body(committed_court.court_id, 3 + 14, hour)
        responses = fire_together(
            [(owner, "/recurring-bookings", series)]
            + [(member, "/holds", third_week) for member in others]
        )

        series_response, hold_responses = responses[0], responses[1:]
        held = sum(1 for r in hold_responses if r.status_code == 201)
        series_rows = len(_bookings_at_hour(committed_court.court_id, owner, hour))
        if series_response.status_code == 201:
            assert (series_rows, held) == (WEEKS, 0)  # the series won every week
        else:
            assert series_response.status_code == 409
            assert (series_rows, held) == (0, 1)  # one hold won; the series saved nothing


def test_one_member_cannot_exceed_three_bookings_by_sending_series_in_parallel(
    committed_court: CommittedCourt,
) -> None:
    member = committed_court.member_ids[0]
    requests = [
        (
            member,
            "/recurring-bookings",
            future_recurring_body(committed_court.court_id, 3, hour, occurrences=2),
        )
        for hour in range(10, 15)  # five different series that never clash with each other
    ]
    responses = fire_together(requests)

    assert Counter(r.status_code for r in responses) == {201: 3, 409: 2}
    assert {r.json()["error"]["code"] for r in responses if r.status_code == 409} == {
        "booking_limit_reached"
    }
    assert series_of(committed_court.court_id) == 3
    assert bookings_of(committed_court.court_id) == 3 * 2


def test_the_client_helper_is_wired_to_the_real_app() -> None:
    assert TestClient(app).get("/health").status_code == 200
