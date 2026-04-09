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

    async def create(self, record: Dict[str, Any]) -> Any:
        """
        Insert a single scheduling calculation record.
        """
        try:
            result = await self.insert_one(self.collection_name, record)
            return result
        except Exception as e:
            logger.error(f"Failed to insert scheduling calculation record: {str(e)}")
            raise

    async def bulk_create(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Insert multiple scheduling calculation records.
        """
        try:
            if not records:
                return {"inserted_count": 0, "error_count": 0}

            result = await self.insert_many(self.collection_name, records)
            return {
                "inserted_count": len(result.inserted_ids) if hasattr(result, 'inserted_ids') else 0,
                "error_count": 0
            }
        except Exception as e:
            logger.error(f"Failed to bulk insert scheduling calculation records: {str(e)}")
            return {"inserted_count": 0, "error_count": len(records)}

    async def delete_all_for_schedule_date(
        self,
        schedule_date: str,
        audit_logger=None,
        user_context: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Delete all scheduling calculation records for a specific schedule date.
        """
        try:
            schedule_date_dt = datetime.strptime(schedule_date, "%Y-%m-%d")

            filter_query = {
                "$or": [
                    {"scheduled_at": schedule_date_dt},
                    {"schedule_date": schedule_date}
                ]
            }

            result = await self.delete_many(self.collection_name, filter_query)
            deleted_count = result.deleted_count if hasattr(result, 'deleted_count') else 0

            logger.info(f"Deleted {deleted_count} scheduling calculation records for schedule_date={schedule_date}")
            return deleted_count
        except Exception as e:
            logger.error(f"Failed to delete scheduling calculation records for {schedule_date}: {str(e)}")
            return 0

    async def delete_blocks_from(self, schedule_date_dt: datetime, from_block: int) -> int:
        """
        Delete scheduling records for a schedule date where time_block >= from_block.
        Preserves records for past blocks (time_block < from_block).
        """
        try:
            collection = self.db[self.collection_name]

            filter_query = {
                "scheduled_at": schedule_date_dt,
                "time_block": {"$gte": from_block}
            }

            result = await collection.delete_many(filter_query)
            deleted_count = result.deleted_count

            logger.info(f"[SchedulingRequisitionCalculationRepository] Deleted {deleted_count} records for blocks >= {from_block} on {schedule_date_dt.date()}")
            return deleted_count
        except Exception as e:
            logger.error(f"[SchedulingRequisitionCalculationRepository] Failed to delete blocks from {from_block}: {str(e)}")
            return 0

    async def getRTMAllPlantDataForScheduling(self) -> List[Dict[str, Any]]:
        """
        Get all RTM scheduling calculation data.
        Returns data grouped by time blocks with plant results.
        """
        try:
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

            cursor = self.aggregate(self.collection_name, pipeline)
            result = await cursor.to_list(length=None)
            logger.info(f"Retrieved {len(result)} time blocks with scheduling data")
            return result
        except Exception as e:
            logger.error(f"Failed to get RTM scheduling data: {str(e)}")
            return []

    async def get_by_schedule_date(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Get all scheduling calculation records for a specific schedule date.
        """
        try:
            schedule_date_dt = datetime.strptime(schedule_date, "%Y-%m-%d")

            filter_query = {
                "$or": [
                    {"scheduled_at": schedule_date_dt},
                    {"schedule_date": schedule_date}
                ]
            }

            result = await self.get_all(self.collection_name, filter_query)
            logger.info(f"Retrieved {len(result)} scheduling calculation records for {schedule_date}")
            return result
        except Exception as e:
            logger.error(f"Failed to get scheduling records for {schedule_date}: {str(e)}")
            return []

    async def truncate_collection(self, audit_logger=None, user_context: Optional[Dict[str, Any]] = None) -> int:
        """
        Truncate (delete all records from) the scheduling_requisition_calculation_table collection.
        Use with caution - this removes ALL data from the collection.
        """
        try:
            logger.warning(f"[SchedulingRequisitionCalculationRepository] TRUNCATING collection: {self.collection_name}")

            collection = self.db[self.collection_name]

            count_before = await collection.count_documents({})

            logger.warning(f"[SchedulingRequisitionCalculationRepository] Found {count_before} total record(s) to delete from {self.collection_name}")

            result = await collection.delete_many({})

            deleted_count = result.deleted_count
            logger.warning(f"[SchedulingRequisitionCalculationRepository] TRUNCATED {deleted_count} record(s) from {self.collection_name}")

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
        Truncate the collection asynchronously.
        Delegates to truncate_collection which is already async.
        """
        return await self.truncate_collection(audit_logger, user_context)

    async def update_block_totals(self, schedule_date: str, time_block: int, total_supply_updated: float, surplus_deficit_updated: float) -> int:
        """
        Update total_availablity_updated and surplus_deficit_updated for all records in a time block.
        """
        try:
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

            collection = self.db[self.collection_name]
            result = await collection.update_many(filter_query, update_query)

            modified_count = result.modified_count
            logger.info(f"Updated totals for block {time_block}: {modified_count} records")
            return modified_count

        except Exception as e:
            logger.error(f"Failed to update block totals for block {time_block}: {str(e)}")
            return 0
