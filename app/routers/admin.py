import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.deps import DbSession, require_staff
from app.models import Court, Member
from app.schemas import (
    CourtIn,
    CourtOut,
    ErrorResponse,
    MemberIn,
    MemberOut,
    PeakWindowData,
    PricingIn,
    VenueIn,
    VenueOut,
)
from app.services import admin

# Every endpoint here needs the staff key.
router = APIRouter(dependencies=[Depends(require_staff)], tags=["admin"])

STAFF_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing X-Staff-Key header"},
    403: {"model": ErrorResponse, "description": "Wrong staff key"},
    404: {"model": ErrorResponse, "description": "Not found"},
    409: {"model": ErrorResponse, "description": "That name is already taken"},
    503: {"model": ErrorResponse, "description": "Staff access is not configured"},
}


def venue_out(detail: admin.VenueDetail) -> VenueOut:
    v = detail.venue
    return VenueOut(
        id=v.id,
        name=v.name,
        timezone=v.timezone,
        open_hour=v.open_hour,
        close_hour=v.close_hour,
        offpeak_rate=v.offpeak_rate,
        peak_rate=v.peak_rate,
        peak_windows=[PeakWindowData.model_validate(w) for w in detail.peak_windows],
        courts=[CourtOut.model_validate(c) for c in detail.courts],
    )


def windows_of(body: PricingIn) -> list[tuple[int, int, int]]:
    return [(w.weekday, w.start_hour, w.end_hour) for w in body.peak_windows]


@router.post("/venues", response_model=VenueOut, status_code=201, responses=STAFF_ERRORS)
def post_venue(body: VenueIn, db: DbSession) -> VenueOut:
    """Create a venue with its opening hours, rates and peak windows."""
    detail = admin.create_venue(
        db,
        body.name,
        body.timezone,
        body.open_hour,
        body.close_hour,
        body.offpeak_rate,
        body.peak_rate,
        windows_of(body),
    )
    return venue_out(detail)


@router.get("/venues", response_model=list[VenueOut], responses=STAFF_ERRORS)
def get_venues(db: DbSession) -> list[VenueOut]:
    """Every venue with its courts and peak windows. Use it to find court ids."""
    return [venue_out(d) for d in admin.list_venues(db)]


@router.put("/venues/{venue_id}/pricing", response_model=VenueOut, responses=STAFF_ERRORS)
def put_pricing(venue_id: uuid.UUID, body: PricingIn, db: DbSession) -> VenueOut:
    """Replace a venue's rates and peak windows. Existing bookings keep their price."""
    return venue_out(
        admin.set_pricing(db, venue_id, body.offpeak_rate, body.peak_rate, windows_of(body))
    )


@router.post(
    "/venues/{venue_id}/courts", response_model=CourtOut, status_code=201, responses=STAFF_ERRORS
)
def post_court(venue_id: uuid.UUID, body: CourtIn, db: DbSession) -> Court:
    """Add a court to a venue. Court names are unique within a venue."""
    return admin.add_court(db, venue_id, body.name)


@router.post("/members", response_model=MemberOut, status_code=201, responses=STAFF_ERRORS)
def post_member(body: MemberIn, db: DbSession) -> Member:
    """Create a member. The returned id is what the member sends as X-Member-Id."""
    return admin.create_member(db, body.name)
