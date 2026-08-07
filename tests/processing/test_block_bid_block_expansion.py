"""
Unit tests for expanding a block bid's clock range into 15-minute blocks.

A block bid is stored under the span the trader entered ("07:00-11:00"), while the
single-bid table looks up one 15-minute block at a time ("07:00-07:15"). These tests
pin the expansion that bridges the two, and the accumulation of overlapping bids.
"""

import pytest
from unittest.mock import AsyncMock, patch

from services.processing.app.v2.repository.block_bid_current_data_repository import (
    BlockBidCurrentDataRepository,
)


def labels_to_blocks(labels):
    """Map "HH:MM-HH:MM" labels back to block numbers, for readable assertions."""
    blocks = []
    for label in labels:
        start = label.split("-")[0]
        hour, minute = (int(part) for part in start.split(":"))
        blocks.append((hour * 60 + minute) // 15 + 1)
    return blocks


expand = BlockBidCurrentDataRepository.expand_time_range_to_block_labels


class TestClockToMinutes:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("00:00", 0),
            ("07:00", 420),
            ("11:00", 660),
            ("23:45", 1425),
            ("24:00", 1440),
        ],
    )
    def test_parses_valid_clock_strings(self, value, expected):
        assert BlockBidCurrentDataRepository.clock_to_minutes(value) == expected

    @pytest.mark.parametrize("value", [None, "", "abc", "7", "25:00", "07:60", 7])
    def test_rejects_unparseable_values(self, value):
        assert BlockBidCurrentDataRepository.clock_to_minutes(value) is None


class TestBlockToLabel:
    @pytest.mark.parametrize(
        "block,label",
        [
            (1, "00:00-00:15"),
            (29, "07:00-07:15"),
            (44, "10:45-11:00"),
            (77, "19:00-19:15"),
            (95, "23:30-23:45"),
            (96, "23:45-00:00"),
        ],
    )
    def test_label_matches_service_format(self, block, label):
        assert BlockBidCurrentDataRepository.block_to_time_block_label(block) == label


class TestExpandTimeRange:
    def test_multi_hour_range_is_start_inclusive_end_exclusive(self):
        """07:00-11:00 covers blocks 29-44, not block 28 (06:45-07:00)."""
        labels = expand("07:00", "11:00")
        assert labels_to_blocks(labels) == list(range(29, 45))
        assert len(labels) == 16
        assert labels[0] == "07:00-07:15"
        assert labels[-1] == "10:45-11:00"

    def test_single_block_range(self):
        assert expand("19:00", "19:15") == ["19:00-19:15"]

    def test_first_block_of_day(self):
        assert expand("00:00", "00:15") == ["00:00-00:15"]

    def test_last_block_ends_at_midnight(self):
        assert expand("23:45", "00:00") == ["23:45-00:00"]

    def test_full_day(self):
        """Real data stores a whole-day bid as 00:00-24:00, never 00:00-00:00."""
        labels = expand("00:00", "24:00")
        assert len(labels) == 96
        assert labels_to_blocks(labels) == list(range(1, 97))

    def test_last_block_accepts_either_midnight_spelling(self):
        assert expand("23:45", "00:00") == expand("23:45", "24:00") == ["23:45-00:00"]

    def test_range_wrapping_past_midnight(self):
        labels = expand("22:00", "02:00")
        assert labels_to_blocks(labels) == list(range(89, 97)) + list(range(1, 9))

    def test_partial_hour_range(self):
        assert labels_to_blocks(expand("13:30", "14:15")) == [55, 56, 57]

    def test_falls_back_to_splitting_time_block_span(self):
        labels = expand(None, None, time_block="07:00-11:00")
        assert labels_to_blocks(labels) == list(range(29, 45))

    def test_falls_back_for_one_missing_side_only(self):
        labels = expand("07:00", "", time_block="07:00-08:00")
        assert labels_to_blocks(labels) == [29, 30, 31, 32]

    @pytest.mark.parametrize("clock", ["07:00", "00:00"])
    def test_zero_length_range_covers_nothing(self, clock):
        """No from == to rows exist in real data; treat one as covering nothing
        rather than letting the midnight-wrap branch spread it over the day."""
        assert expand(clock, clock) == []

    def test_range_starting_at_end_of_day_covers_nothing(self):
        assert expand("24:00", "24:00") == []

    @pytest.mark.parametrize(
        "from_time,to_time,time_block",
        [
            (None, None, None),
            ("", "", ""),
            ("garbage", "11:00", None),
            ("07:00", "garbage", None),
            (None, None, "not-a-range-at-all"),
        ],
    )
    def test_unparseable_input_returns_empty(self, from_time, to_time, time_block):
        assert expand(from_time, to_time, time_block) == []


class TestQuantitiesByTimeBlock:
    """The aggregation groups by (from, to); the repository expands and accumulates."""

    async def run(self, groups):
        repo = BlockBidCurrentDataRepository()
        with patch.object(repo, "aggregate", new=AsyncMock(return_value=groups)):
            return await repo.get_approved_block_bid_quantities_by_time_block(
                "2026-08-08", market_type="DAM"
            )

    @pytest.mark.asyncio
    async def test_single_bid_fills_every_covered_block(self):
        result = await self.run(
            [
                {
                    "_id": {"from": "07:00", "to": "11:00", "time_block": "07:00-11:00"},
                    "total_quantum": -30.0,
                }
            ]
        )
        assert len(result) == 16
        assert result["07:00-07:15"] == -30.0
        assert result["10:45-11:00"] == -30.0
        assert "06:45-07:00" not in result
        assert "11:00-11:15" not in result

    @pytest.mark.asyncio
    async def test_overlapping_bids_sum_on_shared_blocks(self):
        result = await self.run(
            [
                {
                    "_id": {"from": "07:00", "to": "11:00", "time_block": "07:00-11:00"},
                    "total_quantum": -30.0,
                },
                {
                    "_id": {"from": "10:00", "to": "11:00", "time_block": "10:00-11:00"},
                    "total_quantum": -15.0,
                },
            ]
        )
        assert result["09:45-10:00"] == -30.0
        assert result["10:00-10:15"] == -45.0
        assert result["10:45-11:00"] == -45.0

    @pytest.mark.asyncio
    async def test_unusable_range_is_skipped_not_raised(self):
        result = await self.run(
            [
                {
                    "_id": {"from": None, "to": None, "time_block": "garbage"},
                    "total_quantum": -99.0,
                },
                {
                    "_id": {"from": "19:00", "to": "19:15", "time_block": "19:00-19:15"},
                    "total_quantum": -5.0,
                },
            ]
        )
        assert result == {"19:00-19:15": -5.0}

    @pytest.mark.asyncio
    async def test_no_matching_rows_returns_empty_dict(self):
        assert await self.run([]) == {}
