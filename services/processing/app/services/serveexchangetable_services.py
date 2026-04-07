"""
Dashboard service layer using repository pattern.
Handles business logic for dashboard-related data operations.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from ..repository.serve_exchange_table_repository import serveExchangeTableRepository


logger = logging.getLogger(__name__)


class ServeexchangetableServices:
    """
    Service class responsible for dashboard business logic.
    """

    def __init__(self, collection_name: str = "serve_exchange_table"):
        self.serve_exchange_repo = serveExchangeTableRepository(collection_name=collection_name)
        


    async def exchange_by_date(self, schedule_date):
        """
            Fetch Exchange data for a given schedule date.

            Args:
                schedule_date (str): The schedule date in 'YYYY-MM-DD' format.

            Returns:
                Optional[List[Dict[str, Any]]]: A list of aggregated exchange data,
                                                or an empty list if no data is found.
            """

        try:
            if(schedule_date):
                schedule_date=schedule_date
            else:
                # Temporary: Hardcoded schedule date for fetching DSM records
                schedule_date= '2025-08-04'
            
            cursor = self.serve_exchange_repo.get_exchange(schedule_date)
           
            logger.warning(f"[DashboardService] =========++++++++.: {cursor}")
            return cursor
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise
    
    async def exchange_by_date_and_time_block(self, schedule_date, time_block):
        """
            Fetch Exchange data for a given schedule date.

            Args:
                schedule_date (str): The schedule date in 'YYYY-MM-DD' format.

            Returns:
                Optional[List[Dict[str, Any]]]: A list of aggregated exchange data,
                                                or an empty list if no data is found.
            """

        try:
            if(schedule_date):
                schedule_date=schedule_date
            else:
                # Temporary: Hardcoded schedule date for fetching DSM records
                schedule_date= '2025-08-04'
            
            cursor = self.serve_exchange_repo.get_exchange_by_date_time_block(schedule_date, time_block)
           
            logger.warning(f"[get_exchange_by_date_time_block] =========++++++++.: {cursor}")
            return cursor
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise


# Global instance
serveexchangetable_services = ServeexchangetableServices()
