"""
Job Execution Service
Business logic for job execution monitoring
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from croniter import croniter
import pytz

from ..repository.job_execution_repository import job_execution_repository

logger = logging.getLogger(__name__)


class JobExecutionService:
    """Service layer for job execution operations"""
    
    def __init__(self):
        self.repository = job_execution_repository
    
    def calculate_next_execution(
        self,
        schedule: str,
        timezone_str: str,
        base_time: datetime = None
    ) -> Optional[datetime]:
        """
        Calculate next execution time based on cron schedule
        
        Args:
            schedule: Cron expression (e.g., "* * * * *")
            timezone_str: Timezone string (e.g., "Asia/Kolkata")
            base_time: Base time to calculate from (defaults to now)
        
        Returns:
            Next execution datetime or None if calculation fails
        """
        try:
            # Get timezone
            tz = pytz.timezone(timezone_str)
            
            # Use provided base_time or current time in the job's timezone
            if base_time is None:
                base_time = datetime.now(tz)
            elif base_time.tzinfo is None:
                base_time = tz.localize(base_time)
            else:
                base_time = base_time.astimezone(tz)
            
            # Create croniter instance
            cron = croniter(schedule, base_time)
            
            # Get next execution time
            next_time = cron.get_next(datetime)
            
            return next_time
        except Exception as e:
            logger.warning(f"Failed to calculate next execution for schedule '{schedule}': {e}")
            return None
    
    def build_filter_query(
        self,
        job_id: Optional[str] = None,
        job_type: Optional[str] = None,
        status_filter: Optional[str] = None,
        job_name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """Build MongoDB filter query from parameters"""
        filter_query = {}
        
        if job_id:
                filter_query["job_id"] = job_id
        if job_type:
            filter_query["job_type"] = {"$regex": job_type, "$options": "i"}
        
        if status_filter:
            filter_query["status"] = status_filter.lower()
        
        if job_name:
            filter_query["job_name"] = {"$regex": job_name, "$options": "i"}
        
        # Date range filter
        if start_date or end_date:
            date_filter = {}
            
            if start_date:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                date_filter["$gte"] = start_dt
            
            if end_date:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                # Add one day to include the entire end date
                end_dt = end_dt + timedelta(days=1)
                date_filter["$lt"] = end_dt
            
            if date_filter:
                filter_query["execution_start"] = date_filter
        
        return filter_query
    
    async def get_job_executions_paginated(
        self,
        page: int,
        page_size: int,
        job_id: Optional[str] = None,
        job_type: Optional[str] = None,
        status_filter: Optional[str] = None,
        job_name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get paginated job executions with filters"""
        try:
            # Build filter query
            filter_query = self.build_filter_query(
                job_id, job_type, status_filter, job_name, start_date, end_date
            )
            
            # Calculate pagination
            skip = (page - 1) * page_size
            
            # Fetch data from repository
            job_details_list, total_records = await self.repository.get_job_executions_paginated(
                filter_query, skip, page_size
            )

            # Get job IDs
            job_ids = [str(detail["job_id"]) for detail in job_details_list]

            # Fetch job data
            job_data_map = await self.repository.get_job_data_by_ids(job_ids)
            
            # Build response items
            response_items = []
            
            for detail in job_details_list:
                job_id = str(detail["job_id"])
                job_data = job_data_map.get(job_id, {})
                
                # Calculate next execution time
                schedule = job_data.get("schedule", "")
                timezone_str = job_data.get("timezone", "UTC")
                next_execution = None
                
                if schedule:
                    base_time = detail.get("execution_end") or detail.get("execution_start")
                    next_execution = self.calculate_next_execution(schedule, timezone_str, base_time)
                
                item = {
                    "execution_id": detail.get("execution_id", ""),
                    "job_id": job_id,
                    "job_name": detail.get("job_name", ""),
                    "job_type": job_data.get("job_type", ""),
                    "schedule": schedule,
                    "timezone": timezone_str,
                    "execution_start": detail.get("execution_start"),
                    "execution_end": detail.get("execution_end"),
                    "duration_seconds": detail.get("duration_seconds"),
                    "status": detail.get("status", "unknown"),
                    "error_message": detail.get("error_message"),
                    "next_execution": next_execution
                }
                
                response_items.append(item)
            
            # Calculate total pages
            total_pages = (total_records + page_size - 1) // page_size
            
            return {
                "data": response_items,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_records": total_records,
                    "total_pages": total_pages
                }
            }
            
        except ValueError as e:
            # Re-raise validation errors
            raise
        except Exception as e:
            logger.error(f"Error in get_job_executions_paginated: {e}", exc_info=True)
            raise
    
    async def get_job_execution_detail(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific job execution"""
        try:
            # Get job detail
            job_detail = await self.repository.get_job_execution_by_id(execution_id)

            if not job_detail:
                return None

            # Get job data
            job_id = str(job_detail["job_id"])
            job_data = await self.repository.get_job_data_by_object_id(job_id)
            
            # Calculate next execution
            next_execution = None
            if job_data:
                schedule = job_data.get("schedule", "")
                timezone_str = job_data.get("timezone", "UTC")
                if schedule:
                    base_time = job_detail.get("execution_end") or job_detail.get("execution_start")
                    next_execution = self.calculate_next_execution(schedule, timezone_str, base_time)
            
            # Build response
            response = {
                "execution_id": job_detail.get("execution_id"),
                "job_id": job_id,
                "job_name": job_detail.get("job_name"),
                "execution_start": job_detail.get("execution_start"),
                "execution_end": job_detail.get("execution_end"),
                "duration_seconds": job_detail.get("duration_seconds"),
                "status": job_detail.get("status"),
                "success": job_detail.get("success"),
                "error_message": job_detail.get("error_message"),
                "error_details": job_detail.get("error_details"),
                "result_data": job_detail.get("result_data", {}),
                "metadata": job_detail.get("metadata", {}),
                "service": job_detail.get("service"),
                "created_at": job_detail.get("created_at")
            }
            
            # Add job configuration if available
            if job_data:
                response["job_config"] = {
                    "job_type": job_data.get("job_type"),
                    "name": job_data.get("name"),
                    "type": job_data.get("type"),
                    "schedule": job_data.get("schedule"),
                    "enabled": job_data.get("enabled"),
                    "timezone": job_data.get("timezone"),
                    "description": job_data.get("description"),
                    "priority": job_data.get("priority"),
                    "next_execution": next_execution
                }
            
            return response
            
        except Exception as e:
            logger.error(f"Error in get_job_execution_detail: {e}", exc_info=True)
            raise
    
    async def get_jobs_summary(self) -> Dict[str, Any]:
        """Get summary of all jobs with their latest execution status"""
        try:
            # Get all jobs
            all_jobs = await self.repository.get_all_jobs()

            summary_items = []

            for job in all_jobs:
                job_id = str(job["_id"])

                # Get latest execution for this job
                latest_execution = await self.repository.get_latest_execution_for_job(job_id)
                
                # Calculate next execution
                schedule = job.get("schedule", "")
                timezone_str = job.get("timezone", "UTC")
                next_execution = None
                
                if schedule:
                    base_time = None
                    if latest_execution:
                        base_time = latest_execution.get("execution_end") or latest_execution.get("execution_start")
                    next_execution = self.calculate_next_execution(schedule, timezone_str, base_time)
                
                summary_item = {
                    "job_id": job_id,
                    "job_name": job.get("name"),
                    "job_type": job.get("job_type"),
                    "schedule": schedule,
                    "timezone": timezone_str,
                    "enabled": job.get("enabled", False),
                    "priority": job.get("priority"),
                    "description": job.get("description"),
                    "next_execution": next_execution,
                    "latest_execution": None
                }
                
                if latest_execution:
                    summary_item["latest_execution"] = {
                        "execution_id": latest_execution.get("execution_id"),
                        "execution_start": latest_execution.get("execution_start"),
                        "execution_end": latest_execution.get("execution_end"),
                        "duration_seconds": latest_execution.get("duration_seconds"),
                        "status": latest_execution.get("status"),
                        "error_message": latest_execution.get("error_message")
                    }
                
                summary_items.append(summary_item)
            
            # Sort by priority (lower number = higher priority)
            summary_items.sort(key=lambda x: (x.get("priority") or 999, x.get("job_name") or ""))
            
            return {
                "data": summary_items,
                "total_jobs": len(summary_items)
            }
            
        except Exception as e:
            logger.error(f"Error in get_jobs_summary: {e}", exc_info=True)
            raise


# Singleton instance
job_execution_service = JobExecutionService()
