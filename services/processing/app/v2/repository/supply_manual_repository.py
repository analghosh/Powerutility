"""
Supply Manual Repository for v2 Processing
Manages dc_sg collection operations for manual supply queries and updates.
"""

from typing import List, Dict, Optional, Any
from .base_repository import GenericRepository
from ...core.config import settings
from libs.enum.updated_flag import UpdatedFlag
import logging
from datetime import datetime, timedelta
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

class SupplyManualRepository(GenericRepository):
    """
    Repository for dc_sg collection using v2 pattern.
    Provides query/update operations for manual supply management.
    """
    
    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_DC_SG_COLLECTION if hasattr(settings, 'MONGODB_DC_SG_COLLECTION') else "dc_sg"
        logger.info(f"SupplyManualRepository initialized with collection: {self.collection_name}")

    @property
    def collection(self):
        """Get the collection object for direct access"""
        return self.db[self.collection_name]

    def _find_sync(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Execute find operation synchronously"""
        try:
            result = self.collection.find(query)
            return list(result) if result else []
        except Exception as e:
            logger.error(f"Failed to execute find: {str(e)}")
            return []

    async def find_async(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Execute find operation asynchronously"""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, self._find_sync, query)

    def get_manual_supply(
        self,
        plant_id: Optional[str] = None,
        dc_sg: str = "DC",
        process_type: str = "today",
        updated_flag: str = "OLD",
        time_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get supply data from dc_sg collection with filters.
        
        Args:
            plant_id: Optional plant ID filter. If None, return all plants.
            dc_sg: DC or SG type. Defaults to "DC".
            process_type: "today" or "tomorrow". Defaults to "today".
            updated_flag: Filter by updated_flag. Defaults to "OLD".
            time_block: Optional time block filter. If None, return all blocks.
        
        Returns:
            List of dc_sg records matching the filters.
        """
        try:
            query = {
                "dc_sg": dc_sg,
                "process_type": process_type,
                "updated_flag": updated_flag
            }
            
            if plant_id:
                query["plant_id"] = plant_id
            
            if time_block is not None:
                query["time_block"] = time_block
            
            logger.info(f"Querying dc_sg with filters: {query}")
            result = self._find_sync(query)
            logger.info(f"Found {len(result)} records in dc_sg")
            return result
            
        except Exception as e:
            logger.error(f"Failed to get manual supply: {str(e)}")
            return []

    async def get_manual_supply_async(
        self,
        plant_id: Optional[str] = None,
        dc_sg: str = "DC",
        process_type: str = "today",
        updated_flag: str = "OLD",
        time_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Async version of get_manual_supply"""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(
                executor, 
                lambda: self.get_manual_supply(plant_id, dc_sg, process_type, updated_flag, time_block)
            )

    async def update_manual_supply_to_zero(
        self,
        plant_id: str,
        dc_sg: str,
        process_type: str,
        time_block: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Update matching dc_sg records to zero volume with REPLACED flag.
        
        Args:
            plant_id: Plant ID to update (required).
            dc_sg: DC or SG type (required).
            process_type: "today" or "tomorrow" (required).
            time_block: Optional time block filter. If None, updates all blocks.
        
        Returns:
            Dictionary with update results (matched_count, modified_count).
        """
        try:
            query = {
                "plant_id": plant_id,
                "dc_sg": dc_sg,
                "process_type": process_type
            }
            
            if time_block is not None:
                query["time_block"] = time_block
            
            update = {
                "$set": {
                    "volume": 0,
                    "updated_flag": UpdatedFlag.REPLACED.value,
                    "updated_at": datetime.now()
                }
            }
            
            logger.info(f"Updating dc_sg records with query: {query}")
            result = await self.collection.update_many(query, update)
            
            update_result = {
                "matched_count": result.matched_count,
                "modified_count": result.modified_count
            }
            logger.info(f"Updated {result.modified_count} records in dc_sg")
            return update_result
            
        except Exception as e:
            logger.error(f"Failed to update manual supply: {str(e)}")
            return {"matched_count": 0, "modified_count": 0, "error": str(e)}

    def get_records_for_sync(
        self,
        plant_id: str,
        dc_sg: str,
        process_type: str,
        time_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get dc_sg records for syncing to supply_serve.
        Returns records with their scheduled_at dates for deletion/insertion.
        
        Args:
            plant_id: Plant ID filter.
            dc_sg: DC or SG type.
            process_type: "today" or "tomorrow".
            time_block: Optional time block filter.
        
        Returns:
            List of records for sync operations.
        """
        try:
            query = {
                "plant_id": plant_id,
                "dc_sg": dc_sg,
                "process_type": process_type
            }
            
            if time_block is not None:
                query["time_block"] = time_block
            
            return self._find_sync(query)
            
        except Exception as e:
            logger.error(f"Failed to get records for sync: {str(e)}")
            return []


# Singleton instance
supply_manual_repository = SupplyManualRepository()
