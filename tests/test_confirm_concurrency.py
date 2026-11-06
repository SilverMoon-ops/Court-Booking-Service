"""Guarantee G2: a client that retries a confirm never ends up with two bookings.

Here the retries do not arrive politely one after another: they all arrive at once.
"""

from collections import Counter
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import Booking
from tests.conftest import CommittedCourt
from tests.helpers import fire_together, future_body


def make_hold(court_id: UUID, member_id: UUID, days_ahead: int = 3) -> str:
    response = TestClient(app).post(
        "/holds",
        json=future_body(court_id, days_ahead=days_ahead, hour=10),
        headers={"X-Member-Id": str(member_id)},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def rows_for(court_id: UUID) -> list[Booking]:
    with SessionLocal() as db:
        return list(db.scalars(select(Booking).where(Booking.court_id == court_id)))


def test_twenty_parallel_confirms_of_one_hold_make_one_booking(
    committed_court: CommittedCourt,
) -> None:
    member = committed_court.member_ids[0]
    hold_id = make_hold(committed_court.court_id, member)

    responses = fire_together([(member, f"/holds/{hold_id}/confirm", None)] * 20)

    assert Counter(r.status_code for r in responses) == {200: 20}
    assert {r.json()["id"] for r in responses} == {hold_id}
    assert len({r.json()["confirmed_at"] for r in responses}) == 1  # confirmed exactly once
    rows = rows_for(committed_court.court_id)
    assert len(rows) == 1
    assert rows[0].status == "confirmed"


def test_confirms_of_different_holds_do_not_interfere(committed_court: CommittedCourt) -> None:
    members = committed_court.member_ids[:3]
    holds = [
        make_hold(committed_court.court_id, m, days_ahead=3 + i) for i, m in enumerate(members)
    ]

    responses = fire_together(
        [(m, f"/holds/{h}/confirm", None) for m, h in zip(members, holds, strict=True)] * 5
    )

    assert Counter(r.status_code for r in responses) == {200: 15}
    with SessionLocal() as db:
        confirmed = db.scalar(
            select(func.count())
            .select_from(Booking)
            .where(Booking.court_id == committed_court.court_id, Booking.status == "confirmed")
        )
    assert confirmed == 3
