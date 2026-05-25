"""
Unit tests for IdfForecastMergeProcessor.
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.constants.idf_forecast import (
    IDF_FORECAST_SWITCH_KEY,
    IDF_FORECAST_SWITCH_LAST_RESET_KEY,
)
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API
from services.scheduler.app.processors.idf_forecast_merge_processor import (
    IdfForecastMergeProcessor,
)


@pytest.fixture
def processor():
    proc = IdfForecastMergeProcessor()
    proc.demand_repository = MagicMock()
    # Motor: find() is sync and returns a cursor; to_list() is async.
    proc.demand_repository.serve_collection = MagicMock()
    proc.forecast_repository = AsyncMock()
    proc.parameter_repository = AsyncMock()
    return proc



def _mock_demand_cursor(docs):
    """Sync find() -> cursor with async to_list (matches Motor API)."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    return cursor


def _bulk_result(modified=1, upserted=0):
    r = MagicMock()
    r.modified_count = modified
    r.upserted_ids = {i: f"id{i}" for i in range(upserted)}
    return r


@pytest.mark.asyncio
async def test_midnight_reset_sets_switch_true(processor):
    today = date(2026, 5, 16)
    processor.parameter_repository.get_by_key = AsyncMock(
        side_effect=lambda key: (
            {"para_value": "2026-05-15"}
            if key == IDF_FORECAST_SWITCH_LAST_RESET_KEY
            else {"para_value": "false"}
        )
    )
    processor.parameter_repository.upsert_by_key = AsyncMock()

    reset = await processor._maybe_reset_switch_for_new_day(today)

    assert reset is True
    processor.parameter_repository.upsert_by_key.assert_any_call(IDF_FORECAST_SWITCH_KEY, "true")


@pytest.mark.asyncio
async def test_csv_forecast_zero_invalid(processor):
    assert processor._csv_forecast_invalid(None) is True
    assert processor._csv_forecast_invalid(0) is True
    assert processor._csv_forecast_invalid(100.0) is False


@pytest.mark.asyncio
async def test_run_merge_api_mode_applies_block_filter(processor):
    today = date(2026, 5, 15)
    scheduled_at = datetime(2026, 5, 15)
    ctx = {
        "today_ist": today,
        "current_block": 10,
        "after_evening_cutover": False,
        "min_ahead_blocks": 5,
        "include_tomorrow_forecast": False,
    }

    processor.parameter_repository.ensure_idf_forecast_switch_default = AsyncMock()
    processor.parameter_repository.get_by_key = AsyncMock(
        side_effect=lambda key: (
            {"para_value": today.isoformat()}
            if key == IDF_FORECAST_SWITCH_LAST_RESET_KEY
            else {"para_value": "true"}
        )
    )

    async def find_forecast(scheduled_at, source):
        if source == FORECAST_SOURCE_RTM_API:
            return [
                {"time_block": 5, "forecast": 100.0, "forecasted_frequency": 49.0},
                {"time_block": 20, "forecast": 200.0, "forecasted_frequency": 50.0},
            ]
        return []

    processor.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(
        side_effect=find_forecast
    )
    processor.demand_repository.serve_collection.find = MagicMock(
        return_value=_mock_demand_cursor([])
    )
    processor.demand_repository.serve_collection.bulk_write = AsyncMock(
        return_value=_bulk_result(modified=1)
    )

    with patch(
        "services.scheduler.app.processors.idf_forecast_merge_processor.rtm_forecast_run_context",
        return_value=ctx,
    ), patch(
        "services.scheduler.app.processors.idf_forecast_merge_processor.target_scheduled_dates_for_rtm",
        return_value=[today],
    ):
        merged, skipped, failed = await processor.run_merge()

    assert failed == 0
    assert merged == 1
    # Writable blocks 9–96 (88 total); only block 20 has API data.
    assert skipped == 87
    processor.demand_repository.serve_collection.bulk_write.assert_called_once()
    ops = processor.demand_repository.serve_collection.bulk_write.call_args[0][0]
    written_blocks = [op._filter["time_block"] for op in ops]
    assert 5 not in written_blocks
    assert 20 in written_blocks


