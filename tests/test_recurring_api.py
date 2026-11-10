import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Booking, BookingStatus, Court, Member, PeakWindow, RecurringSeries, Venue
from tests.helpers import FixedClock, hold_body, recurring_body
from tests.test_schema import make_court

MONDAY = 7  # 7 January 2030; the following Mondays are 14, 21, 28 January


@pytest.fixture
def world(session: Session) -> tuple[Court, Member, Member]:
    court = make_court(session)
    alice, bob = Member(name="Alice"), Member(name="Bob")
    session.add_all([alice, bob])
    session.flush()
    return court, alice, bob


def as_member(member: Member) -> dict[str, str]:
    return {"X-Member-Id": str(member.id)}


def series(client: TestClient, member: Member, body: dict[str, Any]) -> Any:
    return client.post("/recurring-bookings", json=body, headers=as_member(member))


def hold(client: TestClient, member: Member, court: Court, day: int, hour: int = 10) -> Any:
    return client.post("/holds", json=hold_body(court.id, day, hour), headers=as_member(member))


def count(session: Session, model: type[Booking] | type[RecurringSeries]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


# ------------------------------------------------------------- the happy path


def test_a_weekly_series_books_every_week(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    response = series(client, alice, recurring_body(court.id, MONDAY, 10, occurrences=4))
    assert response.status_code == 201
    body = response.json()
    assert body["occurrences"] == 4
    assert body["weekday"] == 0  # Monday
    assert body["start_hour"] == 10
    assert body["first_date"] == "2030-01-07"
    starts = [b["start_at"] for b in body["bookings"]]
    assert [s[:10] for s in starts] == ["2030-01-07", "2030-01-14", "2030-01-21", "2030-01-28"]
    assert {b["status"] for b in body["bookings"]} == {"confirmed"}
    assert {b["series_id"] for b in body["bookings"]} == {body["id"]}


def test_each_week_is_priced_on_its_own(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    session.add(PeakWindow(venue_id=court.venue_id, weekday=0, start_hour=18, end_hour=22))
    session.flush()
    body = series(
        client, alice, recurring_body(court.id, MONDAY, 17, hours=2, occurrences=3)
    ).json()
    assert [b["price"] for b in body["bookings"]] == [60000, 60000, 60000]  # 20000 + 40000


def test_the_same_local_time_is_kept_across_a_daylight_saving_change(
    client: TestClient, session: Session, clock: FixedClock
) -> None:
    venue = Venue(
        name="New York Club",
        timezone="America/New_York",
        open_hour=6,
        close_hour=22,
        offpeak_rate=20000,
        peak_rate=40000,
    )
    session.add(venue)
    session.flush()
    court, member = Court(venue_id=venue.id, name="Court 1"), Member(name="Nora")
    session.add_all([court, member])
    session.flush()

    # Mondays 4, 11 and 18 March 2030, 18:00 New York time. Clocks go forward on 10 March.
    body = {
        "court_id": str(court.id),
        "start_at": "2030-03-04T18:00:00-05:00",
        "end_at": "2030-03-04T19:00:00-05:00",
        "occurrences": 3,
    }
    response = series(client, member, body)
    assert response.status_code == 201
    assert [b["start_at"][:16] for b in response.json()["bookings"]] == [
        "2030-03-04T23:00",  # 18:00 EST is 23:00 UTC
        "2030-03-11T22:00",  # 18:00 EDT is 22:00 UTC: the UTC hour moved, the local hour did not
        "2030-03-18T22:00",
    ]


def test_a_week_on_a_skipped_local_hour_refuses_the_whole_series(
    client: TestClient, session: Session
) -> None:
    venue = Venue(
        name="Night Club",
        timezone="America/New_York",
        open_hour=0,
        close_hour=24,
        offpeak_rate=20000,
        peak_rate=40000,
    )
    session.add(venue)
    session.flush()
    court, member = Court(venue_id=venue.id, name="Court 1"), Member(name="Nora")
    session.add_all([court, member])
    session.flush()

    # 02:00 on Sunday 10 March 2030 does not exist in New York.
    body = {
        "court_id": str(court.id),
        "start_at": "2030-03-03T02:00:00-05:00",
        "end_at": "2030-03-03T03:00:00-05:00",
        "occurrences": 2,
    }
    response = series(client, member, body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_slot"
    assert count(session, Booking) == 0


# ------------------------------- guarantee G3: all or nothing


def test_one_taken_week_means_no_week_is_booked(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    taken = hold(client, bob, court, MONDAY + 14)  # Bob holds the third Monday
    assert taken.status_code == 201

    response = series(client, alice, recurring_body(court.id, MONDAY, 10, occurrences=4))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "slot_unavailable"
    assert "2030-01-21" in response.json()["error"]["message"]  # says which week is taken
    assert count(session, RecurringSeries) == 0
    assert count(session, Booking) == 1  # only Bob's hold; none of Alice's weeks


def test_the_series_works_once_the_blocking_hold_has_expired(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, bob = world
    hold(client, bob, court, MONDAY + 14)
    assert series(client, alice, recurring_body(court.id, MONDAY, 10)).status_code == 409

    clock.advance(timedelta(minutes=11))
    assert series(client, alice, recurring_body(court.id, MONDAY, 10)).status_code == 201


def test_a_confirmed_booking_in_the_way_also_refuses_the_series(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    held = hold(client, bob, court, MONDAY + 7).json()
    client.post(f"/holds/{held['id']}/confirm", headers=as_member(bob))

    assert series(client, alice, recurring_body(court.id, MONDAY, 10)).status_code == 409
    assert count(session, RecurringSeries) == 0
    assert count(session, Booking) == 1


# ----------------------------------------------------- the limit of 3 bookings


def test_a_series_is_allowed_when_the_member_has_room(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    hold(client, alice, court, MONDAY + 1)
    hold(client, alice, court, MONDAY + 2)  # two active bookings
    assert series(client, alice, recurring_body(court.id, MONDAY, 10)).status_code == 201


def test_a_series_is_refused_at_the_limit_and_saves_nothing(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    for day in (8, 9, 10):
        hold(client, alice, court, day)
    response = series(client, alice, recurring_body(court.id, MONDAY, 10))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "booking_limit_reached"
    assert count(session, RecurringSeries) == 0
    assert count(session, Booking) == 3


def test_a_series_counts_as_one_booking_however_many_weeks(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    assert (
        series(client, alice, recurring_body(court.id, MONDAY, 10, occurrences=12)).status_code
        == 201
    )
    assert hold(client, alice, court, 8).status_code == 201  # series (1) + hold (1)
    assert hold(client, alice, court, 9).status_code == 201  # series (1) + holds (2)
    assert hold(client, alice, court, 10).status_code == 409  # a 4th


# -------------------------------------------------------------- safe to retry


def test_sending_the_same_series_again_returns_the_same_series(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    first = series(client, alice, recurring_body(court.id, MONDAY, 10))
    retry = series(client, alice, recurring_body(court.id, MONDAY, 10))
    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json() == first.json()
    assert count(session, RecurringSeries) == 1
    assert count(session, Booking) == 4


# ----------------------------------------------------- after it exists


def test_one_week_can_be_cancelled_on_its_own(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    body = series(client, alice, recurring_body(court.id, MONDAY, 10)).json()
    second_week = body["bookings"][1]["id"]

    cancelled = client.post(f"/bookings/{second_week}/cancel", headers=as_member(alice))
    assert cancelled.status_code == 200
    assert cancelled.json()["refund_amount"] == 20000  # days of notice: full refund

    assert hold(client, bob, court, MONDAY + 7).status_code == 201  # that week is free now
    assert hold(client, bob, court, MONDAY + 14).status_code == 409  # the others still taken


# ----------------------------------------------------------------- refusals


@pytest.mark.parametrize("weeks", [0, 13])
def test_the_number_of_weeks_is_limited(
    client: TestClient, world: tuple[Court, Member, Member], weeks: int
) -> None:
    court, alice, _ = world
    assert (
        series(client, alice, recurring_body(court.id, MONDAY, 10, occurrences=weeks)).status_code
        == 422
    )


@pytest.mark.parametrize(
    ("hour", "hours"),
    [(10, 4), (5, 1), (21, 2)],
    ids=["too-long", "before-opening", "past-closing"],
)
def test_an_invalid_first_slot_refuses_the_series(
    client: TestClient,
    session: Session,
    world: tuple[Court, Member, Member],
    hour: int,
    hours: int,
) -> None:
    court, alice, _ = world
    response = series(client, alice, recurring_body(court.id, MONDAY, hour, hours))
    assert response.status_code == 422
    assert count(session, Booking) == 0


def test_a_series_cannot_start_in_the_past(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    clock.advance(timedelta(days=30))
    assert series(client, alice, recurring_body(court.id, MONDAY, 10)).status_code == 422


def test_unknown_court_member_and_missing_header(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    assert series(client, alice, recurring_body(uuid.uuid4(), MONDAY, 10)).status_code == 404
    stranger = {"X-Member-Id": str(uuid.uuid4())}
    response = client.post(
        "/recurring-bookings", json=recurring_body(court.id, MONDAY, 10), headers=stranger
    )
    assert response.status_code == 404
    response = client.post("/recurring-bookings", json=recurring_body(court.id, MONDAY, 10))
    assert response.status_code == 401


def test_cancelled_weeks_are_not_counted_as_confirmed(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    body = series(client, alice, recurring_body(court.id, MONDAY, 10, occurrences=2)).json()
    for booking in body["bookings"]:
        client.post(f"/bookings/{booking['id']}/cancel", headers=as_member(alice))
    statuses = set(session.scalars(select(Booking.status)))
    assert statuses == {BookingStatus.CANCELLED}
    # Everything is cancelled, so a fresh identical series is allowed.
    assert (
        series(client, alice, recurring_body(court.id, MONDAY, 10, occurrences=2)).status_code
        == 201
    )
