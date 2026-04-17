from typing import List, Dict, Optional, Any
from .base_repository import GenericRepository
import logging 
from datetime import date, datetime, timedelta
import asyncio
from concurrent.futures import ThreadPoolExecutor

from ...core.config import settings

logger = logging.getLogger(__name__)

class SchedulingRequisitionCalculationRepository(GenericRepository):
    """
    Repository for scheduling_requisition_calculation_table using v2 pattern.
    Uses standard field names:
    - scheduling_req_type (instead of scheduling_requisition_type)
    - Supports backward compatibility with legacy field names
    """
    
    def __init__(self, collection_name: str = "scheduling_requisition_calculation_table"):
        super().__init__()
        self.collection_name = collection_name
        logger.info(f"SchedulingRequisitionCalculationRepository initialized with collection: {collection_name}")
    
    async def _get_rtm_all_plant_data(self) -> List[Dict[str, Any]]:
        """
        Get all RTM plant data for scheduling.
        Maps legacy field names to standard field names in response.
        Returns data for yesterday's date (to match V1 behavior).
        """
        # Get yesterday's date in Asia/Kolkata timezone
        from zoneinfo import ZoneInfo
        today_kolkata = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        yesterday_kolkata = today_kolkata - timedelta(days=1)
        yesterday_start = datetime.combine(yesterday_kolkata, datetime.min.time()).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        today_start = yesterday_start + timedelta(days=2)
        
        logger.info(f"Getting RTM plant data for scheduling for yesterday: {yesterday_kolkata.strftime('%Y-%m-%d')}")
        try:
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {"$gt": yesterday_start, "$lte": today_start}
                    }
                },
                {
                    "$lookup": {
                        "from": "plant_master",
                        "let": {
                            "plantKey": "$plant_id",
                        },
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$eq": ["$plant_id", "$$plantKey"]
                                    }
                                }
                            },
                            {
                                "$project": {
                                    "_id": 0,
                                    "px_price": 1,
                                    "plant_id": 1,
                                    "category": 1
                                }
                            }
                        ],
                        "as": "rtm_data"
                    }
                },
                {"$unwind": {"path": "$rtm_data", "preserveNullAndEmptyArrays": True}},
                {
                    "$sort": {
                        "rtm_data.px_price": -1,
                        "price": -1,  # Use "price" not "market_clearing_price"
                        "plant_id": -1  # Match v1: "key" field sorting
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "schedule_date_timestamp": "$scheduled_at_timestamp",
                            "time_block": "$time_block",
                            "plant_id": "$plant_id"  # Group by plant_id as well to aggregate duplicates
                        },
                        # Aggregate plant-level fields
                        "plant_name": {"$first": "$plant_name"},
                        "key": {"$first": "$plant_id"},
                        "schedule_date": {
                            "$first": {
                                "$dateToString": {
                                    "format": "%Y-%m-%d",
                                    "date": "$scheduled_at"
                                }
                            }
                        },
                        "category": {"$first": "$rtm_data.category"},
                        "px_price": {"$first": {"$ifNull": ["$rtm_data.px_price", 0.0]}},
                        "remaining_volume": {"$first": "$remaining_volume_mw"},
                        "scheduling_requisition_type": {"$first": "$scheduling_req_type"},
                        "price": {"$first": {"$ifNull": ["$price", 0.0]}},
                        "RMP": {"$first": {"$ifNull": ["$plant_rmp_value", 0.0]}},
                        "plant_TM_value": {"$first": {"$ifNull": ["$plant_tm_value", 0.0]}},
                        # Sum the volumes for this plant in this block
                        "genrated_sg_volume": {"$sum": "$generated_sg_volume_mw"},
                        "Final_plant_RMP_volume": {"$sum": "$final_rmp_volume_mw"},
                        "actual_plant_volume": {"$sum": "$actual_plant_volume_mw"},
                        "schedule_date_timestamp": {"$first": "$scheduled_at_timestamp"},
                        
                        # Block-level fields (take first as they are same for the block)
                        "time_block": {"$first": "$time_block"},
                        "dc_sg_type": {"$first": "$dc_sg_type"},
                        "schedule_date_block": {  # Renamed to avoid conflict with plant-level schedule_date
                            "$first": {
                                "$dateToString": {
                                    "format": "%Y-%m-%d",
                                    "date": "$scheduled_at"
                                }
                            }
                        },
                        "schedule_date_timestamp_block": {"$first": "$scheduled_at_timestamp"},
                        "totalAvailability": {"$first": "$total_supply_available_mw"},
                        "demand_forecast": {"$first": "$demand_forecast_mw"},
                        "surplus_deficit": {"$first": "$net_surplus_deficit_mw"},
                        "rtm_forecasted_price": {"$first": "$rtm_forecasted_price"},
                        "surplus_deficit_updated": {"$first": "$updated_surplus_deficit_mw"},
                        "total_availablity_updated": {"$first": "$updated_total_supply_mw"},
                        "genrated_sg_volume_block": {"$first": "$generated_sg_volume_mw"} # Keep block level for reference, though we will re-sum later
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "schedule_date_timestamp": "$_id.schedule_date_timestamp",
                            "time_block": "$_id.time_block"
                        },
                        "result": {
                            "$push": {
                                "plant_name": "$plant_name",
                                "key": "$key",
                                "schedule_date": "$schedule_date",
                                "category": "$category",
                                "px_price": "$px_price",
                                "remaining_volume": "$remaining_volume",
                                "scheduling_requisition_type": "$scheduling_requisition_type",
                                "price": "$price",
                                "RMP": "$RMP",
                                "plant_TM_value": "$plant_TM_value",
                                "genrated_sg_volume": "$genrated_sg_volume",
                                "Final_plant_RMP_volume": "$Final_plant_RMP_volume",
                                "actual_plant_volume": "$actual_plant_volume",
                                "actual_plant_volume_DC": "$actual_dc_volume",
                                "schedule_date_timestamp": "$schedule_date_timestamp"
                            }
                        },
                        "time_block": {"$first": "$time_block"},
                        "dc_sg_type": {"$first": "$dc_sg_type"},
                        "schedule_date": {"$first": "$schedule_date_block"},
                        "schedule_date_timestamp": {"$first": "$schedule_date_timestamp_block"},
                        "totalAvailability": {"$first": "$totalAvailability"},
                        "demand_forecast": {"$first": "$demand_forecast"},
                        "surplus_deficit": {"$first": "$surplus_deficit"},
                        "rtm_forecasted_price": {"$first": "$rtm_forecasted_price"},
                        "surplus_deficit_updated": {"$first": "$surplus_deficit_updated"},
                        "total_availablity_updated": {"$first": "$total_availablity_updated"},
                        # Sum of all plants' generated volume for the block
                        "genrated_sg_volume": {"$sum": "$genrated_sg_volume"} 
                    }
                },
                {
                    "$addFields": {
                        "surplus_deficit_updated": {
                            "$subtract": ["$genrated_sg_volume", "$demand_forecast"]
                        }
                    }
                },
                {
                    "$project": {
                        "result": 1,
                        "time_block": 1,
                        "full_time": None,
                        "start_time": None,
                        "end_time": None,
                        "dc_sg_type": 1,
                        "schedule_date": 1,  # Use "schedule_date" for v1 compatibility
                        "schedule_date_timestamp": 1,
                        "totalAvailability": 1,
                        "demand_forecast": 1,
                        "surplus_deficit": 1,
                        "rtm_forecasted_price": 1,
                        "surplus_deficit_updated": 1,
                        "total_availablity_updated": 1,
                        "genrated_sg_volume": 1
                    }
                },
                {
                    "$sort": {
                        "schedule_date": 1.0,  # Use "schedule_date" for v1 compatibility
                        "time_block": 1.0
                    }
                }
            ]

            return await self.aggregate(self.collection_name, pipeline)
        except Exception as e:
            logger.error(f"Failed to get RTM plant data for scheduling: {str(e)}")
            return []

    async def getRTMAllPlantDataForScheduling(self) -> List[Dict[str, Any]]:
        """
        Get all RTM plant data for scheduling.
        Returns data with standardized field names while supporting legacy field names.
        """
        return await self._get_rtm_all_plant_data()

    async def get_rtm_all_plant_data_for_scheduling(self) -> List[Dict[str, Any]]:
        """
        Get all RTM plant data for scheduling (async version).
        Returns data with standardized field names while supporting legacy field names.
        """
        return await self.getRTMAllPlantDataForScheduling()

    def create(self, record_data: Dict[str, Any]) -> str:
        """
        Create a new scheduling requisition calculation record (backward compatibility alias).
        Handles both standard and legacy field names.
        
        Args:
            record_data: Record data dictionary
            
        Returns:
            Inserted document ID
        """
        return self.create_scheduling_record(record_data)

    def create_scheduling_record(self, record_data: Dict[str, Any]) -> str:
        """
        Create a new scheduling requisition calculation record.
        Handles both standard and legacy field names.
        
        Args:
            record_data: Record data dictionary
            
        Returns:
            Inserted document ID
        """
        try:
            # Ensure scheduling_req_type exists (use scheduling_requisition_type if provided)
            if "scheduling_req_type" not in record_data and "scheduling_requisition_type" in record_data:
                record_data["scheduling_req_type"] = record_data["scheduling_requisition_type"]
            elif "scheduling_requisition_type" not in record_data and "scheduling_req_type" in record_data:
                record_data["scheduling_requisition_type"] = record_data["scheduling_req_type"]
            
            result = self.insert_one(self.collection_name, record_data)
            logger.info(f"[SchedulingRequisitionCalculationRepository] Successfully created scheduling record with ID: {result.inserted_id}")
            return str(result.inserted_id)
        except Exception as e:
            logger.exception(f"[SchedulingRequisitionCalculationRepository] Failed to create scheduling record: {str(e)}")
            raise

    async def create_scheduling_record_async(self, record_data: Dict[str, Any]) -> str:
        """
        Create a new scheduling requisition calculation record asynchronously.
        
        Args:
            record_data: Record data dictionary
            
        Returns:
            Inserted document ID
        """
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(
                executor,
                self.create_scheduling_record,
                record_data
            )

    def create_many_scheduling_records(self, records: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Create multiple scheduling requisition calculation records.
        
        Args:
            records: List of record data dictionaries
            
        Returns:
            Dictionary with inserted_count
        """
        try:
            # Ensure scheduling_req_type exists for all records and remove old field names
            old_to_new_mapping = {
                "schedule_date": "scheduled_at",
                "schedule_date_timestamp": "scheduled_at_timestamp",
                "schedule_date_Int": "scheduled_at_int",
                "demand_forecast": "demand_forecast_mw",
                "total_supply_availablity": "total_supply_available_mw",
                "surplus_deficit": "net_surplus_deficit_mw",
                "surplus_deficit_updated": "updated_surplus_deficit_mw",
                "total_availablity_updated": "updated_total_supply_mw",
                "remaining_volume": "remaining_volume_mw",
                "price": "price",  # Keep as "price" not "market_clearing_price"
                "rtm_forecasted_price": "rtm_forecasted_price",  # Keep as "rtm_forecasted_price" not "forecasted_rtm_price"
                "plant_RMP_value": "plant_rmp_value",
                "plant_TM_value": "plant_tm_value",
                "plant_SEZ_value": "plant_sez_value",
                "plant_MLTP_value": "plant_mltp_value",
                "up_down_RMP_volume": "rmp_adjusted_volume_mw",
                "Final_plant_RMP_volume": "final_rmp_volume_mw",
                "genrated_sg_volume": "generated_sg_volume_mw",
                "actual_plant_volume": "actual_plant_volume_mw",
                "actual_plant_volume_DC": "actual_dc_volume_mw",
                "total_RMP_volume": "total_rmp_volume_mw",
                "category": "category",
                "key": "plant_id",
                "scheduling_requisition_type": "scheduling_req_type",
            }
            
            for record in records:
                # Convert old field names to new ones if they exist
                if "scheduling_req_type" not in record and "scheduling_requisition_type" in record:
                    record["scheduling_req_type"] = record["scheduling_requisition_type"]
                    record.pop("scheduling_requisition_type", None)
                
                # Remove old field names if new ones exist
                for old_field, new_field in old_to_new_mapping.items():
                    if old_field in record and new_field in record:
                        record.pop(old_field, None)
            
            result = self.insert_many(self.collection_name, records)
            logger.info(f"[SchedulingRequisitionCalculationRepository] Successfully created {len(result.inserted_ids)} scheduling records")
            return {"inserted_count": len(result.inserted_ids)}
        except Exception as e:
            logger.exception(f"[SchedulingRequisitionCalculationRepository] Failed to create scheduling records: {str(e)}")
            raise

    async def create_many_scheduling_records_async(self, records: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Create multiple scheduling requisition calculation records asynchronously.
        
        Args:
            records: List of record data dictionaries
            
        Returns:
            Dictionary with inserted_count
        """
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(
                executor,
                self.create_many_scheduling_records,
                records
            )

    def get_by_schedule_date_and_block(
        self,
        schedule_date: str,
        time_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get scheduling records by schedule date and optionally by time block.
        Returns data with standard field names.
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format (matches scheduled_at datetime)
            time_block: Optional time block number
            
        Returns:
            List of scheduling records with standard field names
        """
        try:
            # Convert date string to datetime for scheduled_at matching (consistent with v2 pattern)
            target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
            end_date = target_date + timedelta(days=1)
            
            # Match on scheduled_at (datetime object)
            filter_query: Dict[str, Any] = {
                "scheduled_at": {"$gte": target_date, "$lt": end_date}
            }
            if time_block is not None:
                filter_query["time_block"] = time_block
            
            pipeline = [
                {"$match": filter_query},
                {
                    "$project": {
                        "_id": 0,
                        # Date fields - use new standard field names
                        "scheduled_at": 1,
                        "scheduled_at_timestamp": 1,
                        "scheduled_at_int": 1,
                        # Basic fields
                        "time_block": 1,
                        "dc_sg_type": 1,
                        "session_number": 1,
                        "start_time": 1,
                        "end_time": 1,
                        "full_time": 1,
                        # Forecast and supply fields - use new standard field names
                        "demand_forecast_mw": 1,
                        "total_supply_available_mw": 1,
                        "net_surplus_deficit_mw": 1,
                        "updated_surplus_deficit_mw": 1,
                        "updated_total_supply_mw": 1,
                           # Price fields - use "price" and "rtm_forecasted_price" (not renamed)
                           "price": 1,
                           "rtm_forecasted_price": 1,
                        # Volume fields - use new standard field names
                        "remaining_volume_mw": 1,
                        # Plant value fields - use new standard field names
                        "plant_rmp_value": 1,
                        "plant_tm_value": 1,
                        "plant_sez_value": 1,
                        "plant_mltp_value": 1,
                        # RMP volume fields - use new standard field names
                        "rmp_adjusted_volume_mw": 1,
                        "final_rmp_volume_mw": 1,
                        "generated_sg_volume_mw": 1,
                        "actual_plant_volume_mw": 1,
                        "actual_dc_volume_mw": 1,
                        "total_rmp_volume_mw": 1,
                        # Category and identifier fields - use new standard field names
                        "category": 1,
                        "plant_id": 1,
                        "plant_name": 1,
                        "display_category": 1,
                        "dispatch_type": 1,
                        "revision_no_SG": 1,
                        "revision_no_DC": 1,
                        "plant_min_rum_volume": 1,
                        # Scheduling type - use new standard field name
                        "scheduling_req_type": 1,
                        # Timestamp fields
                        "created_at": 1,
                        "updated_at": 1
                    }
                },
                {"$sort": {"time_block": 1}}
            ]
            
            return self.aggregate(self.collection_name, pipeline)
        except Exception as e:
            logger.error(f"Failed to get scheduling records: {str(e)}")
            return []
    
    def delete_all_for_schedule_date(self, schedule_date: str, audit_logger=None, user_context: Optional[Dict[str, Any]] = None) -> int:
        """
        Delete all scheduling records for a specific schedule_date.
        Used to clear all existing records before inserting new calculations for a date.
        
        Args:
            schedule_date: Schedule date string in YYYY-MM-DD format
            audit_logger: Optional audit logger for logging deletion operations
            user_context: Optional user context dictionary
        
        Returns:
            int: Number of records deleted
        """
        try:
            from zoneinfo import ZoneInfo
            
            # Parse schedule_date string to datetime
            schedule_date_dt = datetime.strptime(schedule_date, "%Y-%m-%d")
            
            # Create date range for the target date in Asia/Kolkata timezone
            target_date = datetime.combine(schedule_date_dt.date(), datetime.min.time()).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            end_date = target_date + timedelta(days=1)
            
            logger.info(f"[SchedulingRequisitionCalculationRepository] Deleting all records for schedule_date={schedule_date} (scheduled_at range: {target_date} to {end_date})")
            
            # Access the MongoDB collection
            collection = self.db[self.collection_name]
            
            # Count records before deletion for logging
            count_before = collection.count_documents({
                "scheduled_at": {"$gte": target_date, "$lt": end_date}
            })
            
            logger.info(f"[SchedulingRequisitionCalculationRepository] Found {count_before} record(s) to delete for schedule_date={schedule_date}")
            
            # Delete all records for this date range
            result = collection.delete_many({
                "scheduled_at": {"$gte": target_date, "$lt": end_date}
            })
            
            deleted_count = result.deleted_count
            logger.info(f"[SchedulingRequisitionCalculationRepository] Deleted {deleted_count} record(s) for schedule_date={schedule_date}")
            
            # Log audit information if audit_logger is provided
            if audit_logger:
                correlation_id = user_context.get("correlation_id", "unknown") if user_context else "unknown"
                user_id = user_context.get("user_id", "unknown") if user_context else "unknown"
                
                audit_logger.log_system_operation(
                    service="processing",
                    operation="rtm_scheduling_table_deletion_repository",
                    message=f"Repository deleted {deleted_count} scheduling calculation records for schedule_date: {schedule_date}",
                    correlation_id=correlation_id,
                    user_id=user_id,
                    details={
                        "schedule_date": schedule_date,
                        "deleted_count": deleted_count,
                        "scheduled_at_range": {
                            "from": target_date.isoformat(),
                            "to": end_date.isoformat()
                        }
                    }
                )
            
            return deleted_count
            
        except ValueError as e:
            logger.error(f"[SchedulingRequisitionCalculationRepository] Invalid date format for schedule_date={schedule_date}: {str(e)}")
            raise ValueError(f"Invalid schedule_date format. Expected YYYY-MM-DD, got: {schedule_date}")
        except Exception as e:
            logger.exception(f"[SchedulingRequisitionCalculationRepository] Failed to delete records for schedule_date={schedule_date}: {str(e)}")
            raise
