import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app
from app.models import Booking, BookingStatus, Court, Member, Venue
from app.policies import iso_week_label
from tests.helpers import (
    IST,
    STAFF_HEADERS,
    FixedClock,
    hold_body,
    recurring_body,
    slot_start,
)
from tests.test_schema import make_court

W02 = "2030-W02"  # Monday 7 to Sunday 13 January 2030
OPEN_HOURS_PER_WEEK = 16 * 7  # the test venue is open 06:00-22:00 every day


@pytest.fixture
def world(session: Session) -> tuple[Court, Member, Member]:
    court = make_court(session)
    alice, bob = Member(name="Alice"), Member(name="Bob")
    session.add_all([alice, bob])
    session.flush()
    return court, alice, bob


def as_member(member: Member) -> dict[str, str]:
    return {"X-Member-Id": str(member.id)}


def utilization(
    client: TestClient,
    week: str | None = W02,
    venue_id: uuid.UUID | None = None,
    headers: dict[str, str] | None = STAFF_HEADERS,
) -> Any:
    params: dict[str, str] = {}
    if week:
        params["week"] = week
    if venue_id:
        params["venue_id"] = str(venue_id)
    return client.get("/staff/utilization", params=params, headers=headers or {})


def confirmed(
    client: TestClient, member: Member, court: Court, day: int, hour: int, hours: int = 1
) -> str:
    body = hold_body(court.id, day, hour, hours)
    held = client.post("/holds", json=body, headers=as_member(member)).json()
    assert client.post(f"/holds/{held['id']}/confirm", headers=as_member(member)).status_code == 200
    return str(held["id"])


def only_court(response: Any) -> dict[str, Any]:
    courts = response.json()["venues"][0]["courts"]
    assert len(courts) == 1
    return dict(courts[0])


# ---------------------------------------------------------------- who may see it


def test_the_staff_key_is_required(client: TestClient, world: tuple[Court, Member, Member]) -> None:
    _, alice, _ = world
    missing = utilization(client, headers={})
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "staff_required"

    wrong = utilization(client, headers={"X-Staff-Key": "guess"})
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "forbidden"

    assert utilization(client, headers=as_member(alice)).status_code == 401  # a member is not staff
    assert utilization(client).status_code == 200


