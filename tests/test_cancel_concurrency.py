"""Cancelling when several requests arrive at once."""

from collections import Counter
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Booking
from tests.conftest import CommittedCourt
from tests.helpers import fire_together, future_body


def make_hold(court_id: UUID, member_id: UUID, days_ahead: int = 3) -> tuple[str, int]:
    response = TestClient(app).post(
        "/holds",
        json=future_body(court_id, days_ahead=days_ahead, hour=10),
        headers={"X-Member-Id": str(member_id)},
    )
    assert response.status_code == 201
    return str(response.json()["id"]), int(response.json()["price"])


def confirm(member_id: UUID, hold_id: str) -> None:
    response = TestClient(app).post(
        f"/holds/{hold_id}/confirm", headers={"X-Member-Id": str(member_id)}
    )
    assert response.status_code == 200


def load(booking_id: str) -> Booking:
    with SessionLocal() as db:
        row = db.scalar(select(Booking).where(Booking.id == UUID(booking_id)))
    assert row is not None
    return row


def test_twenty_parallel_cancels_cancel_once_and_agree(committed_court: CommittedCourt) -> None:
    member = committed_court.member_ids[0]
    booking_id, price = make_hold(committed_court.court_id, member)
    confirm(member, booking_id)

    responses = fire_together([(member, f"/bookings/{booking_id}/cancel", None)] * 20)

    assert Counter(r.status_code for r in responses) == {200: 20}
    assert len({r.json()["cancelled_at"] for r in responses}) == 1  # cancelled exactly once
    assert {r.json()["refund_amount"] for r in responses} == {price}
    assert load(booking_id).status == "cancelled"


def test_cancel_and_confirm_racing_always_end_in_a_consistent_state(
    committed_court: CommittedCourt,
) -> None:
    """The same hold is confirmed and cancelled at the same instant, ten times.

    Either the confirm gets in first (the booking is confirmed, then cancelled, and the
    refund is the full price) or the cancel does (the hold is released, the confirm is
    refused, and there is no refund). Nothing in between is allowed.
    """
    member = committed_court.member_ids[0]
    outcomes: Counter[str] = Counter()

    for round_number in range(10):
        hold_id, price = make_hold(committed_court.court_id, member, days_ahead=3 + round_number)
        confirm_response, cancel_response = fire_together(
            [
                (member, f"/holds/{hold_id}/confirm", None),
                (member, f"/bookings/{hold_id}/cancel", None),
            ]
        )

        assert cancel_response.status_code == 200
        assert load(hold_id).status == "cancelled"  # always ends cancelled
        if confirm_response.status_code == 200:
            assert cancel_response.json()["refund_amount"] == price
            outcomes["confirmed then cancelled"] += 1
        else:
            assert confirm_response.status_code == 409
            assert cancel_response.json()["refund_amount"] == 0
            outcomes["cancelled before confirm"] += 1
    print(dict(outcomes))
