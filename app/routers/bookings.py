import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.deps import ClockDep, DbSession, MemberId
from app.models import Booking
from app.schemas import BookingOut, ErrorResponse
from app.services.cancel import cancel_booking
from app.services.my_bookings import StatusName, member_bookings

router = APIRouter()

CANCEL_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing or invalid X-Member-Id header"},
    404: {"model": ErrorResponse, "description": "Not found (or not yours)"},
    409: {"model": ErrorResponse, "description": "The booking has already started"},
    410: {"model": ErrorResponse, "description": "The hold had already expired"},
}


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut, responses=CANCEL_ERRORS)
def post_cancel(
    booking_id: uuid.UUID, member_id: MemberId, db: DbSession, clock: ClockDep
) -> Booking:
    """Cancel a booking. Full refund with at least 24 hours' notice, otherwise none.

    Calling it again returns the original result.
    """
    return cancel_booking(db, clock, member_id, booking_id)


@router.get(
    "/bookings",
    response_model=list[BookingOut],
    responses={
        401: CANCEL_ERRORS[401],
        404: {"model": ErrorResponse, "description": "Member not found"},
    },
)
def get_my_bookings(
    member_id: MemberId,
    db: DbSession,
    clock: ClockDep,
    status: Annotated[StatusName | None, Query(description="Only this status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[BookingOut]:
    """Your own holds and bookings, earliest first. Use it to find a hold id again."""
    return [
        BookingOut.model_validate(booking).model_copy(update={"status": shown})
        for booking, shown in member_bookings(db, clock, member_id, status, limit)
    ]
