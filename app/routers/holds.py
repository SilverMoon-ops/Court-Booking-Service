import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Response

from app.config import get_settings
from app.deps import ClockDep, DbSession, MemberId
from app.models import Booking
from app.schemas import BookingOut, ErrorResponse, HoldRequest
from app.services.holds import confirm_hold, create_hold

router = APIRouter()

ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing or invalid X-Member-Id header"},
    404: {"model": ErrorResponse, "description": "Not found (or not yours)"},
    409: {"model": ErrorResponse, "description": "Conflict"},
    410: {"model": ErrorResponse, "description": "The hold has expired"},
}


@router.post(
    "/holds",
    response_model=BookingOut,
    status_code=201,
    responses={
        200: {"model": BookingOut, "description": "A retry: you already hold this exact slot"},
        **{code: ERRORS[code] for code in (401, 404, 409)},
    },
)
def post_hold(
    body: HoldRequest,
    response: Response,
    member_id: MemberId,
    db: DbSession,
    clock: ClockDep,
) -> Booking:
    ttl = timedelta(minutes=get_settings().hold_ttl_minutes)
    hold, created = create_hold(
        db, clock, member_id, body.court_id, body.start_at, body.end_at, ttl
    )
    if not created:
        response.status_code = 200  # a retry gets the same hold back, not a second one
    return hold


@router.post(
    "/holds/{hold_id}/confirm",
    response_model=BookingOut,
    responses={code: ERRORS[code] for code in (401, 404, 409, 410)},
)
def post_confirm(
    hold_id: uuid.UUID, member_id: MemberId, db: DbSession, clock: ClockDep
) -> Booking:
    """Confirm a hold. Calling it again for an already confirmed booking is safe."""
    return confirm_hold(db, clock, member_id, hold_id)
