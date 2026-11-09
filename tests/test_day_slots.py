from datetime import UTC, date

from app.policies import PeakRule, VenueRules, day_slots

INDIA = VenueRules("Asia/Kolkata", 6, 22, 20000, 40000, (PeakRule(0, 18, 22),))
NEW_YORK_ALL_DAY = VenueRules("America/New_York", 0, 24, 20000, 40000)


def test_a_day_has_one_slot_per_opening_hour() -> None:
    slots = day_slots(INDIA, date(2030, 1, 7))
    assert len(slots) == 16  # 06:00 to 22:00
    first_start, first_end = slots[0]
    assert first_start.hour == 0 and first_start.minute == 30  # 06:00 India is 00:30 UTC
    assert first_end - first_start == slots[1][0] - slots[0][0]  # consecutive, one hour long


def test_slots_are_back_to_back_with_no_gaps() -> None:
    slots = day_slots(INDIA, date(2030, 1, 7))
    for (_, end), (start, _) in zip(slots, slots[1:], strict=False):
        assert end == start


def test_the_hour_skipped_by_daylight_saving_is_left_out() -> None:
    # Clocks in New York go forward on 10 March 2030: 02:00 does not exist that day.
    slots = day_slots(NEW_YORK_ALL_DAY, date(2030, 3, 10))
    assert len(slots) == 23
    local_hours = [s.astimezone(NEW_YORK_ALL_DAY.tz).hour for s, _ in slots]
    assert 2 not in local_hours


def test_an_ordinary_new_york_day_has_24_slots() -> None:
    assert len(day_slots(NEW_YORK_ALL_DAY, date(2030, 3, 11))) == 24


def test_slot_times_are_utc_instants() -> None:
    start, _ = day_slots(INDIA, date(2030, 1, 7))[0]
    assert start.tzinfo == UTC
