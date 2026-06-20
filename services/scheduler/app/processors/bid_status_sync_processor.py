"""
Bid Status Sync Processor
Refactored to delegate to SingleBidBookProcessor for fetching Single Bid Book details.
This replaces the old transaction-based sync which relied on microservice calls.
"""

import logging
from typing import Dict, Any
from datetime import datetime, time

# Use the Scheduler's internal processor which uses the correct SchedulerExchangeAPIClient
from services.scheduler.app.processors.single_bid_book_processor import single_bid_book_processor

logger = logging.getLogger(__name__)

class BidStatusSyncProcessor:
    """
    Processor to sync bid status.
    Delegates to SingleBidBookProcessor to fetch full Single Bid Book by Delivery Date.
    """
    
    def __init__(self):
        # We can keep init empty or use it if needed. 
        # The single_bid_book_processor is a singleton instance.
        pass


    async def execute_dam_sync(self, payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute DAM sync.
        Delegates to SingleBidBookProcessor.execute_dam_cron()
        """
        logger.info("[BidStatusSync] Delegating DAM Sync to SingleBidBookProcessor")
        try:
            return await single_bid_book_processor.execute_dam_cron()
        except Exception as e:
            logger.error(f"[BidStatusSync] Error in DAM sync delegation: {e}")
            return {"success": False, "error": str(e)}

    async def execute_rtm_sync(self, payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute RTM sync.
        Delegates to SingleBidBookProcessor.execute_rtm_cron()
        """
        logger.info("[BidStatusSync] Delegating RTM Sync to SingleBidBookProcessor")
        try:
            return await single_bid_book_processor.execute_rtm_cron()
        except Exception as e:
            logger.error(f"[BidStatusSync] Error in RTM sync delegation: {e}")
            return {"success": False, "error": str(e)}

    async def execute_gdam_sync(self, payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute GDAM sync.
        Delegates to SingleBidBookProcessor.execute_gdam_cron()
        """
        logger.info("[BidStatusSync] Delegating GDAM Sync to SingleBidBookProcessor")
        try:
            return await single_bid_book_processor.execute_gdam_cron()
        except Exception as e:
            logger.error(f"[BidStatusSync] Error in GDAM sync delegation: {e}")
            return {"success": False, "error": str(e)}

    async def execute_hpdam_sync(self, payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute HPDAM sync.
        Delegates to SingleBidBookProcessor.execute_hpdam_cron()
        """
        logger.info("[BidStatusSync] Delegating HPDAM Sync to SingleBidBookProcessor")
        try:
            return await single_bid_book_processor.execute_hpdam_cron()
        except Exception as e:
            logger.error(f"[BidStatusSync] Error in HPDAM sync delegation: {e}")
            return {"success": False, "error": str(e)}

# Singleton instance
bid_status_sync_processor = BidStatusSyncProcessor()
