from fastapi import APIRouter, HTTPException
from typing import Dict, Any, Optional
from datetime import date, datetime, timedelta, timezone
import logging

import pytz

from ..core.config import settings
from ..processors.bid_trade_live_sync_processor import BidTradeLiveSyncProcessor

# How far back a GDAM date can be pulled in again; each sync is three IEX calls.
GDAM_RESYNC_MAX_DAYS_BACK = 31


def gdam_delivery_timestamp(value: str, today: Optional[date] = None) -> int:
    """
    Turn a YYYY-MM-DD delivery date into the timestamp IEX expects (midnight UTC,
    e.g. 2026-10-03 -> 1790985600). Raises ValueError for a malformed date or one
    outside the last GDAM_RESYNC_MAX_DAYS_BACK days through tomorrow.
    """
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"delivery_date must be YYYY-MM-DD, got {value!r}")

    if today is None:
        today = datetime.now(pytz.timezone(settings.CRON_TIMEZONE)).date()
    earliest = today - timedelta(days=GDAM_RESYNC_MAX_DAYS_BACK)
    latest = today + timedelta(days=1)
    if not earliest <= day <= latest:
        raise ValueError(f"delivery_date must be between {earliest} and {latest}, got {day}")

    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())

router = APIRouter(
    prefix="/bid-trade-live-sync",
    tags=["Bid Trade Live Sync"]
)

logger = logging.getLogger(__name__)


@router.post("/trigger-dam", response_model=Dict[str, Any], include_in_schema=False)
async def trigger_dam_live_sync():
    """
    Trigger DAM bid trade live sync.
    Called by the processing service to proxy the scheduler's execute_dam_live_sync.
    """
    try:
        logger.info("Triggering DAM bid trade live sync via API")
        processor = BidTradeLiveSyncProcessor()
        result = await processor.execute_dam_live_sync()
        return {
            "status": "success",
            "message": "DAM bid trade live sync completed successfully",
            "data": result
        }
    except Exception as e:
        logger.error(f"Error triggering DAM bid trade live sync: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to trigger DAM bid trade live sync: {str(e)}")


@router.post("/trigger-gdam", response_model=Dict[str, Any], include_in_schema=False)
async def trigger_gdam_live_sync(delivery_date: Optional[str] = None):
    """
    Trigger GDAM bid trade live sync, optionally for a given YYYY-MM-DD delivery date.
    Called by the processing service to proxy the scheduler's execute_gdam_live_sync.

    Unlike the cron run, a GDAM fetch that fails is answered with an error rather
    than "success", so the person who pressed the button sees why.
    """
    try:
        delivery_ts = gdam_delivery_timestamp(delivery_date) if delivery_date else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        logger.info(f"Triggering GDAM bid trade live sync via API, delivery_date={delivery_date}")
        processor = BidTradeLiveSyncProcessor()
        result = await processor.execute_gdam_live_sync(delivery_ts)
    except Exception as e:
        logger.error(f"Error triggering GDAM bid trade live sync: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to trigger GDAM bid trade live sync: {str(e)}")

    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=f"GDAM sync failed: {result.get('message')}")

    return {
        "status": "success",
        "message": "GDAM bid trade live sync completed successfully",
        "data": result
    }


@router.post("/trigger-rtm", response_model=Dict[str, Any], include_in_schema=False)
async def trigger_rtm_live_sync():
    """
    Trigger RTM bid trade live sync.
    Called by the processing service to proxy the scheduler's execute_rtm_live_sync.
    """
    try:
        logger.info("Triggering RTM bid trade live sync via API")
        processor = BidTradeLiveSyncProcessor()
        result = await processor.execute_rtm_live_sync()
        return {
            "status": "success",
            "message": "RTM bid trade live sync completed successfully",
            "data": result
        }
    except Exception as e:
        logger.error(f"Error triggering RTM bid trade live sync: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to trigger RTM bid trade live sync: {str(e)}")
