"""
GDAM live sync for a chosen delivery date.

The cron run only ever fetches the date IEX currently offers (T+1), so a day that
has already traded could never be pulled in. The GDAM trigger takes a delivery
date, and reports a failed IEX fetch as an error instead of "success".
"""

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from services.scheduler.app.processors.bid_trade_live_sync_processor import BidTradeLiveSyncProcessor
from services.scheduler.app.routers import bid_trade_live_sync_router as live_sync_router
from services.scheduler.app.routers.bid_trade_live_sync_router import gdam_delivery_timestamp

TODAY = date(2026, 10, 3)
OCT_3 = 1790985600  # what IEX sends as DeliveryDate for 2026-10-03 (midnight UTC)


class TestGdamDeliveryTimestamp:
    def test_matches_the_timestamp_iex_uses(self):
        assert gdam_delivery_timestamp("2026-10-03", today=TODAY) == OCT_3

    @pytest.mark.parametrize("value", ["2026-10-04", "2026-09-02"])
    def test_accepts_tomorrow_and_the_oldest_allowed_day(self, value):
        gdam_delivery_timestamp(value, today=TODAY)

    @pytest.mark.parametrize("value", ["2026-10-05", "2026-09-01"])
    def test_rejects_dates_outside_the_window(self, value):
        with pytest.raises(ValueError, match="between"):
            gdam_delivery_timestamp(value, today=TODAY)

    @pytest.mark.parametrize("value", ["03-10-2026", "2026-13-01", "", None])
    def test_rejects_malformed_dates(self, value):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            gdam_delivery_timestamp(value, today=TODAY)


def bare_processor():
    """A processor with only what _execute_sync touches, so no database is needed."""
    processor = BidTradeLiveSyncProcessor.__new__(BidTradeLiveSyncProcessor)
    processor._live_market_config = {
        "gdam": {
            "get_delivery_dates": AsyncMock(return_value=1791072000),
            "get_single_bid": AsyncMock(return_value={"SingleBidDetails": []}),
            "is_multi_date": False,
        }
    }
    processor._process_and_store = AsyncMock(return_value={"processed": 0})
    return processor


class TestExecuteGdamLiveSync:
    @pytest.mark.asyncio
    async def test_a_given_date_is_fetched_without_asking_iex_for_one(self):
        processor = bare_processor()
        result = await processor.execute_gdam_live_sync(OCT_3)

        gdam = processor._live_market_config["gdam"]
        gdam["get_delivery_dates"].assert_not_awaited()
        gdam["get_single_bid"].assert_awaited_once_with(OCT_3)
        assert result["status"] == "success"
        assert result["details"][0]["date"] == OCT_3

    @pytest.mark.asyncio
    async def test_without_a_date_it_takes_the_one_iex_offers(self):
        processor = bare_processor()
        await processor.execute_gdam_live_sync()

        gdam = processor._live_market_config["gdam"]
        gdam["get_delivery_dates"].assert_awaited_once()
        gdam["get_single_bid"].assert_awaited_once_with(1791072000)


class TestTriggerGdamLiveSync:
    @pytest.mark.asyncio
    async def test_bad_date_is_a_400(self):
        with pytest.raises(HTTPException) as err:
            await live_sync_router.trigger_gdam_live_sync("03-10-2026")
        assert err.value.status_code == 400

    @pytest.mark.asyncio
    async def test_failed_iex_fetch_is_a_502_with_the_reason(self):
        failed = {"status": "error", "message": "Server error '500 Internal Server Error'"}
        with patch.object(live_sync_router, "BidTradeLiveSyncProcessor") as processor_cls:
            processor_cls.return_value.execute_gdam_live_sync = AsyncMock(return_value=failed)
            with pytest.raises(HTTPException) as err:
                await live_sync_router.trigger_gdam_live_sync(None)
        assert err.value.status_code == 502
        assert "500 Internal Server Error" in err.value.detail

    @pytest.mark.asyncio
    async def test_date_reaches_the_processor_as_the_iex_timestamp(self):
        done = {"status": "success", "details": [{"date": OCT_3, "result": {"processed": 3}}]}
        with patch.object(live_sync_router, "BidTradeLiveSyncProcessor") as processor_cls, \
             patch.object(live_sync_router, "gdam_delivery_timestamp", return_value=OCT_3) as to_ts:
            processor_cls.return_value.execute_gdam_live_sync = AsyncMock(return_value=done)
            response = await live_sync_router.trigger_gdam_live_sync("2026-10-03")

        to_ts.assert_called_once_with("2026-10-03")
        processor_cls.return_value.execute_gdam_live_sync.assert_awaited_once_with(OCT_3)
        assert response["status"] == "success"
        assert response["data"] == done

    @pytest.mark.asyncio
    async def test_no_date_syncs_the_current_one(self):
        done = {"status": "success", "details": []}
        with patch.object(live_sync_router, "BidTradeLiveSyncProcessor") as processor_cls:
            processor_cls.return_value.execute_gdam_live_sync = AsyncMock(return_value=done)
            await live_sync_router.trigger_gdam_live_sync(None)
        processor_cls.return_value.execute_gdam_live_sync.assert_awaited_once_with(None)
