"""
The Shift Status Report's rules, with no I/O.

Which blocks a shift covers, what the net schedule and deviation of a block are,
and when a block needs a remark. Every figure on the screen and in the download
passes through these functions, so they carry the report's definitions.
"""

from datetime import date

import pytest

from services.processing.app.v2.helpers import shift_status as rules


def test_the_evening_shift_on_edps_sheet_is_blocks_63_to_96():
    start, end = rules.shift_window("15:30-24:00")
    assert rules.shift_blocks(start, end) == [(0, block) for block in range(63, 97)]


def test_an_hour_only_shift_covers_the_blocks_that_start_inside_it():
    start, end = rules.shift_window("14-21")
    assert rules.shift_blocks(start, end) == [(0, block) for block in range(57, 85)]


def test_a_shift_that_wraps_midnight_runs_into_the_next_day():
    start, end = rules.shift_window("21-7")
    blocks = rules.shift_blocks(start, end)
    assert blocks[:12] == [(0, block) for block in range(85, 97)]
    assert blocks[12:] == [(1, block) for block in range(1, 29)]


def test_the_full_day_is_every_block_of_the_one_date():
    start, end = rules.FULL_DAY_WINDOW
    assert rules.shift_blocks(start, end) == [(0, block) for block in range(1, 97)]


def test_a_shift_with_equal_ends_lasts_24_hours_from_its_start():
    start, end = rules.shift_window("7-7")
    blocks = rules.shift_blocks(start, end)
    assert len(blocks) == 96
    assert blocks[0] == (0, 29) and blocks[-1] == (1, 28)


def test_a_shift_starting_off_the_quarter_hour_begins_at_the_next_block():
    start, end = rules.shift_window("9:10-10:00")
    assert rules.shift_blocks(start, end) == [(0, 38), (0, 39), (0, 40)]


def test_a_malformed_timing_is_refused():
    with pytest.raises(ValueError):
        rules.shift_window("abc")


def test_block_times_run_in_15_minute_steps_and_the_last_ends_at_midnight():
    assert rules.block_times(1) == ("00:00", "00:15")
    assert rules.block_times(63) == ("15:30", "15:45")
    assert rules.block_times(96) == ("23:45", "00:00")


def test_net_schedule_leaves_out_the_intra_state_plant():
    # 28 Sep 2026 block 69 on the test site: Final Schedule 495.25 with PPCLGPP 15.
    assert rules.net_schedule(495.25, 15.0) == pytest.approx(480.25)


def test_net_schedule_is_the_total_when_the_intra_state_plant_has_no_row():
    assert rules.net_schedule(463.0, None) == 463.0


def test_net_schedule_is_blank_without_a_final_schedule():
    assert rules.net_schedule(None, 15.0) is None


@pytest.mark.parametrize("stored", [None, 0, 0.0])
def test_a_missing_or_zero_drawal_is_blank_not_a_reading(stored):
    assert rules.drawal_value(stored) is None


def test_a_real_drawal_is_kept():
    assert rules.drawal_value(448.64) == 448.64


def test_deviation_is_drawal_minus_net_schedule():
    assert rules.deviation(469.0, 463.0) == 6.0
    assert rules.deviation(510.0, 519.0) == -9.0


def test_deviation_is_blank_rather_than_minus_the_schedule_when_drawal_is_missing():
    assert rules.deviation(None, 510.43) is None
    assert rules.deviation(469.0, None) is None


def test_a_block_needs_a_remark_beyond_15_mw_either_way():
    assert rules.needs_remark(15.01, "") is True
    assert rules.needs_remark(-33.0, None) is True
    assert rules.needs_remark(15.0, "") is False
    assert rules.needs_remark(-15.0, "") is False


def test_a_remark_or_a_blank_deviation_clears_the_flag():
    assert rules.needs_remark(33.0, "Buy bid not cleared completely") is False
    assert rules.needs_remark(None, "") is False


def test_a_whitespace_remark_does_not_count():
    assert rules.needs_remark(20.0, "   ") is True


DAM = {63: (-60.0, -51.3), 64: (-61.1, -61.1)}


def test_a_green_or_high_price_day_identical_to_dam_is_a_duplicate():
    # 3 Aug 2026 on the test site: GDAM and HPDAM rows equal DAM in all 71 blocks.
    series = {"DAM": DAM, "GDAM": dict(DAM), "HPDAM": dict(DAM)}
    assert rules.duplicate_markets(series) == {"GDAM", "HPDAM"}


def test_a_genuine_green_trade_is_kept():
    series = {"DAM": DAM, "GDAM": {63: (-10.0, -10.0)}, "HPDAM": {}}
    assert rules.duplicate_markets(series) == set()


def test_nothing_is_a_duplicate_of_an_empty_dam_day():
    series = {"DAM": {}, "GDAM": {}, "HPDAM": {}}
    assert rules.duplicate_markets(series) == set()


def test_idam_adds_the_three_day_ahead_markets_and_keeps_blank_blank():
    assert rules.idam_total([-60.0, None, -5.0]) == -65.0
    assert rules.idam_total([None, None, None]) is None


def test_the_section_date_is_the_shift_start_and_rows_carry_their_own_date():
    start, end = rules.shift_window("21-7")
    rows = rules.section_blocks(date(2026, 9, 28), start, end)
    assert rows[0] == (date(2026, 9, 28), 85)
    assert rows[-1] == (date(2026, 9, 29), 28)


def test_every_date_in_range_gets_a_section_and_the_range_is_capped():
    dates = rules.section_dates(date(2026, 9, 26), date(2026, 9, 28))
    assert dates == [date(2026, 9, 26), date(2026, 9, 27), date(2026, 9, 28)]
    with pytest.raises(ValueError):
        rules.section_dates(date(2026, 9, 1), date(2026, 10, 2))
    with pytest.raises(ValueError):
        rules.section_dates(date(2026, 9, 28), date(2026, 9, 27))


@pytest.mark.parametrize("typed, stored", [
    ("ajay", "ajay"),
    ("ajay,vijay", "ajay, vijay"),
    ("  ajay ,  vijay kumar ,, ", "ajay, vijay kumar"),
    ("ajay\nvijay", "ajay vijay"),
    ("Ajay  Kumar,Vijay", "Ajay Kumar, Vijay"),
])
def test_shift_person_names_are_tidied_but_kept_as_typed(typed, stored):
    assert rules.normalise_names(typed) == stored


@pytest.mark.parametrize("typed", ["", "   ", " , ,"])
def test_shift_person_names_that_name_nobody_are_empty(typed):
    assert rules.normalise_names(typed) == ""
