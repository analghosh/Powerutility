"""A block bid must appear on every time block it covers, not just the first two.

The Bid Status page keys block bids by period code. The mapping used to add the
bid at `from_period_code` and, if the bid was longer than one block, at
`from_period_code + 1` — and stopped there. Measured against the UAT copy of
`block_bid_current_data` (976 rows): 6 bids span 4 blocks, and one each spans
8, 16, 32 and 96, so all ten showed on exactly 2 of their blocks.

Worse, `time_to_period_code("24:00")` computes 97, fails its `1 <= p <= 96`
guard and returns None, so the caller's `continue` dropped the bid entirely.
Five UAT rows end at "24:00", including a GDAM bid covering the whole day —
invisible on the GDAM Bid Status tab.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.repository.block_bid_current_data_repository import (  # noqa: E402
    BlockBidCurrentDataRepository,
)

period_range = BlockBidCurrentDataRepository.period_range


def test_single_block_bid_covers_one_period():
    assert period_range("00:00", "00:15") == [1]


def test_two_block_bid_covers_both_periods():
    assert period_range("00:00", "00:30") == [1, 2]


def test_four_block_bid_covers_all_four():
    """The bug: 6 UAT rows span 4 blocks and used to show on 2."""
    assert period_range("10:00", "11:00") == [41, 42, 43, 44]


def test_eight_block_bid_covers_all_eight():
    assert period_range("10:00", "12:00") == [41, 42, 43, 44, 45, 46, 47, 48]


def test_end_is_exclusive():
    """`to` is the boundary the bid stops at, not a block it covers."""
    assert 49 not in period_range("10:00", "12:00")


def test_full_day_bid_ending_at_2400_covers_every_period():
    """The GDAM 00:00-24:00 row in UAT — dropped entirely before this fix."""
    assert period_range("00:00", "24:00") == list(range(1, 97))


def test_last_block_ending_at_2400():
    """Four RTM rows are 23:45-24:00; one block, not a wrap."""
    assert period_range("23:45", "24:00") == [96]


def test_last_block_ending_at_0000():
    """Six DAM rows write midnight as 00:00 instead of 24:00."""
    assert period_range("23:45", "00:00") == [96]


def test_end_equal_to_start_is_read_as_a_full_day():
    assert period_range("00:00", "00:00") == list(range(1, 97))


def test_no_period_exceeds_the_96_block_day():
    for from_time, to_time in [("23:45", "24:00"), ("23:45", "00:00"), ("00:00", "24:00")]:
        assert max(period_range(from_time, to_time)) <= 96


@pytest.mark.parametrize("bad_from", ["", "not-a-time", None, "25:00"])
def test_unparseable_start_yields_no_periods(bad_from):
    assert period_range(bad_from, "12:00") == []


@pytest.mark.parametrize("bad_to", ["", "not-a-time", None])
def test_unparseable_end_falls_back_to_the_starting_block(bad_to):
    assert period_range("10:00", bad_to) == [41]


def test_every_returned_period_is_contiguous():
    periods = period_range("06:30", "18:45")
    assert periods == list(range(periods[0], periods[-1] + 1))
