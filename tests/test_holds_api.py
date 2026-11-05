import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Booking, BookingStatus, Court, Member, PeakWindow, RecurringSeries
from tests.helpers import START_OF_TIME, FixedClock, hold_body, slot_start
from tests.test_schema import make_court

MONDAY = 7


@pytest.fixture
def world(session: Session) -> tuple[Court, Member, Member]:
    court = make_court(session)
    alice, bob = Member(name="Alice"), Member(name="Bob")
    session.add_all([alice, bob])
    session.flush()
    return court, alice, bob


def as_member(member: Member) -> dict[str, str]:
    return {"X-Member-Id": str(member.id)}


def count_bookings(session: Session, member: Member | None = None) -> int:
    query = select(func.count()).select_from(Booking)
    if member is not None:
        query = query.where(Booking.member_id == member.id)
    return session.scalar(query) or 0


def hold(
    client: TestClient, member: Member, court: Court, day: int, hour: int, hours: int = 1
) -> Any:
    return client.post(
        "/holds", json=hold_body(court.id, day, hour, hours), headers=as_member(member)
    )


# ------------------------------------------------------------ the happy path


def test_creates_a_hold(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    response = hold(client, alice, court, MONDAY, 10)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "held"
    assert body["price"] == 20000
    assert body["member_id"] == str(alice.id)
    assert body["hold_expires_at"].startswith("2030-01-01T00:10:00")  # now + 10 minutes


def test_price_uses_the_venue_peak_hours(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    session.add(PeakWindow(venue_id=court.venue_id, weekday=0, start_hour=18, end_hour=22))
    session.flush()
    response = hold(client, alice, court, MONDAY, 17, hours=2)  # 17:00 off-peak, 18:00 peak
    assert response.json()["price"] == 20000 + 40000


# ------------------------------------------------------- who and what is asked


def test_the_member_header_is_required(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, _, _ = world
    response = client.post("/holds", json=hold_body(court.id, MONDAY, 10))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "member_required"


def test_a_malformed_member_id_is_refused(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, _, _ = world
    response = client.post(
        "/holds", json=hold_body(court.id, MONDAY, 10), headers={"X-Member-Id": "not-a-uuid"}
    )
    assert response.status_code == 401


def test_an_unknown_member_is_not_found(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, _, _ = world
    response = client.post(
        "/holds", json=hold_body(court.id, MONDAY, 10), headers={"X-Member-Id": str(uuid.uuid4())}
    )
    assert response.status_code == 404


def test_an_unknown_court_is_not_found(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    _, alice, _ = world
    response = client.post(
        "/holds", json=hold_body(uuid.uuid4(), MONDAY, 10), headers=as_member(alice)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# ------------------------------------------------------------- invalid slots


def test_a_time_without_a_timezone_is_refused(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    body = {
        "court_id": str(court.id),
        "start_at": "2030-01-07T10:00:00",
        "end_at": "2030-01-07T11:00:00",
    }
    assert client.post("/holds", json=body, headers=as_member(alice)).status_code == 422


@pytest.mark.parametrize(
    ("hour", "hours"),
    [(10, 4), (5, 1), (21, 2)],
    ids=["too-long", "before-opening", "past-closing"],
)
def test_invalid_slots_are_refused_and_nothing_is_saved(
    client: TestClient,
    session: Session,
    world: tuple[Court, Member, Member],
    hour: int,
    hours: int,
) -> None:
    court, alice, _ = world
    response = hold(client, alice, court, MONDAY, hour, hours)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_slot"
    assert count_bookings(session) == 0


def test_a_slot_in_the_past_is_refused(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    clock.advance(timedelta(days=10))  # now it is 11 January; the 7th is over
    assert hold(client, alice, court, MONDAY, 10).status_code == 422


# ---------------------------------------------------------------- conflicts


def test_a_taken_slot_is_refused(client: TestClient, world: tuple[Court, Member, Member]) -> None:
    court, alice, bob = world
    assert hold(client, alice, court, MONDAY, 10).status_code == 201
    response = hold(client, bob, court, MONDAY, 10)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "slot_unavailable"


def test_a_partly_overlapping_slot_is_refused(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    assert hold(client, alice, court, MONDAY, 10, hours=2).status_code == 201  # 10:00-12:00
    assert hold(client, bob, court, MONDAY, 11).status_code == 409


def test_back_to_back_and_other_court_slots_are_allowed(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    other = Court(venue_id=court.venue_id, name="Court 2")
    session.add(other)
    session.flush()
    assert hold(client, alice, court, MONDAY, 10).status_code == 201
    assert hold(client, bob, court, MONDAY, 11).status_code == 201
    assert hold(client, bob, other, MONDAY, 10).status_code == 201


def test_retrying_a_hold_returns_the_same_hold(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    first = hold(client, alice, court, MONDAY, 10)
    retry = hold(client, alice, court, MONDAY, 10)
    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    assert count_bookings(session) == 1


# ------------------------------------------------------------------- expiry


def test_a_hold_blocks_the_slot_until_it_expires(
    client: TestClient,
    session: Session,
    world: tuple[Court, Member, Member],
    clock: FixedClock,
) -> None:
    court, alice, bob = world
    first = hold(client, alice, court, MONDAY, 10).json()

    clock.advance(timedelta(minutes=9))
    assert hold(client, bob, court, MONDAY, 10).status_code == 409  # still held

    clock.advance(timedelta(minutes=2))  # 11 minutes after the hold was made
    assert hold(client, bob, court, MONDAY, 10).status_code == 201  # no longer blocks

    old = session.get(Booking, uuid.UUID(first["id"]))
    assert old is not None
    session.refresh(old)
    assert old.status == BookingStatus.EXPIRED


# -------------------------------------------------------- limit of 3 bookings


def test_a_member_may_hold_at_most_three(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    for day in (7, 8, 9):
        assert hold(client, alice, court, day, 10).status_code == 201
    response = hold(client, alice, court, 10, 10)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "booking_limit_reached"
    assert count_bookings(session, alice) == 3  # the refused request saved nothing


def test_the_limit_is_per_member(client: TestClient, world: tuple[Court, Member, Member]) -> None:
    court, alice, bob = world
    for day in (7, 8, 9):
        hold(client, alice, court, day, 10)
    assert hold(client, bob, court, 10, 10).status_code == 201


def test_expired_holds_do_not_count_towards_the_limit(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    for day in (7, 8, 9):
        assert hold(client, alice, court, day, 10).status_code == 201
    clock.advance(timedelta(minutes=11))
    assert hold(client, alice, court, 10, 10).status_code == 201


def confirmed(
    court: Court, member: Member, day: int, series_id: uuid.UUID | None = None
) -> Booking:
    start = slot_start(day, 10)
    return Booking(
        member_id=member.id,
        court_id=court.id,
        series_id=series_id,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=BookingStatus.CONFIRMED,
        price=20000,
    )


def test_confirmed_future_bookings_count_towards_the_limit(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    session.add_all([confirmed(court, alice, day) for day in (7, 8, 9)])
    session.flush()
    assert hold(client, alice, court, 10, 10).status_code == 409


def test_a_recurring_series_counts_as_one_booking(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    series = RecurringSeries(
        member_id=alice.id,
        court_id=court.id,
        weekday=0,
        start_hour=10,
        duration_hours=1,
        first_date=date(2030, 1, 7),
        occurrences=5,
    )
    session.add(series)
    session.flush()
    # Five weekly bookings (Mondays 7, 14, 21, 28 January and 4 February) are one series.
    for week in range(5):
        session.add(confirmed(court, alice, 7 + 7 * week, series.id))
    session.flush()

    assert hold(client, alice, court, 8, 10).status_code == 201  # series (1) + hold (1)
    assert hold(client, alice, court, 9, 10).status_code == 201  # series (1) + holds (2)
    assert hold(client, alice, court, 10, 10).status_code == 409  # that would be a 4th


def test_start_of_time_is_what_the_tests_expect() -> None:
    assert START_OF_TIME.year == 2030
