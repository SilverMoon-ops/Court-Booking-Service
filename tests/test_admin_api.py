import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.helpers import STAFF_HEADERS, FixedClock, hold_body, recurring_body

MONDAY = 7  # 7 January 2030


def venue_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Lakeside Club",
        "timezone": "Asia/Kolkata",
        "open_hour": 6,
        "close_hour": 22,
        "offpeak_rate": 20000,
        "peak_rate": 40000,
        "peak_windows": [{"weekday": 0, "start_hour": 18, "end_hour": 22}],
    }
    return {**body, **changes}


def staff(client: TestClient, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    return client.request(method, path, json=body, headers=STAFF_HEADERS)


def make_venue(client: TestClient, **changes: Any) -> dict[str, Any]:
    response = staff(client, "POST", "/venues", venue_body(**changes))
    assert response.status_code == 201, response.text
    return dict(response.json())


def make_court(client: TestClient, venue_id: str, name: str = "Court 1") -> dict[str, Any]:
    response = staff(client, "POST", f"/venues/{venue_id}/courts", {"name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


def make_member(client: TestClient, name: str = "Asha") -> dict[str, Any]:
    response = staff(client, "POST", "/members", {"name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


# ------------------------------------------------------------ who may do this


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/venues", venue_body()),
        ("GET", "/venues", None),
        ("PUT", f"/venues/{uuid.uuid4()}/pricing", {"offpeak_rate": 1, "peak_rate": 2}),
        ("POST", f"/venues/{uuid.uuid4()}/courts", {"name": "Court 1"}),
        ("POST", "/members", {"name": "Asha"}),
    ],
)
def test_every_admin_endpoint_needs_the_staff_key(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    missing = client.request(method, path, json=body)
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "staff_required"

    wrong = client.request(method, path, json=body, headers={"X-Staff-Key": "guess"})
    assert wrong.status_code == 403

    # A member's id is not a staff key.
    member = client.request(method, path, json=body, headers={"X-Member-Id": str(uuid.uuid4())})
    assert member.status_code == 401


# ------------------------------------------------------------------- venues


def test_a_venue_is_created_with_its_peak_windows(client: TestClient) -> None:
    response = staff(client, "POST", "/venues", venue_body(name="  Lakeside Club  "))
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Lakeside Club"  # surrounding spaces are trimmed
    assert (body["open_hour"], body["close_hour"]) == (6, 22)
    assert (body["offpeak_rate"], body["peak_rate"]) == (20000, 40000)
    assert body["peak_windows"] == [{"weekday": 0, "start_hour": 18, "end_hour": 22}]
    assert body["courts"] == []
    uuid.UUID(body["id"])


def test_a_venue_name_can_only_be_used_once(client: TestClient) -> None:
    make_venue(client)
    response = staff(client, "POST", "/venues", venue_body())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_exists"


@pytest.mark.parametrize(
    "changes",
    [
        {"timezone": "Mars/Olympus"},
        {"timezone": ""},
        {"timezone": "../../etc/passwd"},
        {"open_hour": 22, "close_hour": 6},
        {"open_hour": 8, "close_hour": 8},
        {"open_hour": -1},
        {"open_hour": 24},
        {"close_hour": 25},
        {"close_hour": 0},
        {"offpeak_rate": -1},
        {"peak_rate": -5},
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"peak_windows": [{"weekday": 7, "start_hour": 18, "end_hour": 22}]},
        {"peak_windows": [{"weekday": 0, "start_hour": 22, "end_hour": 18}]},
        {"peak_windows": [{"weekday": 0, "start_hour": 18, "end_hour": 18}]},
        {"peak_windows": [{"weekday": 0, "start_hour": 18, "end_hour": 25}]},
        {
            "peak_windows": [
                {"weekday": 0, "start_hour": 17, "end_hour": 20},
                {"weekday": 0, "start_hour": 19, "end_hour": 22},
            ]
        },
    ],
)
def test_invalid_venues_are_refused_and_nothing_is_saved(
    client: TestClient, changes: dict[str, Any]
) -> None:
    response = staff(client, "POST", "/venues", venue_body(**changes))
    assert response.status_code == 422, changes
    assert staff(client, "GET", "/venues").json() == []


def test_windows_on_different_days_or_back_to_back_are_allowed(client: TestClient) -> None:
    windows = [
        {"weekday": 0, "start_hour": 17, "end_hour": 19},
        {"weekday": 0, "start_hour": 19, "end_hour": 22},  # starts exactly where the other ends
        {"weekday": 1, "start_hour": 17, "end_hour": 20},
    ]
    assert staff(client, "POST", "/venues", venue_body(peak_windows=windows)).status_code == 201


def test_a_venue_without_peak_windows_is_allowed(client: TestClient) -> None:
    body = venue_body()
    del body["peak_windows"]
    response = staff(client, "POST", "/venues", body)
    assert response.status_code == 201
    assert response.json()["peak_windows"] == []


def test_venues_are_listed_with_their_courts_and_windows(client: TestClient) -> None:
    zed = make_venue(client, name="Zed Club", peak_windows=[])
    alpha = make_venue(client, name="Alpha Club")
    make_court(client, alpha["id"], "Court B")
    make_court(client, alpha["id"], "Court A")

    venues = staff(client, "GET", "/venues").json()
    assert [v["name"] for v in venues] == ["Alpha Club", "Zed Club"]  # alphabetical
    assert [c["name"] for c in venues[0]["courts"]] == ["Court A", "Court B"]
    assert len(venues[0]["peak_windows"]) == 1
    assert venues[1]["id"] == zed["id"]
    assert venues[1]["courts"] == []


# ------------------------------------------------------------------- courts


def test_a_court_is_added_to_a_venue(client: TestClient) -> None:
    venue = make_venue(client)
    response = staff(client, "POST", f"/venues/{venue['id']}/courts", {"name": "Court 1"})
    assert response.status_code == 201
    assert response.json()["venue_id"] == venue["id"]
    assert response.json()["name"] == "Court 1"


def test_court_names_are_unique_within_a_venue_only(client: TestClient) -> None:
    first = make_venue(client, name="First")
    second = make_venue(client, name="Second")
    make_court(client, first["id"], "Court 1")

    duplicate = staff(client, "POST", f"/venues/{first['id']}/courts", {"name": "Court 1"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "already_exists"

    make_court(client, second["id"], "Court 1")  # the same name at another venue is fine


def test_a_court_for_an_unknown_venue_is_not_found(client: TestClient) -> None:
    response = staff(client, "POST", f"/venues/{uuid.uuid4()}/courts", {"name": "Court 1"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_an_empty_court_name_is_refused(client: TestClient) -> None:
    venue = make_venue(client)
    assert staff(client, "POST", f"/venues/{venue['id']}/courts", {"name": " "}).status_code == 422


# ------------------------------------------------------------------ members


def test_a_member_is_created_and_gets_an_id(client: TestClient) -> None:
    response = staff(client, "POST", "/members", {"name": "Asha"})
    assert response.status_code == 201
    assert response.json()["name"] == "Asha"
    uuid.UUID(response.json()["id"])


def test_two_members_may_share_a_name_but_never_an_id(client: TestClient) -> None:
    first, second = make_member(client, "Asha"), make_member(client, "Asha")
    assert first["id"] != second["id"]


def test_an_empty_member_name_is_refused(client: TestClient) -> None:
    assert staff(client, "POST", "/members", {"name": ""}).status_code == 422


# ------------------------------------------------------------------ pricing


def test_pricing_replaces_the_rates_and_all_peak_windows(client: TestClient) -> None:
    venue = make_venue(client)
    new = {
        "offpeak_rate": 30000,
        "peak_rate": 55000,
        "peak_windows": [{"weekday": 1, "start_hour": 17, "end_hour": 21}],
    }
    response = staff(client, "PUT", f"/venues/{venue['id']}/pricing", new)
    assert response.status_code == 200
    body = response.json()
    assert (body["offpeak_rate"], body["peak_rate"]) == (30000, 55000)
    assert body["peak_windows"] == new["peak_windows"]  # the old Monday window is gone
    assert body["open_hour"] == 6  # opening hours are not touched
    stored = staff(client, "GET", "/venues").json()[0]  # what a later read shows
    assert stored["peak_windows"] == new["peak_windows"]
    assert (stored["offpeak_rate"], stored["peak_rate"]) == (30000, 55000)

    cleared = staff(client, "PUT", f"/venues/{venue['id']}/pricing", {**new, "peak_windows": []})
    assert cleared.json()["peak_windows"] == []
    assert staff(client, "GET", "/venues").json()[0]["peak_windows"] == []


def test_pricing_for_an_unknown_venue_is_not_found(client: TestClient) -> None:
    body = {"offpeak_rate": 1, "peak_rate": 2}
    assert staff(client, "PUT", f"/venues/{uuid.uuid4()}/pricing", body).status_code == 404


def test_invalid_pricing_is_refused_and_leaves_the_old_pricing(client: TestClient) -> None:
    venue = make_venue(client)
    bad = {
        "offpeak_rate": 1,
        "peak_rate": 2,
        "peak_windows": [{"weekday": 9, "start_hour": 1, "end_hour": 2}],
    }
    assert staff(client, "PUT", f"/venues/{venue['id']}/pricing", bad).status_code == 422
    stored = staff(client, "GET", "/venues").json()[0]
    assert (stored["offpeak_rate"], len(stored["peak_windows"])) == (20000, 1)


def test_a_price_change_applies_to_new_holds_and_not_to_old_ones(client: TestClient) -> None:
    venue = make_venue(client, peak_windows=[])
    court, member = make_court(client, venue["id"]), make_member(client)
    as_member = {"X-Member-Id": member["id"]}

    first = client.post(
        "/holds", json=hold_body(uuid.UUID(court["id"]), MONDAY, 10), headers=as_member
    )
    assert first.json()["price"] == 20000

    staff(
        client, "PUT", f"/venues/{venue['id']}/pricing", {"offpeak_rate": 30000, "peak_rate": 40000}
    )

    second = client.post(
        "/holds", json=hold_body(uuid.UUID(court["id"]), MONDAY, 11), headers=as_member
    )
    assert second.json()["price"] == 30000  # the new rate
    confirmed = client.post(f"/holds/{first.json()['id']}/confirm", headers=as_member)
    assert confirmed.json()["price"] == 20000  # the first hold keeps the price it was given


# ------------------- the whole system, set up and used through the API alone


def test_the_whole_system_can_be_set_up_and_used_through_the_api_alone(
    client: TestClient, clock: FixedClock
) -> None:
    venue = make_venue(client)
    court = make_court(client, venue["id"])
    member = make_member(client)
    court_id = uuid.UUID(court["id"])
    as_member = {"X-Member-Id": member["id"]}

    slots = client.get(f"/courts/{court['id']}/availability", params={"date": "2030-01-07"}).json()
    assert len(slots["slots"]) == 16
    evening = next(s for s in slots["slots"] if s["start_at"][11:13] == "18")
    assert evening["price"] == 40000  # Monday evening is a peak window

    held = client.post("/holds", json=hold_body(court_id, MONDAY, 18), headers=as_member).json()
    assert held["price"] == 40000
    assert (
        client.post(f"/holds/{held['id']}/confirm", headers=as_member).json()["status"]
        == "confirmed"
    )
    cancelled = client.post(f"/bookings/{held['id']}/cancel", headers=as_member).json()
    assert cancelled["refund_amount"] == 40000  # more than 24 hours ahead

    series = client.post(
        "/recurring-bookings",
        json=recurring_body(court_id, MONDAY, 17, hours=2, occurrences=3),
        headers=as_member,
    )
    assert series.status_code == 201
    assert [b["price"] for b in series.json()["bookings"]] == [60000, 60000, 60000]

    report = staff(client, "GET", f"/staff/utilization?week=2030-W02&venue_id={venue['id']}")
    court_report = report.json()["venues"][0]["courts"][0]
    assert court_report["court_id"] == court["id"]
    assert court_report["booked_hours"] == 2  # the first week of the series
