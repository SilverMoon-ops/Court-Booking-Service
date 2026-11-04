from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.policies import (
    InvalidSlot,
    PeakRule,
    VenueRules,
    can_add_bookings,
    price_for,
    refund_for,
    validate_slot,
    weekly_occurrences,
)

OFFPEAK, PEAK = 20000, 40000

# Open 06:00-22:00 India time. Peak Mon-Fri 18:00-22:00 and Saturday 09:00-13:00.
INDIA = VenueRules(
    timezone="Asia/Kolkata",
    open_hour=6,
    close_hour=22,
    offpeak_rate=OFFPEAK,
    peak_rate=PEAK,
    peak_windows=(
        *(PeakRule(weekday, 18, 22) for weekday in range(5)),
        PeakRule(5, 9, 13),
    ),
)

# A venue in a timezone with daylight saving. Peak every day 18:00-22:00.
NEW_YORK = VenueRules(
    timezone="America/New_York",
    open_hour=6,
    close_hour=22,
    offpeak_rate=OFFPEAK,
    peak_rate=PEAK,
    peak_windows=tuple(PeakRule(weekday, 18, 22) for weekday in range(7)),
)

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2030, 1, 1, 0, 0, tzinfo=UTC)


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment in January 2030 on Indian clock time (Monday 7 January is day 7)."""
    return datetime(2030, 1, day, hour, minute, tzinfo=IST)


def span(day: int, start_hour: int, hours: int) -> tuple[datetime, datetime]:
    start = ist(day, start_hour)
    return start, start + timedelta(hours=hours)


# 7 January 2030 is a Monday, 12 January is a Saturday, 13 January is a Sunday.
MONDAY, SATURDAY, SUNDAY = 7, 12, 13


# ---------------------------------------------------------------- pricing


@pytest.mark.parametrize(
    ("day", "hour", "hours", "expected"),
    [
        (MONDAY, 10, 1, OFFPEAK),
        (MONDAY, 18, 1, PEAK),
        (MONDAY, 21, 1, PEAK),
        (MONDAY, 17, 2, OFFPEAK + PEAK),  # crosses into peak at 18:00
        (MONDAY, 20, 2, 2 * PEAK),
        (MONDAY, 22, 1, OFFPEAK),  # the peak window ends at 22:00 (exclusive)
        (MONDAY, 9, 3, 3 * OFFPEAK),
        (SATURDAY, 9, 3, 3 * PEAK),  # Saturday peak is in the morning
        (SATURDAY, 18, 1, OFFPEAK),  # and Saturday evening is off-peak
        (SUNDAY, 18, 1, OFFPEAK),  # Sunday has no peak window at all
    ],
)
def test_price_is_charged_hour_by_hour(day: int, hour: int, hours: int, expected: int) -> None:
    start, end = span(day, hour, hours)
    assert price_for(INDIA, start, end) == expected


def test_peak_is_decided_by_venue_time_not_utc() -> None:
    # 18:00 in India on a Monday is 12:30 UTC. A UTC-based check would call it off-peak.
    start, end = span(MONDAY, 18, 1)
    assert start.astimezone(UTC).hour == 12
    assert price_for(INDIA, start, end) == PEAK


def test_peak_stays_peak_across_a_daylight_saving_change() -> None:
    # 18:00 in New York is 23:00 UTC before the clocks change and 22:00 UTC after.
    before = datetime(2030, 3, 8, 23, 0, tzinfo=UTC)  # Friday, EST
    after = datetime(2030, 3, 11, 22, 0, tzinfo=UTC)  # Monday, EDT
    assert price_for(NEW_YORK, before, before + timedelta(hours=1)) == PEAK
    assert price_for(NEW_YORK, after, after + timedelta(hours=1)) == PEAK


def test_price_rejects_a_time_without_timezone() -> None:
    naive = datetime(2030, 1, 7, 10, 0)
    with pytest.raises(InvalidSlot, match="timezone"):
        price_for(INDIA, naive, naive + timedelta(hours=1))


# ----------------------------------------------------------------- refund


def test_full_refund_with_a_week_of_notice() -> None:
    assert refund_for(PEAK, NOW + timedelta(days=7), NOW) == PEAK


def test_full_refund_at_exactly_24_hours() -> None:
    assert refund_for(PEAK, NOW + timedelta(hours=24), NOW) == PEAK


def test_no_refund_one_second_inside_24_hours() -> None:
    start = NOW + timedelta(hours=24) - timedelta(seconds=1)
    assert refund_for(PEAK, start, NOW) == 0


def test_no_refund_for_a_booking_that_already_started() -> None:
    assert refund_for(PEAK, NOW - timedelta(hours=1), NOW) == 0


def test_refund_compares_real_time_across_timezones() -> None:
    start = datetime(2030, 1, 3, 12, 0, tzinfo=UTC)
    same_moment_in_india = start.astimezone(IST)
    now = datetime(2030, 1, 2, 12, 0, tzinfo=UTC)
    assert refund_for(PEAK, same_moment_in_india, now) == PEAK


# ------------------------------------------------------------ slot checks


def test_a_normal_slot_is_valid() -> None:
    start, end = span(MONDAY, 10, 2)
    validate_slot(INDIA, start, end, NOW)


def test_a_slot_ending_exactly_at_closing_time_is_valid() -> None:
    start, end = span(MONDAY, 20, 2)  # 20:00-22:00, the venue closes at 22:00
    validate_slot(INDIA, start, end, NOW)


@pytest.mark.parametrize(
    ("hour", "hours", "message"),
    [
        (5, 1, "open"),  # before opening
        (21, 2, "open"),  # runs past closing
        (22, 1, "open"),  # starts at closing time
    ],
)
def test_slot_must_be_inside_opening_hours(hour: int, hours: int, message: str) -> None:
    start, end = span(MONDAY, hour, hours)
    with pytest.raises(InvalidSlot, match=message):
        validate_slot(INDIA, start, end, NOW)


def test_alignment_is_checked_in_venue_time_not_utc() -> None:
    # 10:00 India time is 04:30 UTC, which is NOT on the hour in UTC but is a valid slot.
    start, end = span(MONDAY, 10, 1)
    assert start.astimezone(UTC).minute == 30
    validate_slot(INDIA, start, end, NOW)

    # 09:30 India time is 04:00 UTC, which IS on the hour in UTC but is not a valid slot.
    half_past = ist(MONDAY, 9, 30)
    with pytest.raises(InvalidSlot, match="on the hour"):
        validate_slot(INDIA, half_past, half_past + timedelta(hours=1), NOW)


@pytest.mark.parametrize(
    ("length", "message"),
    [
        (timedelta(0), "1 to 3 hours"),
        (timedelta(hours=4), "1 to 3 hours"),
        (timedelta(minutes=90), "whole number"),
    ],
)
def test_booking_length_must_be_one_to_three_whole_hours(length: timedelta, message: str) -> None:
    start = ist(MONDAY, 10)
    with pytest.raises(InvalidSlot, match=message):
        validate_slot(INDIA, start, start + length, NOW)


def test_a_booking_cannot_cross_midnight() -> None:
    late_night = VenueRules("Asia/Kolkata", 0, 24, OFFPEAK, PEAK)
    start, end = span(MONDAY, 23, 2)  # 23:00 to 01:00
    with pytest.raises(InvalidSlot, match="midnight"):
        validate_slot(late_night, start, end, NOW)


def test_a_booking_must_start_in_the_future() -> None:
    start, end = span(MONDAY, 10, 1)
    with pytest.raises(InvalidSlot, match="future"):
        validate_slot(INDIA, start, end, start)  # starts exactly now
    with pytest.raises(InvalidSlot, match="future"):
        validate_slot(INDIA, start, end, start + timedelta(hours=1))


def test_slot_validation_rejects_a_time_without_timezone() -> None:
    naive = datetime(2030, 1, 7, 10, 0)
    with pytest.raises(InvalidSlot, match="timezone"):
        validate_slot(INDIA, naive, naive + timedelta(hours=1), NOW)


# ---------------------------------------------------------------- limit


@pytest.mark.parametrize(("active", "allowed"), [(0, True), (1, True), (2, True), (3, False)])
def test_a_member_may_hold_at_most_three_active_bookings(active: int, allowed: bool) -> None:
    assert can_add_bookings(active) is allowed


# ------------------------------------------------------- weekly series


def test_weekly_series_keeps_the_same_clock_time_in_india() -> None:
    starts = weekly_occurrences(INDIA, date(2030, 1, 7), 18, 4)
    assert [s.astimezone(IST).strftime("%a %d %H:%M") for s in starts] == [
        "Mon 07 18:00",
        "Mon 14 18:00",
        "Mon 21 18:00",
        "Mon 28 18:00",
    ]
    assert {s.hour for s in starts} == {12}  # India has no daylight saving


def test_weekly_series_keeps_local_time_across_a_daylight_saving_change() -> None:
    # The clocks in New York go forward on Sunday 10 March 2030.
    starts = weekly_occurrences(NEW_YORK, date(2030, 3, 4), 18, 3)
    assert [s.hour for s in starts] == [23, 22, 22]  # UTC hour moves, local stays 18:00
    assert [s.date().isoformat() for s in starts] == ["2030-03-04", "2030-03-11", "2030-03-18"]


def test_weekly_series_is_refused_if_a_week_lands_on_a_skipped_hour() -> None:
    # 02:00 on Sunday 10 March 2030 does not exist in New York.
    with pytest.raises(InvalidSlot, match="does not exist"):
        weekly_occurrences(NEW_YORK, date(2030, 3, 3), 2, 2)


@pytest.mark.parametrize("count", [0, 13])
def test_weekly_series_length_is_limited(count: int) -> None:
    with pytest.raises(InvalidSlot, match="1 to 12"):
        weekly_occurrences(INDIA, date(2030, 1, 7), 18, count)
