"""
Dashboard service layer using repository pattern.
Handles business logic for dashboard-related data operations.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from ..repository.dsm_repository import dsm_repository
# from ...core.helpers import dsm_calculation
from ...core.config import settings

logger = logging.getLogger(__name__)


class DsmService:
    """
    Service layer for DSM-related operations.
    Handles business logic and data processing.
    """

    def __init__(self):
        self.dsm_repo = dsm_repository
        self.demand_serve = settings.MONGODB_DEMAND_SERVE_COLLECTION
        self.demand_serve_historical = (
            settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION
        )

    async def get_dsm_data(
        self, scheduled_at: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get DSM data for a specific schedule date.

        Args:
            scheduled_at: Optional date filter in YYYY-MM-DD format

        Returns:
            Dict containing DSM data and metadata
        """
        try:
            if not scheduled_at:
                # Temporary: Hardcoded schedule date for fetching DSM records
                scheduled_at = datetime.now().replace(
                    hour=0, minute=0, second=0, microsecond=0
                )

            # Test collection access first
            logger.info(f"[DsmService] Testing collection access: {self.demand_serve}")
            test_docs = await self.dsm_repo.test_collection_access(self.demand_serve, limit=5)
            logger.info(
                f"[DsmService] Collection test - found {len(test_docs)} documents"
            )
            if test_docs:
                logger.info(f"[DsmService] Sample document: {test_docs[0]}")

            # Get DSM data using repository
            cursor = await self.dsm_repo.get_dsm_data_pipeline(
                self.demand_serve, scheduled_at
            )

            return {"data": cursor, "count": len(cursor) if cursor else 0}
        except Exception as e:
            logger.exception(f"[DsmService] Failed to fetch DSM data: {str(e)}")
            raise

    async def get_dsm_historical_data(
        self, from_date: Optional[datetime] = None, to_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Get historical DSM data for a date range using MongoDB aggregation pipeline.

        Args:
            from_date: Start date
            to_date: End date

        Returns:
            List containing historical DSM data and metadata
        """
        try:
            # Set proper time components for provided dates, or use defaults
            if from_date and to_date:
                # Use provided dates but ensure proper time components
                from_date = from_date.replace(hour=0, minute=0, second=0, microsecond=0)
                to_date = to_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                logger.info(f"[DsmService] Using provided date range: {from_date} to {to_date}")
            elif not from_date and not to_date:
                # Default range if not provided
                from_date = datetime.now().replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                to_date = datetime.now().replace(
                    hour=23, minute=59, second=59, microsecond=999999
                )
                logger.info(f"[DsmService] Using default date range: {from_date} to {to_date}")
            else:
                # If only one date is provided, raise an error or use defaults
                raise ValueError("Both from_date and to_date must be provided together")

            # Get historical DSM data using repository
            cursor = await self.dsm_repo.get_dsm_historical_data_pipeline(
                self.demand_serve_historical, from_date, to_date
            )
            logger.info(f"[DsmService] Retrieved {len(cursor) if cursor else 0} result(s) from historical DSM data")
            return cursor
        except Exception as e:
            logger.exception(
                f"[DsmService] Failed to fetch historical DSM data: {str(e)}"
            )
            raise

    async def get_dsm_historical_table_data(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        time_block: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get historical DSM table data for a date range and time block using MongoDB aggregation pipeline.

        Args:
            from_date: Start date in YYYY-MM-DD format
            to_date: End date in YYYY-MM-DD format
            time_block: Optional time block filter

        Returns:
            List containing historical DSM table data and metadata
        """
        try:
            logger.warning(f"No dashboard data found: {from_date, to_date, time_block}")
            
            # Validate required parameters
            if from_date is None or to_date is None:
                raise ValueError("from_date and to_date are required for historical table data")
            
            # Get historical DSM table data using repository
            cursor = await self.dsm_repo.get_dsm_historical_table_data_pipeline(
                self.demand_serve_historical, from_date, to_date, time_block
            )
            logger.warning(f"No dashboard data found: {cursor}")
            return cursor
        except Exception as e:
            logger.exception(
                f"[DsmService] Failed to fetch historical DSM table data: {str(e)}"
            )
            raise


# Create service instance
dsm_service = DsmService()
