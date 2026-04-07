
"""
Supply V2 Service
Enhanced supply service with business logic and repository integration
"""

# Definitive fix to ensure file update: 2025-10-11 14:30:00

import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, date, timezone, timedelta
from fastapi import HTTPException, status
import asyncio

from ..repository.supply_repository import supply_repository
from libs.helpers.timeblock_helper import ist_now, block_from_hhmm
from libs.utils.date_filters import validate_schedule_date, normalize_schedule_date

logger = logging.getLogger(__name__)


class SupplyService:
    """
    Service layer for Supply V2 operations.
    Handles business logic and data processing.
    """

    def __init__(self):
        self.supply_repository = supply_repository

    IST = timezone(timedelta(hours=5, minutes=30))

    @classmethod
    def _current_time_block_ist(cls) -> int:
        now = datetime.now(cls.IST)
        total_min = now.hour * 60 + now.minute
        block = total_min // 15 + 1  # 1..96
        return block

    async def get_supply_graph_bundle(self, schedule_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get supply graph bundle data with business logic processing.
        
        Args:
            schedule_date: Optional date filter in YYYY-MM-DD format
            
        Returns:
            Dict containing supply graph bundle data
        """
        try:
            schedule_date = normalize_schedule_date(schedule_date or "")
            
            if not validate_schedule_date(schedule_date):
                return {}
            
            category_graph_task = self.supply_repository.get_supply_by_category_graph(schedule_date)
            technology_graph_task = self.supply_repository.get_supply_by_technology_graph(schedule_date)
            price_group_graph_task = self.supply_repository.get_supply_by_price_group_graph(schedule_date)
            price_range_task = self.supply_repository.get_price_group_definitions()
            max_updated_task = self.supply_repository.get_max_updated_at_from_supply(schedule_date)

            results = await asyncio.gather(
                category_graph_task,
                technology_graph_task,
                price_group_graph_task,
                price_range_task,
                max_updated_task
            )
            
            price_group_graph_response = {
                "price_group": results[2],
                "price_range": results[3]
            }

            # Get last_updated_at with fallback to current UTC time
            last_updated_at = results[4] if results[4] else datetime.now(timezone.utc)

            return {
                "category_graph": results[0],
                "technology_graph": results[1],
                "price_group_graph": price_group_graph_response,
                "last_updated_at": last_updated_at
            }
            
        except Exception as e:
            logger.error(f"[SupplyService] Error processing supply graph bundle data - schedule_date: {schedule_date}, error: {str(e)}")
            raise

    async def get_supply_block_bundle(self, schedule_date: Optional[str] = None, time_block: Optional[int] = None) -> Dict[str, Any]:
        """
        Get supply block bundle data with business logic and data processing.
        
        Args:
            schedule_date: Optional date filter in YYYY-MM-DD format.
            time_block: Optional time block filter (1-96).
            
        Returns:
            A dictionary containing the bundled supply data for a single block.
        """
        try:
            schedule_date = normalize_schedule_date(schedule_date or "")
            if not validate_schedule_date(schedule_date):
                return {}

            block = time_block if time_block is not None else self._current_time_block_ist()

            # Define the pipeline for price group definitions directly in the service
            price_group_definitions_pipeline = [
                {
                    "$project": {
                        "_id": 0,
                        "inserted_date": 0
                    }
                },
            ]

            # Create tasks for concurrent execution
            tasks = [
                self.supply_repository.get_supply_by_category_block(schedule_date, block),
                self.supply_repository.get_supply_by_technology_block(schedule_date, block),
                self.supply_repository.get_supply_by_price_group_block(schedule_date, block),
                self.supply_repository.get_plants_list_block(schedule_date, block),
                self.supply_repository.repository.aggregate_async(self.supply_repository.price_group, price_group_definitions_pipeline)
            ]

            # Run tasks concurrently and await their results
            results = await asyncio.gather(*tasks)

            # Unpack results
            category_payload, technology_payload, price_group_payload, plants_payload, price_range_result = results

            # Structure the final response
            price_group_response = {
                "price_group": price_group_payload,
                "price_range": {"result": price_range_result}
            }

            return {
                "schedule_date": schedule_date,
                "time_block": block,
                "category": category_payload,
                "technology": technology_payload,
                "price_group": price_group_response,
                "plants": plants_payload,
            }

        except Exception as e:
            logger.exception(f"[SupplyService] Error processing supply block bundle data - schedule_date: {schedule_date}, error: {str(e)}")
            raise

    async def get_supply_by_technology_block(self, schedule_date: Optional[str] = None, time_block: Optional[int] = None) -> Dict[str, Any]:
        """Get single-block supply grouped by technology."""
        try:
            schedule_date = normalize_schedule_date(schedule_date or "")
            if not validate_schedule_date(schedule_date):
                return {}
            block = time_block if time_block is not None else self._current_time_block_ist()
            return await self.supply_repository.get_supply_by_technology_block(schedule_date, block)
        except Exception as e:
            logger.error(f"[SupplyService] Error processing supply by technology block - schedule_date: {schedule_date}, error: {str(e)}")
            raise

    async def get_supply_by_price_group_block(self, schedule_date: Optional[str] = None, time_block: Optional[int] = None) -> Dict[str, Any]:
        """Get single-block supply grouped by price group."""
        try:
            schedule_date = normalize_schedule_date(schedule_date or "")
            if not validate_schedule_date(schedule_date):
                return {}
            block = time_block if time_block is not None else self._current_time_block_ist()
            
            price_group_task = self.supply_repository.get_supply_by_price_group_block(schedule_date, block)
            price_range_task = self.supply_repository.get_price_group_definitions()

            price_group_payload, price_range_payload = await asyncio.gather(price_group_task, price_range_task)

            return {
                "price_group": price_group_payload,
                "price_range": price_range_payload
            }
        except Exception as e:
            logger.error(f"[SupplyService] Error processing supply by price group block - schedule_date: {schedule_date}, error: {str(e)}")
            raise

    async def get_supply_raw(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        include_fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        skip: int = 0,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Delegates to the repository to fetch raw supply data with dynamic filters."""
        try:
            result = await self.supply_repository.get_supply_raw(
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                filters=filters,
                include_fields=include_fields,
                exclude_fields=exclude_fields,
                sort=sort,
                skip=skip,
                limit=limit,
            )
            return result
        except Exception as e:
            logger.error(f"[SupplyService] Error processing raw supply data: {str(e)}")
            raise


# Global instance
supply_service = SupplyService()
