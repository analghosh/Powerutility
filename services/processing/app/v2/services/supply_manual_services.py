"""
Supply Manual V2 Service
Handles business logic for manual supply operations on dc_sg and syncs to supply_serve.
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from ..repository.supply_manual_repository import supply_manual_repository
from ..repository.supply_serve_repository import SupplyServeRepository
from ...core.config import settings

logger = logging.getLogger(__name__)


class SupplyManualService:
    """
    Service layer for Manual Supply V2 operations.
    Handles GET/PUT on dc_sg and syncs changes to supply_serve.
    """

    def __init__(self):
        self.dc_sg_repository = supply_manual_repository
        self.supply_serve_repository = SupplyServeRepository()

    async def get_manual_supply(
        self,
        plant_id: Optional[str] = None,
        dc_sg: str = "DC",
        process_type: str = "today",
        updated_flag: str = "OLD",
        time_block: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get supply data from dc_sg collection with filters.
        
        Args:
            plant_id: Optional plant ID filter.
            dc_sg: DC or SG type. Defaults to "DC".
            process_type: "today" or "tomorrow". Defaults to "today".
            updated_flag: Filter by updated_flag. Defaults to "OLD".
            time_block: Optional time block filter.
        
        Returns:
            Dictionary with data and metadata.
        """
        try:
            records = await self.dc_sg_repository.get_manual_supply(
                plant_id=plant_id,
                dc_sg=dc_sg,
                process_type=process_type,
                updated_flag=updated_flag,
                time_block=time_block
            )
            
            # Serialize ObjectId to string for JSON response
            for record in records:
                if "_id" in record:
                    record["_id"] = str(record["_id"])
                if "scheduled_at" in record and isinstance(record["scheduled_at"], datetime):
                    record["scheduled_at"] = record["scheduled_at"].isoformat()
            
            return {
                "records": records,
                "count": len(records),
                "filters_applied": {
                    "plant_id": plant_id,
                    "dc_sg": dc_sg,
                    "process_type": process_type,
                    "updated_flag": updated_flag,
                    "time_block": time_block
                }
            }
            
        except Exception as e:
            logger.error(f"Error in get_manual_supply: {str(e)}")
            raise

    async def update_manual_supply(
        self,
        plant_id: str,
        dc_sg: str,
        process_type: str,
        time_block: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Update dc_sg records to volume=0, flag=REPLACED, then sync to supply_serve.
        
        Args:
            plant_id: Plant ID to update (required).
            dc_sg: DC or SG type (required).
            process_type: "today" or "tomorrow" (required).
            time_block: Optional time block filter.
        
        Returns:
            Dictionary with update results and sync status.
        """
        try:
            # Step 1: Get records to sync BEFORE update (need scheduled_at dates)
            records_to_sync = await self.dc_sg_repository.get_records_for_sync(
                plant_id=plant_id,
                dc_sg=dc_sg,
                process_type=process_type,
                time_block=time_block
            )
            
            if not records_to_sync:
                logger.warning(f"No records found for update: plant_id={plant_id}, dc_sg={dc_sg}, process_type={process_type}")
                return {
                    "dc_sg_update": {"matched_count": 0, "modified_count": 0},
                    "supply_serve_sync": {"deleted_count": 0, "inserted_count": 0},
                    "message": "No matching records found"
                }
            
            # Step 2: Update dc_sg records
            dc_sg_result = await self.dc_sg_repository.update_manual_supply_to_zero(
                plant_id=plant_id,
                dc_sg=dc_sg,
                process_type=process_type,
                time_block=time_block
            )
            
            # Step 3: Sync to supply_serve (Delete + Insert)
            sync_result = await self._sync_to_supply_serve(records_to_sync)
            
            return {
                "dc_sg_update": dc_sg_result,
                "supply_serve_sync": sync_result,
                "message": f"Updated {dc_sg_result.get('modified_count', 0)} dc_sg records and synced {sync_result.get('inserted_count', 0)} to supply_serve"
            }
            
        except Exception as e:
            logger.error(f"Error in update_manual_supply: {str(e)}")
            raise

    async def _sync_to_supply_serve(
        self,
        records: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Sync updated records to supply_serve using delete + insert strategy.
        
        Args:
            records: List of dc_sg records to sync.
        
        Returns:
            Dictionary with sync results.
        """
        deleted_count = 0
        inserted_count = 0
        errors = []
        
        try:
            supply_serve_collection = self.supply_serve_repository.collection
            
            for record in records:
                scheduled_at = record.get("scheduled_at")
                time_block = record.get("time_block")
                plant_id = record.get("plant_id")
                dc_sg = record.get("dc_sg")
                
                if not all([scheduled_at, time_block, plant_id, dc_sg]):
                    errors.append(f"Missing required fields in record: {record.get('_id')}")
                    continue
                
                # Construct unique key for supply_serve
                delete_query = {
                    "scheduled_at": scheduled_at,
                    "time_block": time_block,
                    "plant_id": plant_id,
                    "dc_sg": dc_sg
                }
                
                # Step 1: Delete existing record
                delete_result = await supply_serve_collection.delete_one(delete_query)
                deleted_count += delete_result.deleted_count
                
                # Step 2: Insert new zero-volume record
                new_record = {
                    "scheduled_at": scheduled_at,
                    "time_block": time_block,
                    "plant_id": plant_id,
                    "dc_sg": dc_sg,
                    "volume": 0,
                    "updated_flag": "REPLACED",
                    "inserted_at": datetime.now(),
                    "updated_at": datetime.now(),
                    "timestamp": datetime.now(),
                    # Copy other fields from original record
                    "fc_price": record.get("fc_price", 0),
                    "px_price": record.get("px_price", 0),
                    "vc_price": record.get("vc_price", 0),
                    "price_group": record.get("price_group"),
                    "dam_tm": record.get("dam_tm", 0),
                    "rtm_tm": record.get("rtm_tm", 0),
                    "multiplier": record.get("multiplier", ""),
                    "ramping": record.get("ramping", ""),
                    "source": "manual_update",
                    "revision_no": record.get("revision_no", 0),
                    "system_revision_number": record.get("system_revision_number", 1) + 1
                }
                
                insert_result = await supply_serve_collection.insert_one(new_record)
                if insert_result.inserted_id:
                    inserted_count += 1
            
            return {
                "deleted_count": deleted_count,
                "inserted_count": inserted_count,
                "errors": errors if errors else None
            }
            
        except Exception as e:
            logger.error(f"Error syncing to supply_serve: {str(e)}")
            return {
                "deleted_count": deleted_count,
                "inserted_count": inserted_count,
                "error": str(e)
            }


# Global instance
supply_manual_service = SupplyManualService()
