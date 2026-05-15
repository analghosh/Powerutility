"""
Unit tests for forecast_serve repository — separate documents per source.
"""

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API
from libs.repository.forecast_serve_repository import ForecastServeRepository


@pytest.fixture
def repo():
    r = ForecastServeRepository(
        mongodb_uri="mongodb://localhost:27017/test",
        database_name="test_db",
        collection_name="forecast_serve",
    )
    r.collection = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_upsert_rtm_does_not_touch_csv_filter(repo):
    scheduled_at = datetime(2026, 5, 15)
    mock_result = MagicMock(upserted_id="abc", modified_count=1)
    repo.collection.update_one = AsyncMock(return_value=mock_result)

    await repo.upsert_forecast(
        scheduled_at=scheduled_at,
        time_block=42,
        forecast=1250.5,
        source=FORECAST_SOURCE_RTM_API,
    )

    call_args = repo.collection.update_one.call_args
    filter_doc = call_args[0][0]
    assert filter_doc["source"] == FORECAST_SOURCE_RTM_API
    assert filter_doc["time_block"] == 42
    assert "$set" in call_args[0][1]
    assert call_args[0][1]["$set"]["forecast"] == 1250.5


@pytest.mark.asyncio
async def test_upsert_csv_with_null_forecast(repo):
    scheduled_at = datetime(2026, 5, 15)
    mock_result = MagicMock(upserted_id=None, modified_count=1)
    repo.collection.update_one = AsyncMock(return_value=mock_result)

    await repo.upsert_forecast(
        scheduled_at=scheduled_at,
        time_block=42,
        forecast=None,
        source=FORECAST_SOURCE_CSV,
    )

    update_doc = repo.collection.update_one.call_args[0][1]
    assert update_doc["$set"]["forecast"] is None
    assert repo.collection.update_one.call_args[0][0]["source"] == FORECAST_SOURCE_CSV


@pytest.mark.asyncio
async def test_bulk_upsert_builds_separate_updates_per_source(repo):
    scheduled_at = datetime(2026, 5, 15)
    mock_bulk = MagicMock(matched_count=2, modified_count=1, upserted_ids={0: "id1"})
    repo.collection.bulk_write = AsyncMock(return_value=mock_bulk)

    records = [
        {"scheduled_at": scheduled_at, "time_block": 1, "forecast": 100.0},
        {"scheduled_at": scheduled_at, "time_block": 2, "forecast": None},
    ]
    result = await repo.bulk_upsert_forecasts(records, source=FORECAST_SOURCE_CSV)

    assert repo.collection.bulk_write.called
    operations = repo.collection.bulk_write.call_args[0][0]
    assert len(operations) == 2
    assert result["upserted"] == 1


@pytest.mark.asyncio
async def test_rtm_processor_uses_forecast_serve_not_demand_serve():
    from services.ingestion.app.processors.rtm_forecast_api_processor import RTMForecastAPIProcessor

    processor = RTMForecastAPIProcessor()
    scheduled_at = datetime(2026, 5, 15)
    data = [{"time_block": 10, "demand_forecast_mw": 500.0}]

    with patch(
        "services.ingestion.app.processors.rtm_forecast_api_processor.forecast_serve_repository"
    ) as mock_repo:
        mock_repo.bulk_upsert_forecasts = AsyncMock(return_value={"modified": 1, "upserted": 0})
        count = await processor._store_forecast_serve(
            scheduled_at=scheduled_at,
            data=data,
            today_ist=scheduled_at.date(),
            current_block=1,
        )
        assert count == 1
        mock_repo.bulk_upsert_forecasts.assert_called_once()
        call_args = mock_repo.bulk_upsert_forecasts.call_args
        assert call_args.kwargs["source"] == FORECAST_SOURCE_RTM_API
        assert call_args.args[0][0]["forecast"] == 500.0


def test_should_write_forecast_block_active_session_only_today():
    from services.ingestion.app.processors.rtm_forecast_api_processor import (
        _should_write_forecast_block,
    )

    today = date(2026, 5, 15)
    # current_block=10, min_ahead=5 -> target=15, next_block=16 -> blocks 16..96 only
    assert _should_write_forecast_block(today, 16, today, 10, False, 5) is True
    assert _should_write_forecast_block(today, 15, today, 10, False, 5) is False
    assert _should_write_forecast_block(today, 1, today, 10, False, 5) is False


def test_should_write_forecast_block_after_evening_cutover_all_blocks():
    from services.ingestion.app.processors.rtm_forecast_api_processor import (
        _should_write_forecast_block,
    )

    today = date(2026, 5, 15)
    assert _should_write_forecast_block(today, 1, today, 50, True, 5) is True
    assert _should_write_forecast_block(today, 96, today, 50, True, 5) is True


@pytest.mark.asyncio
async def test_store_forecast_serve_skips_past_blocks_in_active_session():
    from services.ingestion.app.processors.rtm_forecast_api_processor import (
        RTMForecastAPIProcessor,
    )

    processor = RTMForecastAPIProcessor()
    scheduled_at = datetime(2026, 5, 15)
    data = [
        {"time_block": 5, "demand_forecast_mw": 100.0},
        {"time_block": 20, "demand_forecast_mw": 200.0},
    ]

    with patch(
        "services.ingestion.app.processors.rtm_forecast_api_processor.forecast_serve_repository"
    ) as mock_repo:
        mock_repo.bulk_upsert_forecasts = AsyncMock(return_value={"modified": 1, "upserted": 0})
        count = await processor._store_forecast_serve(
            scheduled_at=scheduled_at,
            data=data,
            today_ist=scheduled_at.date(),
            current_block=10,
            after_evening_cutover=False,
            min_ahead_blocks=5,
        )
        assert count == 1
        written_blocks = [r["time_block"] for r in mock_repo.bulk_upsert_forecasts.call_args.args[0]]
        assert 5 not in written_blocks
        assert 20 in written_blocks


@pytest.mark.asyncio
async def test_store_forecast_serve_writes_all_blocks_after_cutover():
    from services.ingestion.app.processors.rtm_forecast_api_processor import (
        RTMForecastAPIProcessor,
    )

    processor = RTMForecastAPIProcessor()
    scheduled_at = datetime(2026, 5, 15)
    data = [
        {"time_block": 5, "demand_forecast_mw": 100.0},
        {"time_block": 20, "demand_forecast_mw": 200.0},
    ]

    with patch(
        "services.ingestion.app.processors.rtm_forecast_api_processor.forecast_serve_repository"
    ) as mock_repo:
        mock_repo.bulk_upsert_forecasts = AsyncMock(return_value={"modified": 2, "upserted": 0})
        count = await processor._store_forecast_serve(
            scheduled_at=scheduled_at,
            data=data,
            today_ist=scheduled_at.date(),
            current_block=10,
            after_evening_cutover=True,
            min_ahead_blocks=5,
        )
        assert count == 2
        written_blocks = sorted(r["time_block"] for r in mock_repo.bulk_upsert_forecasts.call_args.args[0])
        assert written_blocks == [5, 20]


def test_demand_processor_parses_csv_forecast():
    from services.scheduler.app.processors.demand_processor import DemandProcessor

    proc = DemandProcessor()
    assert proc._parse_idf_forecast_from_raw({"forecast": "1200.5"}) == 1200.5
    assert proc._parse_idf_forecast_from_raw({"forecast": ""}) is None
    assert proc._parse_idf_forecast_from_raw({}) is None
