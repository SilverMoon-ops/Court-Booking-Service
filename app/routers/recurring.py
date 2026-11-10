from typing import Any

from fastapi import APIRouter, Response

from app.deps import ClockDep, DbSession, MemberId
from app.schemas import BookingOut, ErrorResponse, RecurringRequest, SeriesOut
from app.services.recurring import create_recurring

router = APIRouter()

RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"model": SeriesOut, "description": "A retry: this series already exists"},
    401: {"model": ErrorResponse, "description": "Missing or invalid X-Member-Id header"},
    404: {"model": ErrorResponse, "description": "Court or member not found"},
    409: {
        "model": ErrorResponse,
        "description": "A week is taken, or the booking limit is reached",
    },
}


@router.post("/recurring-bookings", response_model=SeriesOut, status_code=201, responses=RESPONSES)
def post_recurring(
    body: RecurringRequest,
    response: Response,
    member_id: MemberId,
    db: DbSession,
    clock: ClockDep,
) -> SeriesOut:
    """Book the same time every week. All weeks are booked, or none are."""
    series, bookings, created = create_recurring(
        db, clock, member_id, body.court_id, body.start_at, body.end_at, body.occurrences
    )
    if not created:
        response.status_code = 200
    return SeriesOut(
        id=series.id,
        court_id=series.court_id,
        member_id=series.member_id,
        weekday=series.weekday,
        start_hour=series.start_hour,
        duration_hours=series.duration_hours,
        first_date=series.first_date,
        occurrences=series.occurrences,
        bookings=[BookingOut.model_validate(b) for b in bookings],
    )
