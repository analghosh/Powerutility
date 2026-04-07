from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import logging

from ..processors.bid_trade_live_sync_processor import BidTradeLiveSyncProcessor

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
