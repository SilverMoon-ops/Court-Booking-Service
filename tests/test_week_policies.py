from datetime import UTC, date, timedelta

import pytest

from app.policies import (
    InvalidWeek,
    VenueRules,
    iso_week_label,
    monday_of,
    parse_iso_week,
    week_window,
)

INDIA = VenueRules("Asia/Kolkata", 6, 22, 20000, 40000)
NEW_YORK = VenueRules("America/New_York", 0, 24, 20000, 40000)


def test_an_iso_week_starts_on_its_monday() -> None:
    assert parse_iso_week("2030-W02") == date(2030, 1, 7)
    assert parse_iso_week("2030-W02").weekday() == 0


def test_week_one_can_start_in_the_previous_year() -> None:
    assert parse_iso_week("2030-W01") == date(2029, 12, 31)


def test_a_53_week_year_has_a_week_53() -> None:
    assert parse_iso_week("2026-W53") == date(2026, 12, 28)


@pytest.mark.parametrize(
    "text", ["", "2030-02", "2030-W2", "W02", "2030W02", "2030-W00", "2030-W54", "2030-W53", "x"]
)
def test_malformed_or_impossible_weeks_are_refused(text: str) -> None:
    with pytest.raises(InvalidWeek):
        parse_iso_week(text)  # 2030 has only 52 weeks, so 2030-W53 does not exist


def test_the_label_and_the_monday_agree() -> None:
    assert iso_week_label(date(2029, 12, 31)) == "2030-W01"
    assert iso_week_label(parse_iso_week("2030-W10")) == "2030-W10"
    assert monday_of(date(2030, 1, 13)) == date(2030, 1, 7)  # a Sunday belongs to the week before
    assert monday_of(date(2030, 1, 7)) == date(2030, 1, 7)


def test_the_week_starts_at_the_venues_own_midnight() -> None:
    start, end = week_window(INDIA, date(2030, 1, 7))
    assert start.tzinfo == UTC
    assert (start.hour, start.minute) == (18, 30)  # 00:00 Monday in India is Sunday 18:30 UTC
    assert start.date() == date(2030, 1, 6)
    assert end - start == timedelta(days=7)


def test_a_week_with_the_clocks_going_forward_is_an_hour_shorter() -> None:
    start, end = week_window(NEW_YORK, date(2030, 3, 4))  # clocks go forward on Sunday 10 March
    assert end - start == timedelta(days=7) - timedelta(hours=1)


def test_a_week_with_the_clocks_going_back_is_an_hour_longer() -> None:
    start, end = week_window(NEW_YORK, date(2030, 10, 28))  # clocks go back on Sunday 3 November
    assert end - start == timedelta(days=7) + timedelta(hours=1)