@pytest.mark.asyncio
async def test_run_merge_csv_session_writes_zero_when_csv_missing(processor):
    """Manual mode: incomplete CSV in a session writes 0 MW (no API fallback)."""
    today = date(2026, 5, 15)
    scheduled_at = datetime(2026, 5, 15)
    ctx = {
        "today_ist": today,
        "current_block": 10,
        "after_evening_cutover": False,
        "min_ahead_blocks": 5,
        "include_tomorrow_forecast": False,
    }

    processor.parameter_repository.ensure_idf_forecast_switch_default = AsyncMock()
    processor.parameter_repository.get_by_key = AsyncMock(
        side_effect=lambda key: (
            {"para_value": today.isoformat()}
            if key == IDF_FORECAST_SWITCH_LAST_RESET_KEY
            else {"para_value": "false"}
        )
    )

    async def find_forecast(scheduled_at, source):
        if source == FORECAST_SOURCE_CSV:
            return [
                {"time_block": 9, "forecast": 100.0},
                {"time_block": 10, "forecast": 101.0},
                {"time_block": 20, "forecast": 0},
                {"time_block": 21, "forecast": 150.0},
            ]
        return [
            {"time_block": 20, "forecast": 88.0, "forecasted_frequency": 49.1},
            {"time_block": 21, "forecast": 99.0, "forecasted_frequency": 49.2},
        ]

    processor.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(
        side_effect=find_forecast
    )
    processor.demand_repository.serve_collection.find = MagicMock(
        return_value=_mock_demand_cursor(
            [
                {"time_block": 20, "forecast": 1.0, "rostering": 0.0},
                {"time_block": 21, "forecast": 2.0, "rostering": 0.0},
            ]
        )
    )
    processor.demand_repository.serve_collection.bulk_write = AsyncMock(
        return_value=_bulk_result(modified=2)
    )

    with patch(
        "services.scheduler.app.processors.idf_forecast_merge_processor.rtm_forecast_run_context",
        return_value=ctx,
    ), patch(
        "services.scheduler.app.processors.idf_forecast_merge_processor.target_scheduled_dates_for_rtm",
        return_value=[today],
    ):
        merged, skipped, failed = await processor.run_merge()

    assert failed == 0
    assert merged == 2
    ops = processor.demand_repository.serve_collection.bulk_write.call_args[0][0]
    assert len(ops) == 2
    for op in ops:
        assert op._doc["$set"]["source_forecast"] == FORECAST_SOURCE_CSV
        assert op._doc["$set"]["forecast"] == 0.0
    blocks = sorted(op._filter["time_block"] for op in ops)
    assert blocks == [19, 20]


@pytest.mark.asyncio
async def test_run_merge_manual_active_session_csv_empty_flips_auto(processor):
    """When active session CSV is zero/empty, Manual flips to Auto with user message."""
    from libs.constants.idf_forecast import (
        IDF_FORECAST_SWITCH_AUTO_FALLBACK_MESSAGE_KEY,
        IDF_FORECAST_SWITCH_LAST_RESET_KEY,
        MANUAL_AUTO_FLIP_ACTIVE_SESSION_CSV_MESSAGE,
    )

    today = date(2026, 5, 15)
    ctx = {
        "today_ist": today,
        "current_block": 10,
        "after_evening_cutover": False,
        "min_ahead_blocks": 5,
        "include_tomorrow_forecast": False,
    }

    processor.parameter_repository.ensure_idf_forecast_switch_default = AsyncMock()
    processor.parameter_repository.get_by_key = AsyncMock(
        side_effect=lambda key: (
            {"para_value": today.isoformat()}
            if key == IDF_FORECAST_SWITCH_LAST_RESET_KEY
            else {"para_value": "false"}
        )
    )
    processor.parameter_repository.upsert_by_key = AsyncMock()
    processor.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(
        return_value=[
            {"time_block": 9, "forecast": 0},
            {"time_block": 10, "forecast": 101.0},
        ]
    )
    processor.demand_repository.serve_collection.find = MagicMock(
        return_value=_mock_demand_cursor([])
    )

    with patch(
        "services.scheduler.app.processors.idf_forecast_merge_processor.rtm_forecast_run_context",
        return_value=ctx,
    ), patch(
        "services.scheduler.app.processors.idf_forecast_merge_processor.target_scheduled_dates_for_rtm",
        return_value=[today],
    ):
        await processor.run_merge()

    calls = processor.parameter_repository.upsert_by_key.call_args_list
    assert any(c[0][0] == "idf_forecast_switch" and c[0][1] == "true" for c in calls)
    msg_call = next(
        c for c in calls if c[0][0] == IDF_FORECAST_SWITCH_AUTO_FALLBACK_MESSAGE_KEY
    )
    assert msg_call[0][1] == MANUAL_AUTO_FLIP_ACTIVE_SESSION_CSV_MESSAGE


@pytest.mark.asyncio
async def test_run_merge_manual_no_forecast_flips_auto(processor):
    today = date(2026, 5, 15)
    ctx = {
        "today_ist": today,
        "current_block": 10,
        "after_evening_cutover": False,
        "min_ahead_blocks": 5,
        "include_tomorrow_forecast": False,
    }

    processor.parameter_repository.ensure_idf_forecast_switch_default = AsyncMock()
    processor.parameter_repository.get_by_key = AsyncMock(
        side_effect=lambda key: (
            {"para_value": today.isoformat()}
            if key == IDF_FORECAST_SWITCH_LAST_RESET_KEY
            else {"para_value": "false"}
        )
    )
    processor.parameter_repository.upsert_by_key = AsyncMock()
    processor.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(
        return_value=[]
    )
    processor.demand_repository.serve_collection.find = MagicMock(
        return_value=_mock_demand_cursor([])
    )
    processor.demand_repository.serve_collection.bulk_write = AsyncMock(
        return_value=_bulk_result(modified=0)
    )

    with patch(
        "services.scheduler.app.processors.idf_forecast_merge_processor.rtm_forecast_run_context",
        return_value=ctx,
    ), patch(
        "services.scheduler.app.processors.idf_forecast_merge_processor.target_scheduled_dates_for_rtm",
        return_value=[today],
    ):
        await processor.run_merge()

    processor.parameter_repository.upsert_by_key.assert_any_call(
        IDF_FORECAST_SWITCH_KEY, "true"
    )
