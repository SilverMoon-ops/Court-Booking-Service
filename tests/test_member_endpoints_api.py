import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Booking, Court, Member, Venue
from tests.helpers import FixedClock, hold_body, recurring_body
from tests.test_schema import make_court


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


def my_bookings(client: TestClient, member: Member, **params: Any) -> Any:
    return client.get("/bookings", params=params, headers=as_member(member))


# -------------------------------------------------------------- GET /courts


def test_anyone_can_list_the_courts_without_logging_in(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, _, _ = world
    response = client.get("/courts")  # no header of any kind
    assert response.status_code == 200
    [listed] = response.json()
    assert listed["id"] == str(court.id)
    assert (listed["name"], listed["venue_name"]) == ("Court 1", "Main Hall")
    assert listed["venue_id"] == str(court.venue_id)
    assert (listed["timezone"], listed["open_hour"], listed["close_hour"]) == (
        "Asia/Kolkata",
        6,
        22,
    )


def test_courts_are_listed_by_venue_name_then_court_name(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, _, _ = world  # Main Hall / Court 1
    session.add(Court(venue_id=court.venue_id, name="Court 0"))
    other = Venue(
        name="Annex",
        timezone="Asia/Kolkata",
        open_hour=8,
        close_hour=20,
        offpeak_rate=1,
        peak_rate=2,
    )
    session.add(other)
    session.flush()
    session.add(Court(venue_id=other.id, name="Court Z"))
    session.flush()

    names = [(c["venue_name"], c["name"]) for c in client.get("/courts").json()]
    assert names == [("Annex", "Court Z"), ("Main Hall", "Court 0"), ("Main Hall", "Court 1")]


def test_courts_can_be_filtered_by_venue(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, _, _ = world
    other = Venue(
        name="Annex",
        timezone="Asia/Kolkata",
        open_hour=8,
        close_hour=20,
        offpeak_rate=1,
        peak_rate=2,
    )
    session.add(other)
    session.flush()
    session.add(Court(venue_id=other.id, name="Court Z"))
    session.flush()

    only = client.get("/courts", params={"venue_id": str(court.venue_id)}).json()
    assert [c["venue_name"] for c in only] == ["Main Hall"]

    unknown = client.get("/courts", params={"venue_id": str(uuid.uuid4())})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"


def test_no_courts_is_an_empty_list(client: TestClient) -> None:
    assert client.get("/courts").json() == []


def test_a_member_can_go_from_the_court_list_to_a_booking(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    """Browse, pick a court, look at its day, and book it, using nothing but the API's answers."""
    _, alice, _ = world
    chosen = client.get("/courts").json()[0]
    day = client.get(f"/courts/{chosen['id']}/availability", params={"date": "2030-01-07"})
    assert day.status_code == 200
    free = next(s for s in day.json()["slots"] if s["status"] == "free")

    body = {"court_id": chosen["id"], "start_at": free["start_at"], "end_at": free["end_at"]}
    assert client.post("/holds", json=body, headers=as_member(alice)).status_code == 201


# ------------------------------------------------------------- GET /bookings


def test_the_member_header_is_required(client: TestClient) -> None:
    response = client.get("/bookings")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "member_required"


def test_an_unknown_member_is_not_found(client: TestClient) -> None:
    response = client.get("/bookings", headers={"X-Member-Id": str(uuid.uuid4())})
    assert response.status_code == 404


def test_a_member_with_nothing_booked_gets_an_empty_list(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    _, alice, _ = world
    assert my_bookings(client, alice).json() == []


def test_a_member_sees_only_their_own_bookings(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    mine = hold(client, alice, court, 7).json()
    hold(client, bob, court, 8)

    listed = my_bookings(client, alice).json()
    assert [b["id"] for b in listed] == [mine["id"]]
    assert {b["member_id"] for b in listed} == {str(alice.id)}
    assert len(my_bookings(client, bob).json()) == 1


def test_bookings_come_earliest_first(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    late, early, middle = (hold(client, alice, court, d).json()["id"] for d in (9, 7, 8))
    assert [b["id"] for b in my_bookings(client, alice).json()] == [early, middle, late]


def test_a_lost_hold_id_can_be_found_again_and_confirmed(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    hold(client, alice, court, 7)  # the response is "lost"
    [found] = my_bookings(client, alice, status="held").json()
    confirmed = client.post(f"/holds/{found['id']}/confirm", headers=as_member(alice))
    assert confirmed.json()["status"] == "confirmed"


def test_bookings_can_be_filtered_by_status(
    client: TestClient, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    court, alice, _ = world
    confirmed_id = hold(client, alice, court, 7).json()["id"]
    client.post(f"/holds/{confirmed_id}/confirm", headers=as_member(alice))
    cancelled_id = hold(client, alice, court, 8).json()["id"]
    client.post(f"/holds/{cancelled_id}/confirm", headers=as_member(alice))  # paid, then cancelled
    client.post(f"/bookings/{cancelled_id}/cancel", headers=as_member(alice))
    held_id = hold(client, alice, court, 9).json()["id"]

    def ids(**params: Any) -> list[str]:
        return [b["id"] for b in my_bookings(client, alice, **params).json()]

    assert ids(status="confirmed") == [confirmed_id]
    assert ids(status="cancelled") == [cancelled_id]
    assert ids(status="held") == [held_id]
    assert ids(status="expired") == []
    assert ids() == [confirmed_id, cancelled_id, held_id]

    cancelled = my_bookings(client, alice, status="cancelled").json()[0]
    assert cancelled["refund_amount"] == 20000  # the refund is visible too


def test_a_hold_that_ran_out_is_shown_as_expired_even_if_nothing_tidied_it(
    client: TestClient,
    session: Session,
    world: tuple[Court, Member, Member],
    clock: FixedClock,
) -> None:
    court, alice, _ = world
    held = hold(client, alice, court, 7).json()
    clock.advance(timedelta(minutes=11))

    row = session.get(Booking, uuid.UUID(held["id"]))
    assert row is not None and row.status == "held"  # the table still says held

    assert [b["status"] for b in my_bookings(client, alice).json()] == ["expired"]
    assert [b["id"] for b in my_bookings(client, alice, status="expired").json()] == [held["id"]]
    assert my_bookings(client, alice, status="held").json() == []


def test_a_recurring_series_shows_each_week_with_its_series_id(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    series = client.post(
        "/recurring-bookings",
        json=recurring_body(court.id, 7, 10, occurrences=3),
        headers=as_member(alice),
    ).json()
    listed = my_bookings(client, alice).json()
    assert len(listed) == 3
    assert {b["series_id"] for b in listed} == {series["id"]}
    assert {b["status"] for b in listed} == {"confirmed"}


def test_the_limit_and_bad_filters_are_checked(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    for day in (7, 8, 9):
        hold(client, alice, court, day)
    assert len(my_bookings(client, alice, limit=2).json()) == 2
    assert my_bookings(client, alice, limit=0).status_code == 422
    assert my_bookings(client, alice, limit=501).status_code == 422
    assert my_bookings(client, alice, status="pending").status_code == 422
