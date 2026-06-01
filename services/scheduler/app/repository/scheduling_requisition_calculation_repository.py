"""
Scheduling Requisition Calculation Repository for Scheduler Service
Handles scheduling_requisition_calculation_table collection access
"""
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime

from services.processing.app.v2.repository.base_repository import GenericRepository
from ..core.config import settings

logger = logging.getLogger(__name__)


class SchedulingRequisitionCalculationRepository(GenericRepository):
    """
    Repository for scheduling_requisition_calculation_table collection.
    Handles RTM scheduling calculation records.
    """

    def __init__(self, collection_name: str = "scheduling_requisition_calculation_table"):
        super().__init__()
        self.collection_name = collection_name
        logger.info(f"SchedulingRequisitionCalculationRepository initialized with collection: {self.collection_name}")

    def create(self, record: Dict[str, Any]) -> Any:
        """
        Insert a single scheduling calculation record.
        
        Args:
            record: Scheduling calculation record to insert
            
        Returns:
            Insert result
        """
        try:
            result = self.insert_one(self.collection_name, record)
            return result
        except Exception as e:
            logger.error(f"Failed to insert scheduling calculation record: {str(e)}")
            raise

    def bulk_create(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Insert multiple scheduling calculation records.
        
        Args:
            records: List of scheduling calculation records
            
        Returns:
            Dictionary with insert results
        """
        try:
            if not records:
                return {"inserted_count": 0, "error_count": 0}
            
            result = self.insert_many(self.collection_name, records)
            return {
                "inserted_count": len(result.inserted_ids) if hasattr(result, 'inserted_ids') else 0,
                "error_count": 0
            }
        except Exception as e:
            logger.error(f"Failed to bulk insert scheduling calculation records: {str(e)}")
            return {"inserted_count": 0, "error_count": len(records)}

    def delete_all_for_schedule_date(
        self,
        schedule_date: str,
        audit_logger=None,
        user_context: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Delete all scheduling calculation records for a specific schedule date.
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            audit_logger: Optional audit logger
            user_context: Optional user context for audit logging
            
        Returns:
            Number of deleted records
        """
        try:
            # Convert schedule_date string to datetime for matching
            schedule_date_dt = datetime.strptime(schedule_date, "%Y-%m-%d")
            
            # Delete records matching the schedule date
            # Handle both datetime and string formats in database
            filter_query = {
                "$or": [
                    {"scheduled_at": schedule_date_dt},
                    {"schedule_date": schedule_date}
                ]
            }
            
            result = self.delete_many(self.collection_name, filter_query)
            deleted_count = result.deleted_count if hasattr(result, 'deleted_count') else 0
            
            logger.info(f"Deleted {deleted_count} scheduling calculation records for schedule_date={schedule_date}")
            return deleted_count
        except Exception as e:
            logger.error(f"Failed to delete scheduling calculation records for {schedule_date}: {str(e)}")
            return 0

    def delete_blocks_from(self, schedule_date_dt: datetime, from_block: int) -> int:
        """
        Delete scheduling records for a schedule date where time_block >= from_block.
        Preserves records for past blocks (time_block < from_block).
        
        Args:
            schedule_date_dt: Schedule date as datetime object
            from_block: The starting block number (1-indexed). Records with 
                        time_block >= from_block will be deleted.
            
        Returns:
            int: Number of deleted records
        """
        try:
            collection = self.db[self.collection_name]
            
            filter_query = {
                "scheduled_at": schedule_date_dt,
                "time_block": {"$gte": from_block}
            }
            
            result = collection.delete_many(filter_query)
            deleted_count = result.deleted_count
            
            logger.info(f"[SchedulingRequisitionCalculationRepository] Deleted {deleted_count} records for blocks >= {from_block} on {schedule_date_dt.date()}")
            return deleted_count
        except Exception as e:
            if "MongoClient after close" in str(e):
                logger.warning("[SchedulingRequisitionCalculationRepository] MongoClient was closed, refreshing connection and retrying delete...")
                self._client = None
                self._db = None
                try:
                    collection = self.db[self.collection_name]
                    filter_query = {
                        "scheduled_at": schedule_date_dt,
                        "time_block": {"$gte": from_block}
                    }
                    result = collection.delete_many(filter_query)
                    deleted_count = result.deleted_count
                    logger.info(f"[SchedulingRequisitionCalculationRepository] Deleted {deleted_count} records for blocks >= {from_block} on {schedule_date_dt.date()} (after reconnect)")
                    return deleted_count
                except Exception as retry_err:
                    logger.error(f"[SchedulingRequisitionCalculationRepository] Failed to delete blocks from {from_block} after reconnect: {str(retry_err)}")
                    return 0
            logger.error(f"[SchedulingRequisitionCalculationRepository] Failed to delete blocks from {from_block}: {str(e)}")
            return 0

    def getRTMAllPlantDataForScheduling(self) -> List[Dict[str, Any]]:
        """
        Get all RTM scheduling calculation data.
        Returns data grouped by time blocks with plant results.
        
        Returns:
            List of scheduling records grouped by time block
        """
        try:
            # Aggregation pipeline to group by time_block and organize plant data
            pipeline = [
                {"$sort": {"time_block": 1, "plant_name": 1}},
                {
                    "$group": {
                        "_id": "$time_block",
                        "time_block": {"$first": "$time_block"},
                        "full_time": {"$first": "$full_time"},
                        "start_time": {"$first": "$start_time"},
                        "end_time": {"$first": "$end_time"},
                        "dc_sg_type": {"$first": "$dc_sg_type"},
                        "schedule_date": {"$first": {
                            "$cond": {
                                "if": {"$eq": [{"$type": "$scheduled_at"}, "date"]},
                                "then": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                                "else": "$schedule_date"
                            }
                        }},
                        "schedule_date_timestamp": {"$first": "$scheduled_at_timestamp"},
                        "totalAvailability": {"$first": "$total_supply_available_mw"},
                        "demand_forecast": {"$first": "$demand_forecast_mw"},
                        "surplus_deficit": {"$first": "$net_surplus_deficit_mw"},
                        "rtm_forecasted_price": {"$first": "$rtm_forecasted_price"},
                        "surplus_deficit_updated": {"$first": "$updated_surplus_deficit_mw"},
                        "total_availablity_updated": {"$first": "$updated_total_supply_mw"},
                        "result": {
                            "$push": {
                                "plant_name": "$plant_name",
                                "key": "$plant_id",
                                "schedule_date": {
                                    "$cond": {
                                        "if": {"$eq": [{"$type": "$scheduled_at"}, "date"]},
                                        "then": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                                        "else": "$schedule_date"
                                    }
                                },
                                "category": "$category",
                                "px_price": "$price",
                                "remaining_volume": "$remaining_volume_mw",
                                "scheduling_requisition_type": "$scheduling_req_type",
                                "price": "$price",
                                "RMP": "$plant_rmp_value",
                                "plant_TM_value": "$plant_tm_value",
                                "genrated_sg_volume": "$generated_sg_volume_mw",
                                "Final_plant_RMP_volume": "$final_rmp_volume_mw",
                                "actual_plant_volume": "$actual_plant_volume_mw",
                                "schedule_date_timestamp": "$scheduled_at_timestamp"
                            }
                        }
                    }
                },
                {"$sort": {"time_block": 1}}
            ]
            
            result = self.aggregate(self.collection_name, pipeline)
            logger.info(f"Retrieved {len(result)} time blocks with scheduling data")
            return result
        except Exception as e:
            logger.error(f"Failed to get RTM scheduling data: {str(e)}")
            return []

    def get_by_schedule_date(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Get all scheduling calculation records for a specific schedule date.
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            
        Returns:
            List of scheduling calculation records
        """
        try:
            schedule_date_dt = datetime.strptime(schedule_date, "%Y-%m-%d")
            
            filter_query = {
                "$or": [
                    {"scheduled_at": schedule_date_dt},
                    {"schedule_date": schedule_date}
                ]
            }
            
            result = self.get_all(self.collection_name, filter_query)
            logger.info(f"Retrieved {len(result)} scheduling calculation records for {schedule_date}")
            return result
        except Exception as e:
            logger.error(f"Failed to get scheduling records for {schedule_date}: {str(e)}")
            return []

    def truncate_collection(self, audit_logger=None, user_context: Optional[Dict[str, Any]] = None) -> int:
        """
        Truncate (delete all records from) the scheduling_requisition_calculation_table collection.
        Use with caution - this removes ALL data from the collection.
        
        Args:
            audit_logger: Optional audit logger for logging truncation operation
            user_context: Optional user context dictionary
        
        Returns:
            int: Number of records deleted
        """
        try:
            logger.warning(f"[SchedulingRequisitionCalculationRepository] TRUNCATING collection: {self.collection_name}")
            
            # Access the MongoDB collection
            collection = self.db[self.collection_name]
            
            # Count records before deletion for logging
            count_before = collection.count_documents({})
            
            logger.warning(f"[SchedulingRequisitionCalculationRepository] Found {count_before} total record(s) to delete from {self.collection_name}")
            
            # Delete all records from the collection
            result = collection.delete_many({})
            
            deleted_count = result.deleted_count
            logger.warning(f"[SchedulingRequisitionCalculationRepository] TRUNCATED {deleted_count} record(s) from {self.collection_name}")
            
            # Log audit information if audit_logger is provided
            if audit_logger:
                correlation_id = user_context.get("correlation_id", "unknown") if user_context else "unknown"
                user_id = user_context.get("user_id", "unknown") if user_context else "unknown"
                username = user_context.get("username", "system") if user_context else "system"
                
                audit_logger.log_system_operation(
                    service="processing",
                    operation="rtm_scheduling_table_truncate",
                    message=f"Collection {self.collection_name} truncated - deleted {deleted_count} records",
                    correlation_id=correlation_id,
                    user_id=user_id,
                    details={
                        "collection_name": self.collection_name,
                        "deleted_count": deleted_count,
                        "operation": "truncate_all",
                        "username": username
                    }
                )
            
            return deleted_count
            
        except Exception as e:
            logger.exception(f"[SchedulingRequisitionCalculationRepository] Failed to truncate collection {self.collection_name}: {str(e)}")
            raise

    async def truncate_collection_async(self, audit_logger=None, user_context: Optional[Dict[str, Any]] = None) -> int:
        """
        Truncate (delete all records from) the scheduling_requisition_calculation_table collection asynchronously.
        Use with caution - this removes ALL data from the collection.
        
        Args:
            audit_logger: Optional audit logger for logging truncation operation
            user_context: Optional user context dictionary
        
        Returns:
            int: Number of records deleted
        """
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(
                executor,
                audit_logger,
                user_context
            )

    def update_block_totals(self, schedule_date: str, time_block: int, total_supply_updated: float, surplus_deficit_updated: float) -> int:
        """
        Update total_availablity_updated and surplus_deficit_updated for all records in a time block.
        
        Args:
            schedule_date: Schedule date string
            time_block: Time block number
            total_supply_updated: New total supply value
            surplus_deficit_updated: New surplus/deficit value
            
        Returns:
            int: Number of records updated
        """
        try:
            # Handle both string and datetime formats for schedule_date
            schedule_date_dt = datetime.strptime(schedule_date, "%Y-%m-%d")
            
            filter_query = {
                "$or": [
                    {"scheduled_at": schedule_date_dt},
                    {"schedule_date": schedule_date}
                ],
                "time_block": time_block
            }
            
            update_query = {
                "$set": {
                    "total_availablity_updated": total_supply_updated,
                    "surplus_deficit_updated": surplus_deficit_updated
                }
            }
            
            # Use update_many directly from the collection
            collection = self.db[self.collection_name]
            result = collection.update_many(filter_query, update_query)
            
            modified_count = result.modified_count
            logger.info(f"Updated totals for block {time_block}: {modified_count} records")
            return modified_count
            
        except Exception as e:
            logger.error(f"Failed to update block totals for block {time_block}: {str(e)}")
            return 0
