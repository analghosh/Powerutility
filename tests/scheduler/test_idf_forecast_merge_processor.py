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
    proc.demand_repository.serve_collection = AsyncMock()
    proc.forecast_repository = AsyncMock()
    proc.parameter_repository = AsyncMock()
    return proc


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
    processor.parameter_repository.upsert_by_key.assert_any_call(
        IDF_FORECAST_SWITCH_LAST_RESET_KEY,
        "2026-05-16",
    )


@pytest.mark.asyncio
async def test_midnight_reset_skips_same_day(processor):
    today = date(2026, 5, 15)
    processor.parameter_repository.get_by_key = AsyncMock(
        return_value={"para_value": "2026-05-15"}
    )
    processor.parameter_repository.upsert_by_key = AsyncMock()

    reset = await processor._maybe_reset_switch_for_new_day(today)

    assert reset is False
    processor.parameter_repository.upsert_by_key.assert_not_called()


@pytest.mark.asyncio
async def test_csv_forecast_zero_skipped(processor):
    assert processor._csv_forecast_invalid(None) is True
    assert processor._csv_forecast_invalid(0) is True
    assert processor._csv_forecast_invalid(0.0) is True
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
    }

    processor.parameter_repository.ensure_idf_forecast_switch_default = AsyncMock()
    processor.parameter_repository.get_by_key = AsyncMock(
        side_effect=lambda key: (
            {"para_value": today.isoformat()}
            if key == IDF_FORECAST_SWITCH_LAST_RESET_KEY
            else {"para_value": "true"}
        )
    )
    processor.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(
        return_value=[
            {"time_block": 5, "forecast": 100.0, "forecasted_frequency": 49.0},
            {"time_block": 20, "forecast": 200.0, "forecasted_frequency": 50.0},
        ]
    )
    processor.demand_repository.serve_collection.find_one = AsyncMock(return_value=None)
    update_result = MagicMock(upserted_id=None, modified_count=1)
    processor.demand_repository.serve_collection.update_one = AsyncMock(
        return_value=update_result
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
    assert skipped == 1
    written_blocks = [
        c[0][0]["time_block"]
        for c in processor.demand_repository.serve_collection.update_one.call_args_list
    ]
    assert 5 not in written_blocks
    assert 20 in written_blocks


@pytest.mark.asyncio
async def test_run_merge_csv_mode_skips_invalid_forecast(processor):
    today = date(2026, 5, 15)
    scheduled_at = datetime(2026, 5, 15)
    ctx = {
        "today_ist": today,
        "current_block": 10,
        "after_evening_cutover": False,
        "min_ahead_blocks": 5,
    }

    processor.parameter_repository.ensure_idf_forecast_switch_default = AsyncMock()
    processor.parameter_repository.get_by_key = AsyncMock(
        side_effect=lambda key: (
            {"para_value": today.isoformat()}
            if key == IDF_FORECAST_SWITCH_LAST_RESET_KEY
            else {"para_value": "false"}
        )
    )
    processor.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(
        return_value=[
            {"time_block": 10, "forecast": 0},
            {"time_block": 11, "forecast": 150.0},
        ]
    )
    processor.demand_repository.serve_collection.find_one = AsyncMock(
        return_value={"forecasted_frequency": 48.0}
    )
    update_result = MagicMock(upserted_id=None, modified_count=1)
    processor.demand_repository.serve_collection.update_one = AsyncMock(
        return_value=update_result
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
    assert skipped == 1
    processor.forecast_repository.find_by_scheduled_at_and_source.assert_called_with(
        scheduled_at, FORECAST_SOURCE_CSV
    )
    assert processor.demand_repository.serve_collection.update_one.call_count == 1
    update_set = processor.demand_repository.serve_collection.update_one.call_args[0][1][
        "$set"
    ]
    assert update_set["forecast"] == 150.0
    assert update_set["source_forecast"] == FORECAST_SOURCE_CSV
