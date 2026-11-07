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
PRICE = 20000  # one off-peak hour at the test venue
HOUR = timedelta(hours=1)


@pytest.fixture
def world(session: Session) -> tuple[Court, Member, Member]:
    court = make_court(session)
    alice, bob = Member(name="Alice"), Member(name="Bob")
    session.add_all([alice, bob])
    session.flush()
    return court, alice, bob


def as_member(member: Member) -> dict[str, str]:
    return {"X-Member-Id": str(member.id)}


def hold(client: TestClient, member: Member, court: Court, day: int = MONDAY) -> Any:
    return client.post("/holds", json=hold_body(court.id, day, 10), headers=as_member(member))


def confirmed_booking(client: TestClient, member: Member, court: Court, day: int = MONDAY) -> str:
    held = hold(client, member, court, day).json()
    response = client.post(f"/holds/{held['id']}/confirm", headers=as_member(member))
    assert response.status_code == 200
    return str(held["id"])


def cancel(client: TestClient, member: Member, booking_id: str) -> Any:
    return client.post(f"/bookings/{booking_id}/cancel", headers=as_member(member))


def go_to(clock: FixedClock, moment_before_start: timedelta, day: int = MONDAY) -> None:
    """Move the clock to this long before the booking starts."""
    clock.advance(slot_start(day, 10) - moment_before_start - clock.now())


def db_row(session: Session, booking_id: str) -> Booking:
    row = session.scalar(
        select(Booking)
        .where(Booking.id == uuid.UUID(booking_id))
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


# ---------------------------------------------------------- the refund rule


def test_cancelling_with_plenty_of_notice_refunds_everything(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    booking_id = confirmed_booking(client, alice, court)

    response = cancel(client, alice, booking_id)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == booking_id
    assert body["status"] == "cancelled"
    assert body["refund_amount"] == PRICE
    assert body["cancelled_at"].startswith("2030-01-01T00:00:00")


def test_exactly_24_hours_of_notice_is_a_full_refund(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    booking_id = confirmed_booking(client, alice, court)
    go_to(clock, timedelta(hours=24))
    assert cancel(client, alice, booking_id).json()["refund_amount"] == PRICE


def test_one_second_inside_24_hours_is_no_refund(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    booking_id = confirmed_booking(client, alice, court)
    go_to(clock, timedelta(hours=24) - timedelta(seconds=1))
    response = cancel(client, alice, booking_id)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["refund_amount"] == 0


def test_cancelling_an_unconfirmed_hold_releases_it_with_no_refund(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    held = hold(client, alice, court).json()
    response = cancel(client, alice, held["id"])
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["refund_amount"] == 0  # nothing was paid


# -------------------------------------------------------- safe to retry


def test_a_retry_returns_the_original_result_even_after_the_window_closed(
    client: TestClient,
    session: Session,
    world: tuple[Court, Member, Member],
    clock: FixedClock,
) -> None:
    court, alice, _ = world
    booking_id = confirmed_booking(client, alice, court)
    first = cancel(client, alice, booking_id)  # days ahead: full refund

    go_to(clock, timedelta(hours=1))  # the retry arrives when only an hour is left
    second = cancel(client, alice, booking_id)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()  # same refund, same cancelled_at
    assert second.json()["refund_amount"] == PRICE
    assert session.scalar(select(func.count()).select_from(Booking)) == 1


# ------------------------------------------------------ what cancelling frees


def test_a_cancelled_slot_can_be_booked_by_someone_else(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    booking_id = confirmed_booking(client, alice, court)
    assert hold(client, bob, court).status_code == 409  # taken

    cancel(client, alice, booking_id)
    assert hold(client, bob, court).status_code == 201  # free again


def test_cancelling_frees_a_place_under_the_limit(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    ids = [confirmed_booking(client, alice, court, day) for day in (7, 8, 9)]
    assert hold(client, alice, court, 10).status_code == 409  # at the limit

    cancel(client, alice, ids[0])
    assert hold(client, alice, court, 10).status_code == 201


# ----------------------------------------------------------- what is refused


def test_a_booking_that_has_started_cannot_be_cancelled(
    client: TestClient,
    session: Session,
    world: tuple[Court, Member, Member],
    clock: FixedClock,
) -> None:
    court, alice, _ = world
    booking_id = confirmed_booking(client, alice, court)
    go_to(clock, timedelta(minutes=-30))  # half an hour after the start
    response = cancel(client, alice, booking_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state"
    assert db_row(session, booking_id).status == BookingStatus.CONFIRMED


def test_an_expired_hold_cannot_be_cancelled(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    held = hold(client, alice, court).json()
    clock.advance(timedelta(minutes=11))
    response = cancel(client, alice, held["id"])
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "hold_expired"


def test_only_the_owner_can_cancel(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    booking_id = confirmed_booking(client, alice, court)
    assert cancel(client, bob, booking_id).status_code == 404
    assert db_row(session, booking_id).status == BookingStatus.CONFIRMED


def test_an_unknown_booking_is_not_found(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    _, alice, _ = world
    assert cancel(client, alice, str(uuid.uuid4())).status_code == 404


def test_the_member_header_is_required(client: TestClient) -> None:
    assert client.post(f"/bookings/{uuid.uuid4()}/cancel").status_code == 401
