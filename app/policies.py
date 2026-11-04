"""Business rules as pure functions: no database, no clock, no web framework.

Everything here takes plain values and returns plain values, so each rule can be
tested on its own and changed quickly when a requirement changes. Times are
always timezone-aware. Opening hours and peak windows are in the venue's local
time, so they are converted from UTC before they are compared.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

MAX_ACTIVE_BOOKINGS = 3
MIN_DURATION_HOURS = 1
MAX_DURATION_HOURS = 3
MAX_SERIES_OCCURRENCES = 12
FULL_REFUND_NOTICE = timedelta(hours=24)
HOUR = timedelta(hours=1)


class InvalidSlot(ValueError):
    """The requested time cannot be booked. The message says why."""


@dataclass(frozen=True)
class PeakRule:
    weekday: int  # Monday = 0 ... Sunday = 6
    start_hour: int  # venue-local, inclusive
    end_hour: int  # venue-local, exclusive


@dataclass(frozen=True)
class VenueRules:
    timezone: str  # IANA name, e.g. "Asia/Kolkata"
    open_hour: int
    close_hour: int  # 24 means midnight
    offpeak_rate: int  # per hour, minor units (paise)
    peak_rate: int
    peak_windows: tuple[PeakRule, ...] = ()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _utc(moment: datetime) -> datetime:
    """Convert to UTC. A time without a timezone is refused, never guessed."""
    if moment.tzinfo is None:
        raise InvalidSlot("times must include a timezone offset")
    return moment.astimezone(UTC)


def _hour_slots(start_at: datetime, end_at: datetime) -> list[datetime]:
    """The start of each hour in the booking, as UTC instants.

    Works in UTC so that adding hours is exact elapsed time, even across a
    daylight-saving change in the venue's own timezone.
    """
    start, end = _utc(start_at), _utc(end_at)
    hours, remainder = divmod(end - start, HOUR)
    if remainder:
        raise InvalidSlot("a booking must last a whole number of hours")
    if not MIN_DURATION_HOURS <= hours <= MAX_DURATION_HOURS:
        raise InvalidSlot(f"a booking must last {MIN_DURATION_HOURS} to {MAX_DURATION_HOURS} hours")
    return [start + i * HOUR for i in range(hours)]


def validate_slot(venue: VenueRules, start_at: datetime, end_at: datetime, now: datetime) -> None:
    """Raise InvalidSlot unless this is a bookable slot at this venue."""
    slots = _hour_slots(start_at, end_at)
    if slots[0] <= _utc(now):
        raise InvalidSlot("a booking must start in the future")

    local = [slot.astimezone(venue.tz) for slot in slots]
    if any(t.minute or t.second or t.microsecond for t in local):
        raise InvalidSlot("a booking must start on the hour in the venue's local time")
    if len({t.date() for t in local}) != 1:
        raise InvalidSlot("a booking cannot cross midnight in the venue's local time")
    if any(not venue.open_hour <= t.hour < venue.close_hour for t in local):
        raise InvalidSlot(
            f"the venue is open {venue.open_hour:02d}:00 to {venue.close_hour:02d}:00"
        )


def _is_peak(venue: VenueRules, local: datetime) -> bool:
    return any(
        rule.weekday == local.weekday() and rule.start_hour <= local.hour < rule.end_hour
        for rule in venue.peak_windows
    )


def price_for(venue: VenueRules, start_at: datetime, end_at: datetime) -> int:
    """Total price: each hour is charged at its own peak or off-peak rate.

    A booking that crosses a peak boundary is simply priced hour by hour, so no
    special case is needed.
    """
    total = 0
    for slot in _hour_slots(start_at, end_at):
        local = slot.astimezone(venue.tz)
        total += venue.peak_rate if _is_peak(venue, local) else venue.offpeak_rate
    return total


def refund_for(price: int, start_at: datetime, now: datetime) -> int:
    """Full refund with at least 24 hours' notice, otherwise nothing."""
    notice = _utc(start_at) - _utc(now)
    return price if notice >= FULL_REFUND_NOTICE else 0


def can_add_bookings(active_count: int, adding: int = 1) -> bool:
    """True if a member with `active_count` active future bookings may add more."""
    return active_count + adding <= MAX_ACTIVE_BOOKINGS


def weekly_occurrences(
    venue: VenueRules, first_date: date, start_hour: int, count: int
) -> list[datetime]:
    """Start times (UTC) of a weekly booking, at the same local clock time every week.

    Adding 7 x 24 hours would drift by an hour whenever daylight saving changes,
    so each week is built from the local date and hour instead. If a week lands on
    a local time that does not exist (the hour skipped in spring), the whole series
    is refused. A local time that happens twice (autumn) uses its first occurrence.
    """
    if not 1 <= count <= MAX_SERIES_OCCURRENCES:
        raise InvalidSlot(f"a series has 1 to {MAX_SERIES_OCCURRENCES} occurrences")

    starts: list[datetime] = []
    for week in range(count):
        day = first_date + timedelta(weeks=week)
        wanted = datetime(day.year, day.month, day.day, start_hour, tzinfo=venue.tz)
        instant = wanted.astimezone(UTC)
        if instant.astimezone(venue.tz).replace(tzinfo=None) != wanted.replace(tzinfo=None):
            raise InvalidSlot(f"{day} {start_hour:02d}:00 does not exist in {venue.timezone}")
        starts.append(instant)
    return starts
