import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from services.processing.app.v2.repository.base_repository import GenericRepository
from services.processing.app.core.config import settings

logger = logging.getLogger(__name__)

class ExchangeTradeServices:
    def __init__(self):
        self.generic_repo = GenericRepository()
        self.collection_name = getattr(settings, 'EXCHANGE_TRADE_SERVE_COLLECTION', 'exchange_trade_serve')

    async def get_exchange_trade_data(
        self,
        scheduled_at: Optional[str] = None,
        from_timeblock: int = 1,
        to_timeblock: int = 96,
        market: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch exchange trade data based on filters.
        """
        try:
            # Parse requested date
            if scheduled_at:
                try:
                    target_date = datetime.strptime(scheduled_at, "%Y-%m-%d").date()
                except ValueError:
                    target_date = datetime.now().date()
            else:
                target_date = datetime.now().date()

            # Build query with date range to avoid timezone exact-match issues
            start_dt = datetime.combine(target_date, datetime.min.time())
            end_dt = datetime.combine(target_date, datetime.max.time())
            
            query = {
                "scheduled_at": {"$gte": start_dt, "$lte": end_dt},
                "time_block": {"$gte": from_timeblock, "$lte": to_timeblock}
            }
            if market:
                query["market_type"] = market.upper()
                
            # Fetch data with async repository method
            # get_all_async usually returns the records, we will sort them manually or if supported by query parser
            data = await self.generic_repo.get_all_async(self.collection_name, query)
            
            if data:
                # Remove _id from data for clean JSON response
                for item in data:
                    if '_id' in item:
                        item['_id'] = str(item['_id'])
                
                # Sort by time_block ascending
                data = sorted(data, key=lambda x: x.get('time_block', 0))

            return {"data": data if data else []}
            
        except Exception as e:
            logger.exception(f"Error fetching exchange trade data: {e}")
            return {"data": [], "error": str(e)}
