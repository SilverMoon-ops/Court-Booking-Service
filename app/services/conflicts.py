from psycopg.errors import DeadlockDetected, ExclusionViolation
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.errors import SlotUnavailable


def flush_or_slot_unavailable(db: Session, message: str) -> None:
    """Send pending inserts to the database; turn "that time is taken" into a 409.

    The exclusion constraint is the final word on double-booking. When two inserts
    collide, Postgres reports either an exclusion violation or, occasionally, a deadlock
    for the loser. Both mean the slot was taken.
    """
    try:
        db.flush()
    except IntegrityError as exc:
        if isinstance(exc.orig, ExclusionViolation):
            raise SlotUnavailable(message) from exc
        raise
    except OperationalError as exc:
        if isinstance(exc.orig, DeadlockDetected):
            raise SlotUnavailable(message) from exc
        raise
