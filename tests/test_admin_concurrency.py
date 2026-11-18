"""Staff endpoints under simultaneous requests: names stay unique, pricing never gets mixed."""

import uuid
from collections import Counter
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.main import app
from app.models import Court, PeakWindow, Venue
from tests.helpers import STAFF_HEADERS, fire_staff


@pytest.fixture
def prefix(migrated_db: None) -> Iterator[str]:
    """A unique name prefix. Everything created with it is deleted afterwards."""
    name = f"race-{uuid.uuid4().hex[:8]}"
    yield name
    with SessionLocal() as db:
        ids = list(db.scalars(select(Venue.id).where(Venue.name.like(f"{name}%"))))
        db.execute(delete(PeakWindow).where(PeakWindow.venue_id.in_(ids)))
        db.execute(delete(Court).where(Court.venue_id.in_(ids)))
        db.execute(delete(Venue).where(Venue.id.in_(ids)))
        db.commit()


def venue_body(name: str, **changes: object) -> dict[str, object]:
    return {
        "name": name,
        "timezone": "Asia/Kolkata",
        "open_hour": 6,
        "close_hour": 22,
        "offpeak_rate": 20000,
        "peak_rate": 40000,
        "peak_windows": [{"weekday": 0, "start_hour": 18, "end_hour": 22}],
        **changes,
    }


def create_venue(name: str) -> str:
    response = TestClient(app).post("/venues", json=venue_body(name), headers=STAFF_HEADERS)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_ten_staff_creating_the_same_venue_at_once_make_exactly_one(prefix: str) -> None:
    responses = fire_staff([("POST", "/venues", venue_body(prefix))] * 10)

    assert Counter(r.status_code for r in responses) == {201: 1, 409: 9}
    assert {r.json()["error"]["code"] for r in responses if r.status_code == 409} == {
        "already_exists"
    }
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(Venue).where(Venue.name == prefix))
    assert count == 1


def test_ten_staff_adding_the_same_court_at_once_make_exactly_one(prefix: str) -> None:
    venue_id = create_venue(prefix)
    responses = fire_staff([("POST", f"/venues/{venue_id}/courts", {"name": "Court 1"})] * 10)

    assert Counter(r.status_code for r in responses) == {201: 1, 409: 9}
    with SessionLocal() as db:
        count = db.scalar(
            select(func.count()).select_from(Court).where(Court.venue_id == uuid.UUID(venue_id))
        )
    assert count == 1


def test_simultaneous_window_updates_never_mix_two_requests(prefix: str) -> None:
    """Ten different peak-window lists are sent at once, all with the same rates.

    The result must be exactly ONE complete list: both of its windows, and nothing left over
    from another request. (Because the rates never change, nothing else would stop two updates
    from running side by side, so this is what the row lock is for.)"""
    venue_id = uuid.UUID(create_venue(prefix))
    requests = [
        (
            "PUT",
            f"/venues/{venue_id}/pricing",
            {
                "offpeak_rate": 20000,
                "peak_rate": 40000,
                "peak_windows": [
                    {"weekday": i % 7, "start_hour": 6, "end_hour": 8},
                    {"weekday": i % 7, "start_hour": 18, "end_hour": 22},
                ],
            },
        )
        for i in range(10)
    ]
    responses = fire_staff(requests)
    assert Counter(r.status_code for r in responses) == {200: 10}

    with SessionLocal() as db:
        windows = list(
            db.scalars(
                select(PeakWindow)
                .where(PeakWindow.venue_id == venue_id)
                .order_by(PeakWindow.start_hour)
            )
        )
    stored = [(w.weekday, w.start_hour, w.end_hour) for w in windows]
    assert len(stored) == 2, stored  # not 4 or 6: no leftovers from other requests
    day = stored[0][0]
    assert stored == [(day, 6, 8), (day, 18, 22)], stored  # both windows from the same request
