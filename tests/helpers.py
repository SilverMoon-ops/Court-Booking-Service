import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))


class FixedClock:
    """A clock the test controls. Hold expiry and refund rules depend on the time."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


START_OF_TIME = datetime(2030, 1, 1, 0, 0, tzinfo=UTC)


def slot_start(day: int, hour: int) -> datetime:
    """`day` counts from 1 January 2030 (so 7 is a Monday and 35 is 4 February), in Indian time."""
    d = date(2030, 1, 1) + timedelta(days=day - 1)
    return datetime(d.year, d.month, d.day, hour, tzinfo=IST)


def hold_body(court_id: uuid.UUID, day: int, hour: int, hours: int = 1) -> dict[str, Any]:
    """Request body for a slot, in Indian time. See slot_start for what `day` means."""
    start = slot_start(day, hour)
    return {
        "court_id": str(court_id),
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=hours)).isoformat(),
    }


def future_body(court_id: uuid.UUID, days_ahead: int, hour: int, hours: int = 1) -> dict[str, Any]:
    """Like hold_body, but relative to the real clock, for tests that use the real time."""
    day = datetime.now(IST).date() + timedelta(days=days_ahead)
    start = datetime(day.year, day.month, day.day, hour, tzinfo=IST)
    return {
        "court_id": str(court_id),
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=hours)).isoformat(),
    }
