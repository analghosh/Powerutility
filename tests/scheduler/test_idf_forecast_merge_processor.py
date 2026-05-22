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


def _session_mapping_9_10():
    return {
        9: {"session_number": 5, "Session_Id": 5},
        10: {"session_number": 5, "Session_Id": 5},
    }


@pytest.fixture
def processor():
    proc = IdfForecastMergeProcessor()
    proc.demand_repository = MagicMock()
    proc.demand_repository.serve_collection = AsyncMock()
    proc.forecast_repository = AsyncMock()
    proc.parameter_repository = AsyncMock()
    proc.rtm_hardcoded_repository = MagicMock()
    proc.rtm_hardcoded_repository.get_session_mapping = AsyncMock(
        return_value=_session_mapping_9_10()
    )
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
async def test_csv_forecast_zero_writes_zero_from_active_session(processor):
    assert processor._parse_csv_forecast_mw(0) is None
    from libs.helpers.idf_active_session import csv_forecast_mw

    assert csv_forecast_mw(0, treat_blank_as_zero=True) == 0.0


@pytest.mark.asyncio
async def test_run_merge_api_mode_applies_block_filter(processor):
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
    written_blocks = [
        c[0][0]["time_block"]
        for c in processor.demand_repository.serve_collection.update_one.call_args_list
    ]
    assert 5 not in written_blocks
    assert 20 in written_blocks
    assert all(
        c[0][1]["$set"]["source_forecast"] == FORECAST_SOURCE_RTM_API
        for c in processor.demand_repository.serve_collection.update_one.call_args_list
    )


@pytest.mark.asyncio
async def test_run_merge_active_session_csv_zero_flips_and_writes_api(processor):
    """CSV zero on active block 9 -> flip Auto; demand_serve updated from API."""
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

    async def find_forecast(scheduled_at, source):
        if source == FORECAST_SOURCE_CSV:
            return [
                {"time_block": 9, "forecast": 0},
                {"time_block": 10, "forecast": 100.0},
            ]
        return [
            {"time_block": 9, "forecast": 77.0, "forecasted_frequency": 48.0},
            {"time_block": 10, "forecast": 88.0, "forecasted_frequency": 48.5},
            {"time_block": 20, "forecast": 200.0, "forecasted_frequency": 50.0},
        ]

    processor.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(
        side_effect=find_forecast
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
    ), patch(
        "services.scheduler.app.processors.idf_forecast_merge_processor.should_write_forecast_block",
        return_value=True,
    ):
        await processor.run_merge()

    processor.parameter_repository.upsert_by_key.assert_any_call(
        IDF_FORECAST_SWITCH_KEY, "true"
    )
    written = [
        c[0][1]["$set"]
        for c in processor.demand_repository.serve_collection.update_one.call_args_list
    ]
    block_9 = next(w for w in written if w["time_block"] == 9)
    assert block_9["forecast"] == 77.0
    assert block_9["source_forecast"] == FORECAST_SOURCE_RTM_API


@pytest.mark.asyncio
async def test_run_merge_tomorrow_active_zero_does_not_flip(processor):
    """Tomorrow CSV zero on active blocks must not flip; only today's active session counts."""
    today = date(2026, 5, 15)
    tomorrow = date(2026, 5, 16)
    ctx = {
        "today_ist": today,
        "current_block": 10,
        "after_evening_cutover": False,
        "min_ahead_blocks": 5,
        "include_tomorrow_forecast": True,
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

    async def find_forecast(scheduled_at, source):
        day = scheduled_at.date()
        if source == FORECAST_SOURCE_CSV:
            if day == today:
                return [
                    {"time_block": 9, "forecast": 100.0},
                    {"time_block": 10, "forecast": 110.0},
                    {"time_block": 20, "forecast": 50.0},
                ]
            return [
                {"time_block": 9, "forecast": 0},
                {"time_block": 10, "forecast": 0},
            ]
        return []

    processor.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(
        side_effect=find_forecast
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
        return_value=[today, tomorrow],
    ), patch(
        "services.scheduler.app.processors.idf_forecast_merge_processor.should_write_forecast_block",
        return_value=True,
    ):
        await processor.run_merge()

    processor.parameter_repository.upsert_by_key.assert_not_called()
    written = [
        c[0][1]["$set"]
        for c in processor.demand_repository.serve_collection.update_one.call_args_list
    ]
    assert all(w["source_forecast"] == FORECAST_SOURCE_CSV for w in written)


@pytest.mark.asyncio
async def test_run_merge_both_api_only_stays_manual_without_csv_zero(processor):
    """API-only on active session does not flip; only CSV zero/null does."""
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

    async def find_forecast(scheduled_at, source):
        if source == FORECAST_SOURCE_RTM_API:
            return [
                {"time_block": 9, "forecast": 80.0, "forecasted_frequency": 48.0},
                {"time_block": 10, "forecast": 90.0, "forecasted_frequency": 48.5},
                {"time_block": 20, "forecast": 200.0, "forecasted_frequency": 50.0},
            ]
        return [
            {"time_block": 9, "forecast": 100.0},
            {"time_block": 10, "forecast": 110.0},
        ]

    processor.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(
        side_effect=find_forecast
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
        await processor.run_merge()

    processor.parameter_repository.upsert_by_key.assert_not_called()
    written = [
        c[0][1]["$set"]
        for c in processor.demand_repository.serve_collection.update_one.call_args_list
    ]
    assert all(w["source_forecast"] == FORECAST_SOURCE_CSV for w in written)


@pytest.mark.asyncio
async def test_run_merge_csv_zero_writes_zero_in_scope(processor):
    """Block 20 in scope: CSV zero writes 0; active 9/10 valid CSV -> no flip."""
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

    async def find_forecast(scheduled_at, source):
        if source == FORECAST_SOURCE_CSV:
            return [
                {"time_block": 9, "forecast": 50.0},
                {"time_block": 10, "forecast": 60.0},
                {"time_block": 20, "forecast": 0},
            ]
        return []

    processor.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(
        side_effect=find_forecast
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
    processor.parameter_repository.upsert_by_key.assert_not_called()
    update_set = processor.demand_repository.serve_collection.update_one.call_args[0][1][
        "$set"
    ]
    assert update_set["time_block"] == 20
    assert update_set["forecast"] == 0.0
    assert update_set["source_forecast"] == FORECAST_SOURCE_CSV
