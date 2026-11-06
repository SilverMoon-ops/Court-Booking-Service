import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Booking, BookingStatus, Court, Member
from tests.helpers import FixedClock, hold_body, slot_start
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


def hold(client: TestClient, member: Member, court: Court, day: int, hour: int = 10) -> Any:
    return client.post("/holds", json=hold_body(court.id, day, hour), headers=as_member(member))


def confirm(client: TestClient, member: Member, hold_id: str) -> Any:
    return client.post(f"/holds/{hold_id}/confirm", headers=as_member(member))


def status_in_db(session: Session, booking_id: str) -> str:
    row = session.scalar(
        select(Booking)
        .where(Booking.id == uuid.UUID(booking_id))
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row.status


def booking_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Booking)) or 0


# ------------------------------------------------------------ the happy path


def test_confirming_a_hold_makes_a_confirmed_booking(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    held = hold(client, alice, court, MONDAY).json()

    response = confirm(client, alice, held["id"])
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == held["id"]  # the hold and the booking are the same record
    assert body["status"] == "confirmed"
    assert body["price"] == held["price"]
    assert body["confirmed_at"].startswith("2030-01-01T00:00:00")


# --------------------------------------------- guarantee G2: safe to retry


def test_retrying_a_confirm_returns_the_same_booking(
    client: TestClient,
    session: Session,
    world: tuple[Court, Member, Member],
    clock: FixedClock,
) -> None:
    court, alice, _ = world
    held = hold(client, alice, court, MONDAY).json()

    first = confirm(client, alice, held["id"])
    clock.advance(timedelta(minutes=3))  # the retry arrives later
    second = confirm(client, alice, held["id"])
    third = confirm(client, alice, held["id"])

    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 200]
    assert first.json() == second.json() == third.json()  # even confirmed_at is unchanged
    assert booking_count(session) == 1


def test_a_retry_still_works_after_the_hold_time_has_passed(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    """The client timed out, the server had confirmed, the client retries much later."""
    court, alice, _ = world
    held = hold(client, alice, court, MONDAY).json()
    assert confirm(client, alice, held["id"]).status_code == 200

    clock.advance(timedelta(hours=1))  # long after the 10 minutes were up
    retry = confirm(client, alice, held["id"])
    assert retry.status_code == 200
    assert retry.json()["status"] == "confirmed"


# -------------------------------------------------------------------- expiry


def test_confirming_an_expired_hold_is_refused(
    client: TestClient,
    session: Session,
    world: tuple[Court, Member, Member],
    clock: FixedClock,
) -> None:
    court, alice, _ = world
    held = hold(client, alice, court, MONDAY).json()
    clock.advance(timedelta(minutes=11))

    response = confirm(client, alice, held["id"])
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "hold_expired"
    assert status_in_db(session, held["id"]) != BookingStatus.CONFIRMED


def test_an_expired_hold_cannot_steal_a_slot_someone_else_took(
    client: TestClient,
    session: Session,
    world: tuple[Court, Member, Member],
    clock: FixedClock,
) -> None:
    court, alice, bob = world
    alices = hold(client, alice, court, MONDAY).json()
    clock.advance(timedelta(minutes=11))
    bobs = hold(client, bob, court, MONDAY)
    assert bobs.status_code == 201  # the expired hold no longer blocks Bob

    assert confirm(client, alice, alices["id"]).status_code == 410
    assert status_in_db(session, bobs.json()["id"]) == BookingStatus.HELD  # Bob is untouched
    assert confirm(client, bob, bobs.json()["id"]).status_code == 200


def test_a_hold_for_a_slot_that_has_already_started_cannot_be_confirmed(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    start = slot_start(MONDAY, 10)  # 10:00 Indian time on Monday 7 January
    clock.advance(start - clock.now() - timedelta(minutes=5))  # five minutes before the start
    held = hold(client, alice, court, MONDAY).json()

    clock.advance(timedelta(minutes=6))  # the hold has not expired (10 min) but the slot began
    assert confirm(client, alice, held["id"]).status_code == 410


# ---------------------------------------------------- who may confirm what


def test_only_the_owner_can_confirm(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    held = hold(client, alice, court, MONDAY).json()

    response = confirm(client, bob, held["id"])
    assert response.status_code == 404  # not even revealed to exist
    assert status_in_db(session, held["id"]) == BookingStatus.HELD


def test_an_unknown_hold_is_not_found(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    _, alice, _ = world
    response = confirm(client, alice, str(uuid.uuid4()))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_the_member_header_is_required(client: TestClient) -> None:
    response = client.post(f"/holds/{uuid.uuid4()}/confirm")
    assert response.status_code == 401


def test_a_cancelled_booking_cannot_be_confirmed(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    held = hold(client, alice, court, MONDAY).json()
    row = session.get(Booking, uuid.UUID(held["id"]))
    assert row is not None
    row.status = BookingStatus.CANCELLED
    session.flush()

    response = confirm(client, alice, held["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state"


# ----------------------------------------------- what confirming changes


def test_a_confirmed_booking_never_expires_and_keeps_blocking_the_slot(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, bob = world
    held = hold(client, alice, court, MONDAY).json()
    confirm(client, alice, held["id"])

    clock.advance(timedelta(hours=5))
    response = hold(client, bob, court, MONDAY)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "slot_unavailable"


def test_confirmed_bookings_keep_counting_towards_the_limit(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    for day in (7, 8, 9):
        held = hold(client, alice, court, day).json()
        assert confirm(client, alice, held["id"]).status_code == 200

    clock.advance(timedelta(hours=1))  # holds would have expired by now, bookings do not
    response = hold(client, alice, court, 10)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "booking_limit_reached"
