"""
Demand Schedule Processor
Handles updating the schedule field in demand_serve based on aggregated supply data.
"""

import logging
from datetime import datetime, timedelta, date
from typing import Dict, List, Any, Optional

from .base_processor import BaseProcessor
from libs.models.demand_serve_entity import DemandServeEntity
from libs.enum.updated_flag import UpdatedFlag
from ..repository.demand_serve_repository import demand_serve_repository
from ..repository.supply_serve_repository import supply_serve_repository
from ..core.config import settings
from libs.core.job_entities import JobDetail

logger = logging.getLogger(__name__)

class DemandScheduleProcessor(BaseProcessor):
    """
    Processor for updating demand schedules based on supply data.
    """
    
    def __init__(self):
        super().__init__()
        self.demand_repository = demand_serve_repository
        self.supply_repository = supply_serve_repository
        self.service_name = "scheduler"

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute demand schedule update as a separate cron job.
        Updates the schedule field in demand_serve based on aggregated supply data.
        """
        job_type = "demand_schedule_update"
        job_name = "Demand Schedule Update"
        schedule = "*/5 * * * *"  # Every 5 minutes
        start_time = datetime.now()
        
        execution_id = self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            return {"status": "error", "message": "Failed to record job start"}
        
        self.set_correlation_id(execution_id)
        logger.info("=== EXECUTING DEMAND SCHEDULE UPDATE JOB ===")
        
        try:
            # Process for Yesterday, Today, Tomorrow
            today = datetime.now().date()
            target_dates = [today, today + timedelta(days=1)]
            
            success = await self._update_demand_schedule_logic(target_dates)
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            result = {
                "status": "success" if success else "error",
                "message": "Demand schedule updated successfully" if success else "Demand schedule update failed",
                "execution_time": execution_time,
                "data_type": "demand",
                "correlation_id": execution_id
            }
            
            self._record_job_completion(
                job_type, job_name, schedule, start_time, end_time, execution_id,
                result["status"], success, result
            )
            
            return result
            
        except Exception as e:
            end_time = datetime.now()
            logger.error(f"Error in demand schedule update job: {e}")
            self._record_job_completion(
                job_type, job_name, schedule, start_time, end_time, execution_id,
                "error", False, {"message": str(e)}, str(e)
            )
            return {"status": "error", "message": str(e)}
        finally:
            self.clear_correlation_id()

    async def _update_demand_schedule_logic(self, target_dates: List[Any]) -> bool:
        """
        Logic to update demand schedule based on aggregated supply data.
        """
        try:
            supply_map = self._get_supply_schedule_map(target_dates)
            if not supply_map:
                logger.info("No supply schedule data found for update.")
                return True
            
            updated_count = 0
            
            for date_obj in target_dates:
                date_key = date_obj if isinstance(date_obj, date) else date_obj.date()
                if date_key not in supply_map:
                    continue
                
                # Fetch existing demand records for the date
                start_dt = datetime.combine(date_key, datetime.min.time())
                end_dt = datetime.combine(date_key + timedelta(days=1), datetime.min.time())
                
                query = {
                    "scheduled_at": {"$gte": start_dt, "$lt": end_dt},
                    "type": "IDF"
                }
                
                # Use get_all_async from repository
                records = await self.demand_repository.get_all_async(
                    collection_name=self.demand_repository.collection_name,
                    filter=query
                )
                
                bulk_updates = []
                for record in records:
                    tb = record.get("time_block")
                    if tb in supply_map[date_key]:
                        supply_data = supply_map[date_key][tb]
                        
                        # Logic:
                        # If Drawal is available (and not 0/None) -> Schedule = Central Plants
                        # Else -> Schedule = Central + State Plants
                        
                        drawal = record.get("drawal")
                        if drawal is not None and drawal != 0:
                            new_schedule = supply_data["central"]
                        else:
                            new_schedule = supply_data["central"] + supply_data["state"]
                        
                        # Only update if changed
                        current_schedule = record.get("schedule")
                        # Handle float comparison
                        if current_schedule is None or abs(float(current_schedule) - float(new_schedule)) > 0.001:
                            try:
                                # Create entity for update
                                entity = DemandServeEntity(**record)
                                entity.schedule = new_schedule
                                entity.updated_at = datetime.now()
                                entity.updated_flag = UpdatedFlag.UPDATED
                                bulk_updates.append(entity)
                            except Exception as e:
                                logger.warning(f"Skipping record update for {record.get('_id')}: {e}")
                
                if bulk_updates:
                    await self.demand_repository.bulk_insert_demand_serve_with_deduplication(bulk_updates)
                    updated_count += len(bulk_updates)
            
            logger.info(f"Updated schedule for {updated_count} demand records")
            return True
            
        except Exception as e:
            logger.error(f"Error updating demand schedule logic: {e}")
            return False
    
    def _get_supply_schedule_map(self, raw_data: Any) -> Dict[Any, Dict[int, Dict[str, float]]]:
        """Fetch supply schedule data for the dates in raw_data (list of dicts or list of dates)"""
        try:
            unique_dates = set()
            
            # Handle list of dates directly
            if isinstance(raw_data, list) and raw_data and isinstance(raw_data[0], (date, datetime)):
                 for d in raw_data:
                     if isinstance(d, datetime):
                         unique_dates.add(d.date())
                     elif isinstance(d, date):
                         unique_dates.add(d)
            # Handle raw data dicts
            elif isinstance(raw_data, list):
                for item in raw_data:
                    if not isinstance(item, dict):
                        continue
                    scheduled_at = item.get("scheduled_at") or item.get("timestamp") or item.get("date_time")
                    if scheduled_at:
                        if isinstance(scheduled_at, str):
                            try:
                                dt = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
                                unique_dates.add(dt.date())
                            except ValueError:
                                pass
                        elif isinstance(scheduled_at, datetime):
                            unique_dates.add(scheduled_at.date())
            
            if not unique_dates:
                return {}
            
            sorted_dates = sorted(list(unique_dates))
            start_date = datetime.combine(sorted_dates[0], datetime.min.time())
            end_date = datetime.combine(sorted_dates[-1] + timedelta(days=1), datetime.min.time())
            
            aggregated_schedule = self.supply_repository.aggregate_supply_schedule(start_date, end_date)
            
            schedule_map = {}
            for item in aggregated_schedule:
                sch_at = item.get("scheduled_at")
                if isinstance(sch_at, datetime):
                    date_key = sch_at.date()
                else:
                    continue
                
                time_block = item.get("time_block")
                
                if date_key not in schedule_map:
                    schedule_map[date_key] = {}
                
                schedule_map[date_key][time_block] = {
                    "central": item.get("central_schedule", 0),
                    "state": item.get("state_schedule", 0),
                    "total": item.get("total_schedule", 0)
                }
                
            return schedule_map
            
        except Exception as e:
            logger.error(f"Error fetching supply schedule map: {e}")
            return {}

    def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Implementation of abstract method.
        DemandScheduleProcessor does not process raw data directly via this method.
        """
        return True


