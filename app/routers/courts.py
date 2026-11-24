import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.deps import ClockDep, DbSession
from app.schemas import AvailabilityOut, CourtListItem, ErrorResponse
from app.services.availability import Availability, court_availability
from app.services.venues import CourtListing, list_courts

router = APIRouter()


@router.get(
    "/courts/{court_id}/availability",
    response_model=AvailabilityOut,
    responses={404: {"model": ErrorResponse, "description": "Court not found"}},
)
def get_availability(
    court_id: uuid.UUID,
    db: DbSession,
    clock: ClockDep,
    day: Annotated[
        date | None,
        Query(alias="date", description="Venue-local day, YYYY-MM-DD. Defaults to today."),
    ] = None,
) -> Availability:
    """Hourly slots for one day, each free or unavailable, with its price."""
    return court_availability(db, clock, court_id, day)


@router.get(
    "/courts",
    response_model=list[CourtListItem],
    responses={404: {"model": ErrorResponse, "description": "Venue not found"}},
)
def get_courts(
    db: DbSession,
    venue_id: Annotated[
        uuid.UUID | None, Query(description="Only the courts of this venue")
    ] = None,
) -> list[CourtListing]:
    """All courts, with their venue, timezone and opening hours. Open to everyone.

    Use a court's id with GET /courts/{id}/availability.
    """
    return list_courts(db, venue_id)