def test_staff_access_is_switched_off_if_no_key_is_configured(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(staff_api_key="")
    response = utilization(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "staff_not_configured"


# --------------------------------------------------------------- what is counted


def test_an_empty_week_shows_open_hours_and_zero_use(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    body = utilization(client).json()
    venue = body["venues"][0]
    assert (venue["week"], venue["week_start"], venue["week_end"]) == (
        W02,
        "2030-01-07",
        "2030-01-13",
    )
    assert venue["timezone"] == "Asia/Kolkata"
    court = venue["courts"][0]
    assert court["open_hours"] == OPEN_HOURS_PER_WEEK
    assert court["booked_hours"] == 0
    assert court["utilization"] == 0


def test_confirmed_hours_are_divided_by_open_hours(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    confirmed(client, alice, court, 7, 10, hours=2)  # Monday, 2 hours
    confirmed(client, bob, court, 8, 14)  # Tuesday, 1 hour
    result = only_court(utilization(client))
    assert result["booked_hours"] == 3
    assert result["utilization"] == round(3 / OPEN_HOURS_PER_WEEK, 4)  # 0.0268


def test_holds_cancelled_and_expired_bookings_do_not_count(
    client: TestClient,
    session: Session,
    world: tuple[Court, Member, Member],
    clock: FixedClock,
) -> None:
    court, alice, bob = world
    confirmed(client, alice, court, 7, 10)  # the only one that counts
    client.post("/holds", json=hold_body(court.id, 8, 10), headers=as_member(alice))  # a live hold
    cancelled = confirmed(client, alice, court, 9, 10)
    client.post(f"/bookings/{cancelled}/cancel", headers=as_member(alice))
    client.post("/holds", json=hold_body(court.id, 10, 10), headers=as_member(bob))
    clock.advance(timedelta(minutes=11))  # Bob's hold has now expired

    assert only_court(utilization(client))["booked_hours"] == 1


def test_bookings_in_other_weeks_are_counted_in_their_own_week(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    confirmed(client, alice, court, 14, 10)  # Monday 14 January, the following week
    assert only_court(utilization(client, "2030-W02"))["booked_hours"] == 0
    assert only_court(utilization(client, "2030-W03"))["booked_hours"] == 1


def test_a_recurring_series_counts_in_each_of_its_weeks(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    court, alice, _ = world
    client.post(
        "/recurring-bookings",
        json=recurring_body(court.id, 7, 10, occurrences=4),
        headers=as_member(alice),
    )
    weekly = [
        only_court(utilization(client, f"2030-W{n:02d}"))["booked_hours"] for n in range(2, 7)
    ]
    assert weekly == [1, 1, 1, 1, 0]  # weeks 2 to 5 have a booking; week 6 has none


def test_each_court_is_reported_separately_in_name_order(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, alice, bob = world
    other = Court(venue_id=court.venue_id, name="Court 2")
    session.add(other)
    session.flush()
    confirmed(client, alice, court, 7, 10, hours=3)
    confirmed(client, bob, other, 7, 10)

    courts = utilization(client).json()["venues"][0]["courts"]
    assert [(c["court_name"], c["booked_hours"]) for c in courts] == [
        ("Court 1", 3),
        ("Court 2", 1),
    ]


# --------------------------------------------------------------- venues


def test_several_venues_can_be_listed_or_filtered(
    client: TestClient, session: Session, world: tuple[Court, Member, Member]
) -> None:
    court, _, _ = world
    second = Venue(
        name="Zed Club",
        timezone="America/New_York",
        open_hour=8,
        close_hour=20,
        offpeak_rate=1,
        peak_rate=2,
    )
    session.add(second)
    session.flush()
    session.add(Court(venue_id=second.id, name="Court A"))
    session.flush()

    everything = utilization(client).json()["venues"]
    assert [v["venue_name"] for v in everything] == ["Main Hall", "Zed Club"]
    assert everything[1]["courts"][0]["open_hours"] == 12 * 7

    one = utilization(client, venue_id=court.venue_id).json()["venues"]
    assert [v["venue_name"] for v in one] == ["Main Hall"]

    unknown = utilization(client, venue_id=uuid.uuid4())
    assert unknown.status_code == 404


# ---------------------------- which week does a booking belong to?


def make_all_day_venue(session: Session) -> tuple[Court, Member]:
    venue = Venue(
        name="All Day",
        timezone="Asia/Kolkata",
        open_hour=0,
        close_hour=24,
        offpeak_rate=1,
        peak_rate=2,
    )
    session.add(venue)
    session.flush()
    court, member = Court(venue_id=venue.id, name="Court 1"), Member(name="Night owl")
    session.add_all([court, member])
    session.flush()
    return court, member


def confirmed_row(court: Court, member: Member, start: datetime) -> Booking:
    return Booking(
        member_id=member.id,
        court_id=court.id,
        start_at=start,
        end_at=start + timedelta(hours=1),
        status=BookingStatus.CONFIRMED,
        price=1,
    )


def test_the_week_changes_at_the_venues_midnight_not_utc_midnight(
    client: TestClient, session: Session
) -> None:
    court, member = make_all_day_venue(session)
    # Sunday 13 January, 23:00-24:00 in India (17:30 UTC): the last hour of week 2.
    # Monday 14 January, 00:00-01:00 in India is still SUNDAY 18:30 UTC: the first hour of week 3.
    session.add_all(
        [
            confirmed_row(court, member, datetime(2030, 1, 13, 23, tzinfo=IST)),
            confirmed_row(court, member, datetime(2030, 1, 14, 0, tzinfo=IST)),
        ]
    )
    session.flush()

    week2, week3 = utilization(client, "2030-W02"), utilization(client, "2030-W03")
    assert (only_court(week2)["booked_hours"], only_court(week3)["booked_hours"]) == (1, 1)
    assert only_court(week2)["open_hours"] == 24 * 7


def test_a_daylight_saving_week_has_an_hour_fewer_open_hours(
    client: TestClient, session: Session
) -> None:
    venue = Venue(
        name="Open Always",
        timezone="America/New_York",
        open_hour=0,
        close_hour=24,
        offpeak_rate=1,
        peak_rate=2,
    )
    session.add(venue)
    session.flush()
    session.add(Court(venue_id=venue.id, name="Court 1"))
    session.flush()

    spring = iso_week_label(date(2030, 3, 4))  # the week the clocks go forward
    assert only_court(utilization(client, spring))["open_hours"] == 24 * 7 - 1
    assert only_court(utilization(client, "2030-W05"))["open_hours"] == 24 * 7


def test_without_a_week_each_venue_reports_its_own_current_week(
    client: TestClient, session: Session, world: tuple[Court, Member, Member], clock: FixedClock
) -> None:
    ny = Venue(
        name="Zed Club",
        timezone="America/New_York",
        open_hour=8,
        close_hour=20,
        offpeak_rate=1,
        peak_rate=2,
    )
    session.add(ny)
    session.flush()
    session.add(Court(venue_id=ny.id, name="Court A"))
    session.flush()

    # Sunday 6 January 20:00 UTC: already Monday 01:30 in India, still Sunday 15:00 in New York.
    clock.advance(datetime(2030, 1, 6, 20, tzinfo=clock.now().tzinfo) - clock.now())
    venues = utilization(client, week=None).json()["venues"]
    assert [(v["venue_name"], v["week"]) for v in venues] == [
        ("Main Hall", "2030-W02"),
        ("Zed Club", "2030-W01"),
    ]


def test_a_bad_week_is_refused(client: TestClient, world: tuple[Court, Member, Member]) -> None:
    for bad in ("2030-02", "2030-W99", "nonsense", "2030-W53"):
        response = utilization(client, bad)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "invalid_week"


# ------------------------------------------- the two reports must agree


def test_the_report_agrees_with_what_availability_shows_as_taken(
    client: TestClient, world: tuple[Court, Member, Member]
) -> None:
    """Every confirmed hour in the week must appear as an unavailable slot on some day."""
    court, alice, bob = world
    confirmed(client, alice, court, 7, 10, hours=2)
    confirmed(client, alice, court, 9, 18, hours=3)
    confirmed(client, bob, court, 11, 6)
    confirmed(client, bob, court, 13, 21)

    unavailable = 0
    for day in range(7, 14):
        slots = client.get(
            f"/courts/{court.id}/availability",
            params={"date": slot_start(day, 0).date().isoformat()},
        ).json()["slots"]
        unavailable += sum(1 for s in slots if s["status"] == "unavailable")

    assert unavailable == 7
    assert only_court(utilization(client))["booked_hours"] == unavailable
