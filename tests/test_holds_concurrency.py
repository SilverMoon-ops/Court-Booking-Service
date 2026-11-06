"""POST /holds under simultaneous requests, through the real app and the real database.

Each request runs on its own thread with its own HTTP client and database
connection, and all of them are released at the same instant.
"""

import uuid
from collections import Counter

from httpx import Response
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Booking
from tests.conftest import CommittedCourt
from tests.helpers import fire_together, future_body


def error_codes(responses: list[Response]) -> Counter[str]:
    return Counter(r.json()["error"]["code"] for r in responses if r.status_code >= 400)


def stored(court_id: uuid.UUID, member_id: uuid.UUID | None = None) -> int:
    query = select(func.count()).select_from(Booking).where(Booking.court_id == court_id)
    if member_id is not None:
        query = query.where(Booking.member_id == member_id)
    with SessionLocal() as db:
        return db.scalar(query) or 0


def test_twenty_members_race_for_one_slot_and_exactly_one_wins(
    committed_court: CommittedCourt,
) -> None:
    body = future_body(committed_court.court_id, days_ahead=3, hour=10)
    responses = fire_together(
        [(member, "/holds", body) for member in committed_court.member_ids[:20]]
    )

    statuses = Counter(r.status_code for r in responses)
    assert statuses == {201: 1, 409: 19}, statuses
    assert error_codes(responses) == {"slot_unavailable": 19}
    assert stored(committed_court.court_id) == 1


def test_one_member_cannot_exceed_three_bookings_by_sending_requests_in_parallel(
    committed_court: CommittedCourt,
) -> None:
    member = committed_court.member_ids[0]
    # Six different days, so the slots never conflict: only the limit can refuse them.
    requests = [
        (member, "/holds", future_body(committed_court.court_id, days_ahead=3 + day, hour=10))
        for day in range(6)
    ]
    responses = fire_together(requests)

    statuses = Counter(r.status_code for r in responses)
    assert statuses == {201: 3, 409: 3}, statuses
    assert error_codes(responses) == {"booking_limit_reached": 3}
    assert stored(committed_court.court_id, member) == 3


def test_ten_parallel_retries_of_the_same_hold_make_one_hold(
    committed_court: CommittedCourt,
) -> None:
    member = committed_court.member_ids[0]
    body = future_body(committed_court.court_id, days_ahead=3, hour=10)
    responses = fire_together([(member, "/holds", body)] * 10)

    statuses = Counter(r.status_code for r in responses)
    assert statuses == {201: 1, 200: 9}, statuses
    assert len({r.json()["id"] for r in responses}) == 1  # everyone got the same hold
    assert stored(committed_court.court_id) == 1
