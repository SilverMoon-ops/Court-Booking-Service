import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.deps import ClockDep, DbSession, require_staff
from app.schemas import ErrorResponse, UtilizationOut, VenueUtilization
from app.services.utilization import weekly_utilization

router = APIRouter()


@router.get(
    "/staff/utilization",
    response_model=UtilizationOut,
    dependencies=[Depends(require_staff)],
    responses={
        401: {"model": ErrorResponse, "description": "Missing X-Staff-Key header"},
        403: {"model": ErrorResponse, "description": "Wrong staff key"},
        404: {"model": ErrorResponse, "description": "Venue not found"},
        503: {"model": ErrorResponse, "description": "Staff access is not configured"},
    },
)
def get_utilization(
    db: DbSession,
    clock: ClockDep,
    week: Annotated[
        str | None, Query(description="ISO week, like 2030-W02. Defaults to the current week.")
    ] = None,
    venue_id: Annotated[uuid.UUID | None, Query(description="Only this venue")] = None,
) -> UtilizationOut:
    """Court utilization for one week: booked hours divided by open hours, per court."""
    reports = weekly_utilization(db, clock, week, venue_id)
    return UtilizationOut(venues=[VenueUtilization.model_validate(r) for r in reports])
