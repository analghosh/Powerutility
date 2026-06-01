"""
Market service layer using repository pattern.
Handles business logic for market-related data operations.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from ..repository.market_repository import market_repository
from libs.helpers.timeblock_helper import ist_now, block_from_hhmm
from libs.utils.date_filters import validate_schedule_date, normalize_schedule_date

logger = logging.getLogger(__name__)


class MarketService:
    """
    Service layer for Market-related operations.
    Handles business logic and data processing.
    """

    def __init__(self):
        self.market_repository = market_repository

    async def get_exchange_graph_data(self, scheduled_at: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get market exchange data for graph visualization.
        
        Args:
            scheduled_at: Optional date filter in YYYY-MM-DD format
            
        Returns:
            List containing market exchange data for graphs
        """
        try:
            # Normalize and validate the schedule date
            original_date = scheduled_at
            scheduled_at = normalize_schedule_date(scheduled_at)
            
            logger.info(f"[MarketService] Fetching exchange graph data - Original: {original_date}, Normalized: {scheduled_at}")
            
            # Validate date format
            if not validate_schedule_date(scheduled_at):
                logger.error(f"[MarketService] Invalid date format: {scheduled_at}")
                return []
            
            # Calculate current block for RTM filtering (business logic in service)
            now_ist = ist_now()
            today_ist_str = now_ist.date().isoformat()
            is_today_ist = (today_ist_str == scheduled_at)
            
            if is_today_ist:
                current_block = block_from_hhmm((now_ist + timedelta(seconds=1)).strftime("%H:%M"))
                logger.info(f"[MarketService] Requested date {scheduled_at} is today ({today_ist_str}). Current IST time: {now_ist.strftime('%H:%M:%S')}, Current IST block: {current_block}")
            else:
                current_block = 96  # Show all blocks for non-today dates
                logger.info(f"[MarketService] Requested date {scheduled_at} is not today ({today_ist_str}). Using max block: {current_block}")
            
            logger.info(f"[MarketService] Calling repository with schedule_date='{scheduled_at}', current_block={current_block}")
            
            # Repository just fetches data with the calculated parameters
            result = self.market_repository.get_exchange_data(scheduled_at, current_block)
            
            logger.info(f"[MarketService] Repository returned {len(result)} records")
            
            return self._flatten_data(result)  # Return raw data to match v1 format
            
        except Exception as e:
            logger.exception(f"[MarketService] Failed to fetch exchange graph data: {str(e)}")
            raise

    async def get_overview_graph_data(self, scheduled_at: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get market overview data for graph visualization.
        
        Args:
            scheduled_at: Optional date filter in YYYY-MM-DD format
            
        Returns:
            List containing market overview data for graphs
        """
        try:
            # Normalize and validate the schedule date
            scheduled_at = normalize_schedule_date(scheduled_at)
            
            logger.info(f"[MarketService] Fetching overview graph data for date: {scheduled_at}")
            
            # Validate date format
            if not validate_schedule_date(scheduled_at):
                logger.error(f"Invalid date format: {scheduled_at}")
                return []
            
            # Repository just fetches data - no business logic
            result = self.market_repository.get_bid_trade_data(scheduled_at)
            
            return self._flatten_data(result)  # Return flattened data
            
        except Exception as e:
            logger.exception(f"[MarketService] Failed to fetch overview graph data: {str(e)}")
            raise

    async def get_exchange_table_data(self, scheduled_at: Optional[str] = None, time_block: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get market exchange data in table format.
        
        Args:
            scheduled_at: Optional date filter in YYYY-MM-DD format
            time_block: Optional time block filter (1-96)
            
        Returns:
            List containing market exchange data in table format
        """
        try:
            # Normalize and validate the schedule date
            scheduled_at = normalize_schedule_date(scheduled_at)
            
            logger.info(f"[MarketService] Fetching exchange table data for date: {scheduled_at}, time_block: {time_block}")
            
            # Validate date format
            if not validate_schedule_date(scheduled_at):
                logger.error(f"Invalid date format: {scheduled_at}")
                return []
            
            # Repository just fetches data - no business logic
            result = self.market_repository.get_exchange_table_data(scheduled_at, time_block)
            
            return self._flatten_data(result)  # Return raw data to match v1 format
            
        except Exception as e:
            logger.exception(f"[MarketService] Failed to fetch exchange table data: {str(e)}")
            raise

    async def get_bidding_summary_data(self, scheduled_at: Optional[str] = None, time_block: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get market bidding summary data in table format.
        
        Args:
            scheduled_at: Optional date filter in YYYY-MM-DD format
            time_block: Optional time block filter (1-96)
            
        Returns:
            List containing market bidding summary data in table format
        """
        try:
            # Normalize and validate the schedule date
            scheduled_at = normalize_schedule_date(scheduled_at)
            
            logger.info(f"[MarketService] Fetching bidding summary data for date: {scheduled_at}, time_block: {time_block}")
            
            # Validate date format
            if not validate_schedule_date(scheduled_at):
                logger.error(f"Invalid date format: {scheduled_at}")
                return []
            
            # Repository just fetches data - no business logic
            if time_block is not None:
                result = self.market_repository.get_bid_trade_by_time_block(scheduled_at, time_block)
            else:
                result = self.market_repository.get_bid_trade_data(scheduled_at)
            
            return self._flatten_data(result)  # Return flattened data
            
        except Exception as e:
            logger.exception(f"[MarketService] Failed to fetch bidding summary data: {str(e)}")
            raise

    def _flatten_data(self, data: Any) -> Any:
        """
        Recursively traverse data and flatten any dict with a 'total' key to its value.
        This ensures compatibility for generic frontends expecting scalars.
        """
        if isinstance(data, list):
            return [self._flatten_data(item) for item in data]
        elif isinstance(data, dict):
            new_data = {}
            for k, v in data.items():
                # Generic check: if value is a dict and has 'total', flatten it
                if isinstance(v, dict) and "total" in v:
                    new_data[k] = v["total"]
                else:
                    new_data[k] = self._flatten_data(v)
            return new_data
        else:
            return data


# Create service instance
market_service = MarketService()