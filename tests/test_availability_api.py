import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Booking, BookingStatus, Court, Member, PeakWindow
from tests.helpers import FixedClock, hold_body, slot_start
from tests.test_schema import make_court

MONDAY_DATE = "2030-01-07"
MONDAY = 7


@pytest.fixture
def world(session: Session) -> tuple[Court, Member, Member]:
    court = make_court(session)
    alice, bob = Member(name="Alice"), Member(name="Bob")
    session.add_all([alice, bob])
    session.flush()
    return court, alice, bob


def availability(client: TestClient, court_id: uuid.UUID, date: str | None = MONDAY_DATE) -> Any:
    params = {"date": date} if date else {}
    return client.get(f"/courts/{court_id}/availability", params=params)


def by_hour(body: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Slots keyed by their venue-local starting hour."""
    return {int(slot["start_at"][11:13]): slot for slot in body["slots"]}


def book(client: TestClient, member: Member, court: Court, hour: int, hours: int = 1) -> Any:
    return client.post(
        "/holds",
        json=hold_body(court.id, MONDAY, hour, hours),
        headers={"X-Member-Id": str(member.id)},
    )


# ------------------------------------------------------------ the day's shape


def test_a_day_lists_every_opening_hour_as_free(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, _, _ = world
    response = availability(client, court.id)
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == MONDAY_DATE
    assert body["timezone"] == "Asia/Kolkata"
    assert body["court_id"] == str(court.id)
    assert len(body["slots"]) == 16  # 06:00 to 22:00
    assert {slot["status"] for slot in body["slots"]} == {"free"}
    assert body["slots"][0]["start_at"].startswith("2030-01-07T06:00:00+05:30")  # local time
    assert body["slots"][-1]["end_at"].startswith("2030-01-07T22:00:00+05:30")


def test_each_slot_shows_its_peak_or_off_peak_price(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, _, _ = world
    session.add(PeakWindow(venue_id=court.venue_id, weekday=0, start_hour=18, end_hour=22))
    session.flush()
    slots = by_hour(availability(client, court.id).json())
    assert slots[17]["price"] == 20000
    assert [slots[h]["price"] for h in (18, 19, 20, 21)] == [40000] * 4


def test_the_date_defaults_to_today_at_the_venue(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, _, _ = world  # the test clock says 1 January 2030, 05:30 in India
    body = availability(client, court.id, date=None).json()
    assert body["date"] == "2030-01-01"


# ------------------------------------------------- what makes a slot unavailable


def test_a_confirmed_booking_makes_its_hours_unavailable(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    held = book(client, alice, court, 10, hours=2).json()  # 10:00 to 12:00
    client.post(f"/holds/{held['id']}/confirm", headers={"X-Member-Id": str(alice.id)})

    slots = by_hour(availability(client, court.id).json())
    assert [slots[h]["status"] for h in (9, 10, 11, 12)] == [
        "free",
        "unavailable",
        "unavailable",
        "free",
    ]


def test_a_live_hold_makes_a_slot_unavailable_and_an_expired_one_does_not(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    book(client, alice, court, 10)
    assert by_hour(availability(client, court.id).json())[10]["status"] == "unavailable"

    clock.advance(timedelta(minutes=11))
    assert by_hour(availability(client, court.id).json())[10]["status"] == "free"


def test_a_cancelled_booking_shows_as_free(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    held = book(client, alice, court, 10).json()
    client.post(f"/bookings/{held['id']}/cancel", headers={"X-Member-Id": str(alice.id)})
    assert by_hour(availability(client, court.id).json())[10]["status"] == "free"


def test_hours_that_have_already_started_are_unavailable(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, _, _ = world
    clock.advance(timedelta(hours=5))  # now 10:30 in India on 1 January
    slots = by_hour(availability(client, court.id, "2030-01-01").json())
    assert slots[10]["status"] == "unavailable"  # started at 10:00
    assert slots[11]["status"] == "free"
    assert slots[6]["status"] == "unavailable"


def test_another_courts_bookings_do_not_affect_this_one(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    other = Court(venue_id=court.venue_id, name="Court 2")
    session.add(other)
    session.flush()
    book(client, alice, other, 10)
    assert by_hour(availability(client, court.id).json())[10]["status"] == "free"
    assert by_hour(availability(client, other.id).json())[10]["status"] == "unavailable"


def test_availability_never_reveals_who_booked(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    book(client, alice, court, 10)
    text = availability(client, court.id).text
    assert str(alice.id) not in text
    assert set(availability(client, court.id).json()["slots"][0]) == {
        "start_at",
        "end_at",
        "status",
        "price",
    }


# ------------------------------------------------------------------- errors


def test_an_unknown_court_is_not_found(client: TestClient) -> None:
    response = availability(client, uuid.uuid4())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_malformed_date_is_refused(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, _, _ = world
    assert availability(client, court.id, "not-a-date").status_code == 422


# ----------------------------- availability must agree with POST /holds


def test_availability_agrees_with_what_booking_really_does(
    client: TestClient, session: Session, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    """For every hour of the day, 'free' means a hold works and 'unavailable' means it fails.

    The day is made mixed on purpose: a confirmed booking, a live hold, a cancelled one,
    and hours that have already started.
    """
    court, alice, bob = world
    held = book(client, alice, court, 10, hours=2).json()
    client.post(f"/holds/{held['id']}/confirm", headers={"X-Member-Id": str(alice.id)})
    book(client, bob, court, 15)
    cancelled = book(client, bob, court, 17).json()
    client.post(f"/bookings/{cancelled['id']}/cancel", headers={"X-Member-Id": str(bob.id)})
    clock.advance(slot_start(MONDAY, 8) - clock.now() + timedelta(minutes=10))  # 08:10 Monday

    slots = by_hour(availability(client, court.id).json())
    assert {s["status"] for s in slots.values()} == {"free", "unavailable"}

    for hour, slot in slots.items():
        stranger = Member(name=f"Stranger {hour}")  # a fresh member, so the limit never interferes
        session.add(stranger)
        session.flush()
        outcome = book(client, stranger, court, hour)
        if slot["status"] == "free":
            assert outcome.status_code == 201, (hour, outcome.text)
        else:
            assert outcome.status_code in (409, 422), (hour, outcome.text)


def test_booking_changes_state_between_two_availability_calls(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    before = by_hour(availability(client, court.id).json())[10]["status"]
    book(client, alice, court, 10)
    after = by_hour(availability(client, court.id).json())[10]["status"]
    assert (before, after) == ("free", "unavailable")
    assert session.query(Booking).filter_by(status=BookingStatus.HELD).count() == 1
