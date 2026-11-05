from datetime import timedelta

from fastapi import APIRouter, Response

from app.config import get_settings
from app.deps import ClockDep, DbSession, MemberId
from app.models import Booking
from app.schemas import BookingOut, HoldRequest
from app.services.holds import create_hold

router = APIRouter()


@router.post("/holds", response_model=BookingOut, status_code=201)
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
