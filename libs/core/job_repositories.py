"""
Shared Job Repositories
Database repositories for job-related operations that can be used across all services
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from uuid import uuid4

from libs.database.base_repository import BaseRepository
from .job_entities import JobData, JobDataUpdate, JobDetail, JobDetailQuery

logger = logging.getLogger(__name__)


class JobDataRepository(BaseRepository):
    """Repository for job configuration data"""
    
    def __init__(self, audit_db_uri: str, audit_db_name: str, job_data_collection: str):
        """Initialize the repository with MongoDB connection"""
        
        super().__init__(
            collection_name=job_data_collection,
            mongodb_uri=audit_db_uri,
            database_name=audit_db_name
        )
        
        # Create indexes for better performance
        self._create_indexes()
    
    async def _create_indexes(self):
        """Create database indexes for optimal performance"""
        try:
            # Primary indexes
            self.collection.create_index([("job_type", 1)], unique=True)
            self.collection.create_index([("enabled", 1)])
            self.collection.create_index([("type", 1)])
            self.collection.create_index([("priority", 1)])
            self.collection.create_index([("service", 1)])
            
            # Compound indexes
            self.collection.create_index([("enabled", 1), ("type", 1)])
            self.collection.create_index([("enabled", 1), ("service", 1)])
            self.collection.create_index([("created_at", -1)])
            self.collection.create_index([("updated_at", -1)])
            
            logger.info("Created indexes for job_data collection")
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to create indexes: %s", str(e))
    
    async def create_job(self, job_data: JobData) -> str:
        """
        Create a new job configuration
        
        Args:
            job_data: Job data entity to create
            
        Returns:
            str: ID of the created job
        """
        try:
            # Validate that service field is properly set
            if not job_data.service:
                raise ValueError("Service field is required when creating a job")
            
            # Check if job already exists for this service
            existing_job = self.get_job(job_data.job_type)
            if existing_job:
                # If it exists but for a different service, that's an error
                if existing_job.get("service") != job_data.service:
                    raise ValueError(f"Job type '{job_data.job_type}' already exists for service '{existing_job.get('service')}'. Use a unique job_type for service '{job_data.service}'")
                else:
                    # Same service, same job_type - this is a duplicate
                    raise ValueError(f"Job type '{job_data.job_type}' already exists for service '{job_data.service}'")
            
            # Exclude id field to let MongoDB generate the _id
            job_dict = job_data.model_dump(by_alias=True, exclude={"id"})
            
            # Ensure service field is included in the document
            job_dict["service"] = job_data.service
            
            result = self.collection.insert_one(job_dict)
            job_id = str(result.inserted_id)
            
            logger.info("Created job: %s (ID: %s, Service: %s)", 
                       job_data.name, job_id, job_data.service)
            return job_id
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to create job: %s", str(e))
            raise
    
    async def get_job(self, job_type: str) -> Optional[Dict[str, Any]]:
        """
        Get a job by job_type
        
        Args:
            job_type: Job type to retrieve
            
        Returns:
            Job data or None if not found
        """
        try:
            job = self.collection.find_one({"job_type": job_type})
            if job and '_id' in job:
                job['_id'] = str(job['_id'])
            return job
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get job %s: %s", job_type, str(e))
            return None
    
    async def get_job_by_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a job by MongoDB _id
        
        Args:
            job_id: MongoDB _id to retrieve
            
        Returns:
            Job data or None if not found
        """
        try:
            from bson import ObjectId
            job = self.collection.find_one({"_id": ObjectId(job_id)})
            if job and '_id' in job:
                job['_id'] = str(job['_id'])
            return job
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get job by id %s: %s", job_id, str(e))
            return None
    
    async def get_all_jobs(self, enabled_only: bool = False, service: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all jobs
        
        Args:
            enabled_only: If True, return only enabled jobs
            service: If provided, filter by service name
            
        Returns:
            List of job configurations
        """
        try:
            query: Dict[str, Any] = {}
            if enabled_only:
                query["enabled"] = True
            if service:
                query["service"] = service
            
            jobs = await self.collection.find(query).sort("priority", 1).to_list(length=10000)

            # Convert ObjectId to string for JSON serialization
            for job in jobs:
                if '_id' in job:
                    job['_id'] = str(job['_id'])
            
            return jobs
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get all jobs: %s", str(e))
            return []
    
    async def get_jobs_by_type(self, job_type: str, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """
        Get jobs by type
        
        Args:
            job_type: Type of jobs to retrieve
            enabled_only: If True, return only enabled jobs
            
        Returns:
            List of job configurations
        """
        try:
            query: Dict[str, Any] = {"job_type": job_type}
            if enabled_only:
                query["enabled"] = True
            
            jobs = await self.collection.find(query).sort("priority", 1).to_list(length=10000)

            # Convert ObjectId to string for JSON serialization
            for job in jobs:
                if '_id' in job:
                    job['_id'] = str(job['_id'])
            
            return jobs
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get jobs by type %s: %s", job_type, str(e))
            return []
    
    async def get_jobs_by_service(self, service: str, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """
        Get jobs by service (treats blank/empty service as 'scheduler')
        
        Args:
            service: Service name to filter by
            enabled_only: If True, return only enabled jobs
            
        Returns:
            List of job configurations
        """
        try:
            # Build query - if service is 'scheduler', include blank/empty services too
            if service == 'scheduler':
                # Include jobs with service='scheduler' OR service is blank/empty/null
                service_query = {
                    "$or": [
                        {"service": "scheduler"},
                        {"service": {"$in": ["", None]}},
                        {"service": {"$exists": False}}
                    ]
                }
            else:
                service_query = {"service": service}
            
            # Combine service query with enabled filter
            query = service_query
            # if enabled_only:
            #     query = {"$and": [service_query, {"enabled": True}]}
            # else:
            #     query = service_query
            
            jobs = await self.collection.find(query).sort("priority", 1).to_list(length=10000)

            # Convert ObjectId to string for JSON serialization
            for job in jobs:
                if '_id' in job:
                    job['_id'] = str(job['_id'])
            
            return jobs
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get jobs by service %s: %s", service, str(e))
            return []
    
    def update_job(self, job_type: str, update_data: JobDataUpdate) -> bool:
        """
        Update a job configuration
        
        Args:
            job_type: Job type to update
            update_data: Update data
            
        Returns:
            bool: True if updated successfully
        """
        try:
            update_dict = update_data.model_dump(exclude_none=True)
            update_dict["updated_at"] = datetime.now()
            
            result = self.collection.update_one(
                {"job_type": job_type},
                {
                    "$set": update_dict,
                    "$inc": {"version": 1}
                }
            )
            
            if result.modified_count > 0:
                logger.info("Updated job: %s", job_type)
                return True
            else:
                logger.warning("Job %s not found for update", job_type)
                return False
                
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to update job %s: %s", job_type, str(e))
            return False
    
    def enable_job(self, job_type: str) -> bool:
        """
        Enable a job
        
        Args:
            job_type: Job type to enable
            
        Returns:
            bool: True if enabled successfully
        """
        return self.update_job(job_type, JobDataUpdate(enabled=True))
    
    def disable_job(self, job_type: str) -> bool:
        """
        Disable a job
        
        Args:
            job_type: Job type to disable
            
        Returns:
            bool: True if disabled successfully
        """
        return self.update_job(job_type, JobDataUpdate(enabled=False))
    
    def update_job_enabled_status(self, job_type: str, enabled: bool) -> bool:
        """
        Update the enabled status of a job
        
        Args:
            job_type: Type of job to update
            enabled: New enabled status
            
        Returns:
            bool: True if successful, False otherwise
        """
        return self.update_job(job_type, JobDataUpdate(enabled=enabled))
    
    def delete_job(self, job_type: str) -> bool:
        """
        Delete a job configuration
        
        Args:
            job_type: Job type to delete
            
        Returns:
            bool: True if deleted successfully
        """
        try:
            result = self.collection.delete_one({"job_type": job_type})
            
            if result.deleted_count > 0:
                logger.info("Deleted job: %s", job_type)
                return True
            else:
                logger.warning("Job %s not found for deletion", job_type)
                return False
                
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to delete job %s: %s", job_type, str(e))
            return False
    
    def job_exists(self, job_type: str) -> bool:
        """
        Check if a job exists
        
        Args:
            job_type: Job type to check
            
        Returns:
            bool: True if job exists
        """
        try:
            count = self.collection.count_documents({"job_type": job_type})
            return count > 0
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to check job existence %s: %s", job_type, str(e))
            return False
    
    def get_enabled_jobs(self, service: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all enabled jobs
        
        Args:
            service: Optional service filter
            
        Returns:
            List of enabled job configurations
        """
        return self.get_all_jobs(enabled_only=True, service=service)
    
    def get_job_count(self, enabled_only: bool = False, service: Optional[str] = None) -> int:
        """
        Get total job count
        
        Args:
            enabled_only: If True, count only enabled jobs
            service: Optional service filter
            
        Returns:
            int: Total number of jobs
        """
        try:
            query: Dict[str, Any] = {}
            if enabled_only:
                query["enabled"] = True
            if service:
                query["service"] = service
            
            return self.collection.count_documents(query)
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get job count: %s", str(e))
            return 0
    
    def get_all_services(self) -> List[str]:
        """
        Get list of all unique services from job_data collection
        
        Returns:
            List[str]: List of unique service names
        """
        try:
            # Use distinct to get unique service values
            services = self.collection.distinct("service")
            # Filter out None/empty values and return sorted list
            return sorted([s for s in services if s])
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get all services: %s", str(e))
            return []


class JobDetailRepository(BaseRepository):
    """Repository for job execution logs (time series data)"""
    
    def __init__(self, audit_db_uri: str, audit_db_name: str, job_detail_collection: str):
        """Initialize the repository with MongoDB connection"""
        
        super().__init__(
            collection_name=job_detail_collection,
            mongodb_uri=audit_db_uri,
            database_name=audit_db_name
        )
        
        # Create indexes for better performance
        self._create_indexes()
    
    async def _create_indexes(self):
        """Create database indexes for optimal performance"""
        try:
            # Primary indexes for time series data
            self.collection.create_index([("job_id", 1)])
            self.collection.create_index([("execution_id", 1)])
            self.collection.create_index([("status", 1)])
            self.collection.create_index([("success", 1)])
            self.collection.create_index([("service", 1)])
            
            # Time-based indexes (most important for time series)
            self.collection.create_index([("execution_start", -1)])
            self.collection.create_index([("created_at", -1)])
            
            # Compound indexes for common query patterns
            self.collection.create_index([("job_id", 1), ("execution_start", -1)])
            self.collection.create_index([("job_id", 1), ("status", 1)])
            self.collection.create_index([("job_id", 1), ("success", 1)])
            self.collection.create_index([("service", 1), ("execution_start", -1)])
            self.collection.create_index([("status", 1), ("execution_start", -1)])
            self.collection.create_index([("success", 1), ("execution_start", -1)])
            
            logger.info("Created indexes for job_detail collection")
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to create indexes: %s", str(e))
    
    async def log_execution(self, job_detail: JobDetail) -> str:
        """
        Log a job execution
        
        Args:
            job_detail: Job execution details to log
            
        Returns:
            str: ID of the logged execution
        """
        try:
            # Validate that service field is properly set
            if not job_detail.service:
                raise ValueError("Service field is required when logging job execution")
            
            # Generate execution_id if not provided
            if not job_detail.execution_id:
                job_detail.execution_id = str(uuid4())
            
            job_dict = job_detail.model_dump(by_alias=True, exclude={"id"})
            
            # Ensure service field is included in the document
            job_dict["service"] = job_detail.service
            
            result = self.collection.insert_one(job_dict)
            execution_id = str(result.inserted_id)
            
            logger.info("Logged job execution: %s (Service: %s, Execution ID: %s)", 
                       job_detail.job_name, job_detail.service, job_detail.execution_id)
            return execution_id
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to log job execution: %s", str(e))
            raise
    
    async def get_execution_logs(self, query: JobDetailQuery) -> List[Dict[str, Any]]:
        """
        Get execution logs based on query parameters
        
        Args:
            query: Query parameters for filtering logs
            
        Returns:
            List of execution logs
        """
        try:
            filter_query: Dict[str, Any] = {}
            
            # Add filters based on query parameters
            if query.job_id:
                filter_query["job_id"] = query.job_id
            
            if query.status:
                filter_query["status"] = query.status
                
            if query.service:
                filter_query["service"] = query.service
            
            if query.start_date or query.end_date:
                date_filter: Dict[str, Any] = {}
                if query.start_date:
                    date_filter["$gte"] = query.start_date
                if query.end_date:
                    date_filter["$lte"] = query.end_date
                filter_query["execution_start"] = date_filter
            
            # Execute query with pagination
            cursor = self.collection.find(
                filter_query,
                {"_id": 0}
            ).sort("execution_start", -1).skip(query.skip).limit(query.limit).to_list(length=10000)

        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get execution logs: %s", str(e))
            return []
    
    async def get_job_execution_history(self, job_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get execution history for a specific job
        
        Args:
            job_id: Job ID to get history for
            limit: Maximum number of records to return
            
        Returns:
            List of execution logs for the job
        """
        try:
            cursor = self.collection.find(
                {"job_id": job_id},
                {"_id": 0}
            ).sort("execution_start", -1).limit(limit).to_list(length=10000)

        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get execution history for job %s: %s", job_id, str(e))
            return []
    
    async def get_recent_executions(self, job_id: str) -> bool:
        """
        Get recent successful executions for a job
        
        Args:
            job_id: Job ID to get recent executions for
            
        Returns:
            bool: True if recent successful execution logs exist
        """
        try:
            count = self.collection.count_documents(
                {
                    "job_id": job_id,
                    "status": "success"
                }
            )
            
            return count > 0
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get recent executions for job %s: %s", job_id, str(e))
            return False
    
    async def get_failed_executions(self, job_id: Optional[str] = None, days: int = 7, service: Optional[str] = None) -> bool:
        """
        Get failed executions
        
        Args:
            job_id: Optional job ID to filter by
            days: Number of days to look back
            service: Optional service to filter by
            
        Returns:
            bool: True if failed execution logs exist
        """
        try:
            cutoff_time = datetime.now() - timedelta(days=days)
            
            filter_query = {
                "success": False,
                "execution_start": {"$gte": cutoff_time}
            }
            
            if job_id:
                filter_query["job_id"] = job_id
            if service:
                filter_query["service"] = service
            
            count = self.collection.count_documents(filter_query)
            return count > 0
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get failed executions: %s", str(e))
            return False
    
    async def get_execution_by_id(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific execution by execution_id
        
        Args:
            execution_id: Execution ID to retrieve
            
        Returns:
            Execution log or None if not found
        """
        try:
            execution = self.collection.find_one(
                {"execution_id": execution_id},
                {"_id": 0}
            )
            return execution
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get execution %s: %s", execution_id, str(e))
            return None
    
    async def update_execution(self, execution_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update an execution log
        
        Args:
            execution_id: Execution ID to update
            update_data: Data to update
            
        Returns:
            bool: True if updated successfully
        """
        try:
            update_data["updated_at"] = datetime.now()
            
            result = self.collection.update_one(
                {"execution_id": execution_id},
                {"$set": update_data}
            )
            
            if result.modified_count > 0:
                logger.info("Updated execution: %s", execution_id)
                return True
            else:
                logger.warning("Execution %s not found for update", execution_id)
                return False
                
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to update execution %s: %s", execution_id, str(e))
            return False
    
    async def get_job_statistics(self, job_id: str, days: int = 30) -> Dict[str, Any]:
        """
        Get statistics for a job
        
        Args:
            job_id: Job ID to get statistics for
            days: Number of days to analyze
            
        Returns:
            Dictionary with job statistics
        """
        try:
            cutoff_time = datetime.now() - timedelta(days=days)
            
            # Get total executions
            total_executions = self.collection.count_documents({
                "job_id": job_id,
                "execution_start": {"$gte": cutoff_time}
            })
            
            # Get successful executions
            successful_executions = self.collection.count_documents({
                "job_id": job_id,
                "success": True,
                "execution_start": {"$gte": cutoff_time}
            })
            
            # Get failed executions
            failed_executions = self.collection.count_documents({
                "job_id": job_id,
                "success": False,
                "execution_start": {"$gte": cutoff_time}
            })
            
            # Calculate success rate
            success_rate = round((successful_executions / total_executions * 100), 2) if total_executions > 0 else 0
            
            # Get average duration
            pipeline = [
                {
                    "$match": {
                        "job_id": job_id,
                        "execution_start": {"$gte": cutoff_time},
                        "duration_seconds": {"$exists": True, "$ne": None}
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "avg_duration": {"$avg": "$duration_seconds"},
                        "min_duration": {"$min": "$duration_seconds"},
                        "max_duration": {"$max": "$duration_seconds"}
                    }
                }
            ]
            
            duration_stats = await self.collection.aggregate(pipeline).to_list(length=10000)
            avg_duration = duration_stats[0]["avg_duration"] if duration_stats else 0
            min_duration = duration_stats[0]["min_duration"] if duration_stats else 0
            max_duration = duration_stats[0]["max_duration"] if duration_stats else 0
            
            # Get last execution
            last_execution = self.collection.find_one(
                {"job_id": job_id},
                {"_id": 0},
                sort=[("execution_start", -1)]
            )
            
            return {
                "job_id": job_id,
                "period_days": days,
                "total_executions": total_executions,
                "successful_executions": successful_executions,
                "failed_executions": failed_executions,
                "success_rate": round(success_rate, 2),
                "average_duration_seconds": round(avg_duration, 2),
                "min_duration_seconds": round(min_duration, 2),
                "max_duration_seconds": round(max_duration, 2),
                "last_execution": last_execution
            }
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get statistics for job %s: %s", job_id, str(e))
            return {
                "job_id": job_id,
                "period_days": days,
                "total_executions": 0,
                "successful_executions": 0,
                "failed_executions": 0,
                "success_rate": 0.0,
                "average_duration_seconds": 0.0,
                "min_duration_seconds": 0.0,
                "max_duration_seconds": 0.0,
                "last_execution": None
            }
    
    def get_old_executions_for_migration(self, days_old: int) -> List[Dict[str, Any]]:
        """
        Get old executions for migration to historical collection
        
        Args:
            days_old: Minimum age in days for migration
            
        Returns:
            List of old execution logs
        """
        try:
            cutoff_time = datetime.now() - timedelta(days=days_old)
            
            cursor = self.collection.find({
                "execution_start": {"$lt": cutoff_time}
            }).sort("execution_start", 1).to_list(length=10000)

        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get old executions for migration: %s", str(e))
            return []
    
    def delete_old_executions(self, days_old: int) -> int:
        """
        Delete old executions from the active collection
        
        Args:
            days_old: Minimum age in days for deletion
            
        Returns:
            int: Number of deleted records
        """
        try:
            cutoff_time = datetime.now() - timedelta(days=days_old)
            
            result = self.collection.delete_many({
                "execution_start": {"$lt": cutoff_time}
            })
            
            deleted_count = result.deleted_count
            logger.info("Deleted %s old execution records", deleted_count)
            return deleted_count
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to delete old executions: %s", str(e))
            return 0
    
    def get_execution_count(self, job_id: Optional[str] = None, days: Optional[int] = None, service: Optional[str] = None) -> int:
        """
        Get count of execution logs
        
        Args:
            job_id: Optional job ID to filter by
            days: Optional number of days to look back
            service: Optional service to filter by
            
        Returns:
            int: Number of execution records
        """
        try:
            filter_query: Dict[str, Any] = {}
            
            if job_id:
                filter_query["job_id"] = job_id
            if service:
                filter_query["service"] = service
            
            if days:
                cutoff_time = datetime.now() - timedelta(days=days)
                filter_query["execution_start"] = {"$gte": cutoff_time}
            
            return self.collection.count_documents(filter_query)
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get execution count: %s", str(e))
            return 0
    
    async def get_running_jobs(self) -> List[Dict[str, Any]]:
        """
        Get all currently running jobs
        
        Returns:
            List[Dict[str, Any]]: List of running jobs with execution details
        """
        try:
            # Find jobs that are currently running (status = "running")
            running_jobs = await self.collection.find(
                {"status": "running"},
                {
                    "job_id": 1,
                    "job_name": 1,
                    "service": 1,
                    "execution_start": 1,
                    "job_metadata": 1,
                    "execution_duration": 1,
                    "progress": 1,
                    "current_step": 1,
                    "estimated_completion": 1
                }
            ).sort("execution_start", -1).to_list(length=10000)
            
            # Calculate runtime for each job and convert ObjectIds to strings
            current_time = datetime.now()
            for job in running_jobs:
                # Convert ObjectId to string
                if "_id" in job:
                    job["_id"] = str(job["_id"])
                
                if job.get("execution_start"):
                    runtime = (current_time - job["execution_start"]).total_seconds()
                    job["runtime_seconds"] = runtime
                    job["runtime_formatted"] = self._format_duration(runtime)
            
            logger.debug("Found %s running jobs", len(running_jobs))
            return running_jobs
            
        except Exception as e:
            logger.error("Error getting running jobs: %s", str(e))
            return []
    
    async def get_job_execution_trace(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed execution trace for a specific job
        
        Args:
            job_id: ID of the job to trace
            
        Returns:
            Dict[str, Any]: Job execution trace with current status and history
        """
        try:
            # Get the most recent execution record
            latest_execution = self.collection.find_one(
                {"job_id": job_id},
                sort=[("execution_start", -1)]
            )
            
            if not latest_execution:
                logger.warning("No execution found for job_id: %s", job_id)
                return None
            
            # Convert ObjectId to string
            if "_id" in latest_execution:
                latest_execution["_id"] = str(latest_execution["_id"])
            
            # Get execution history (last 10 executions)
            execution_history = await self.collection.find(
                {"job_id": job_id}
            ).sort("execution_start", -1).limit(10).to_list(length=10000)
            
            # Convert ObjectIds to strings in history
            for execution in execution_history:
                if "_id" in execution:
                    execution["_id"] = str(execution["_id"])
            
            # Calculate runtime if job is currently running
            if latest_execution.get("status") == "running":
                start_time = latest_execution.get("execution_start")
                if start_time:
                    runtime = (datetime.now() - start_time).total_seconds()
                    latest_execution["runtime_seconds"] = runtime
                    latest_execution["runtime_formatted"] = self._format_duration(runtime)
            
            return {
                "job_id": job_id,
                "current_status": latest_execution.get("status", "unknown"),
                "latest_execution": latest_execution,
                "execution_history": execution_history,
                "total_executions": len(execution_history)
            }
            
        except Exception as e:
            logger.error("Error getting job execution trace for %s: %s", job_id, str(e))
            return None
    
    async def get_jobs_by_service(self, service: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get jobs for a specific service (centralized job monitoring)
        
        Args:
            service: Service name (scheduler, ingestion, processing, auth)
            limit: Maximum number of jobs to return
            
        Returns:
            List[Dict[str, Any]]: List of jobs for the service
        """
        try:
            if service == "scheduler":
                # Get scheduler jobs from job_detail collections
                return await self._get_scheduler_jobs(limit)
            elif service == "ingestion":
                # Get ingestion jobs from job_data collection and cross-reference with job_detail
                return await self._get_ingestion_jobs(limit)
            elif service == "processing":
                # Get processing jobs (placeholder for future implementation)
                return await self._get_processing_jobs(limit)
            elif service == "auth":
                # Get auth jobs (placeholder for future implementation)
                return await self._get_auth_jobs(limit)
            else:
                logger.warning("Unknown service: %s", service)
                return []
                
        except Exception as e:
            logger.error("Error getting jobs for service %s: %s", service, str(e))
            return []
    
    async def _get_scheduler_jobs(self, limit: int) -> List[Dict[str, Any]]:
        """Get scheduler service jobs from job_detail collections"""
        try:
            # Get jobs from both current and historical collections
            current_jobs = await self.collection.find(
                {"service": "scheduler"}
            ).sort("execution_start", -1).limit(limit).to_list(length=10000)
            
            # Note: historical_collection would need to be initialized if needed
            # For now, just use current collection
            all_jobs = current_jobs
            
            # Convert ObjectIds to strings and add runtime for running jobs
            current_time = datetime.now()
            for job in all_jobs:
                if "_id" in job:
                    job["_id"] = str(job["_id"])
                
                if job.get("status") == "running" and job.get("execution_start"):
                    runtime = (current_time - job["execution_start"]).total_seconds()
                    job["runtime_seconds"] = runtime
                    job["runtime_formatted"] = self._format_duration(runtime)
            
            # Sort by execution start time (newest first)
            all_jobs.sort(key=lambda x: x.get("execution_start", datetime.min), reverse=True)
            
            # Limit results
            return all_jobs[:limit]
            
        except Exception as e:
            logger.error("Error getting scheduler jobs: %s", str(e))
            return []
    
    async def _get_ingestion_jobs(self, limit: int) -> List[Dict[str, Any]]:
        """Get ingestion service jobs from job_data collection"""
        try:
            # This would need access to job_data_repository
            # For now, return empty list as placeholder
            logger.info("Ingestion jobs retrieval not yet implemented in libs")
            return []
            
        except Exception as e:
            logger.error("Error getting ingestion jobs: %s", str(e))
            return []
    
    async def _get_processing_jobs(self, limit: int) -> List[Dict[str, Any]]:
        """Get processing service jobs (placeholder for future implementation)"""
        try:
            # Placeholder for processing service jobs
            _ = limit  # Suppress unused argument warning
            return []
            
        except Exception as e:
            logger.error("Error getting processing jobs: %s", str(e))
            return []
    
    async def _get_auth_jobs(self, limit: int) -> List[Dict[str, Any]]:
        """Get auth service jobs (placeholder for future implementation)"""
        try:
            # Placeholder for auth service jobs
            _ = limit  # Suppress unused argument warning
            return []
            
        except Exception as e:
            logger.error("Error getting auth jobs: %s", str(e))
            return []
    
    async def get_overall_job_statistics(self, hours: int = 24, job_data_repository=None) -> Dict[str, Any]:
        """
        Get overall job execution statistics for the specified time period
        For Total Jobs (24H): Shows total count of ALL available jobs from job_data collection
        Other metrics (successful, failed, running) use execution logs from the specified time period
        
        Args:
            hours: Number of hours to look back for execution metrics
            job_data_repository: Optional JobDataRepository to include job_data collection counts
            
        Returns:
            Dict[str, Any]: Job statistics
        """
        try:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            
            # Get jobs from current collection (execution logs) for success/failure metrics
            jobs = await self.collection.find({
                "execution_start": {"$gte": cutoff_time}
            }).to_list(length=10000)
            
            # Calculate statistics from execution logs
            total_execution_jobs = len(jobs)
            successful_jobs = len([j for j in jobs if j.get("success", False)])
            failed_jobs = len([j for j in jobs if not j.get("success", True)])
            running_jobs = len([j for j in jobs if j.get("status") == "running"])
            
            # For Total Jobs (24H): Use job_data collection count for ALL available jobs in the system
            total_jobs = 0
            if job_data_repository:
                try:
                    # Get total count of ALL enabled jobs from job_data collection
                    total_jobs = job_data_repository.get_job_count(enabled_only=True)
                    logger.info("Total available jobs in system: %d", total_jobs)
                except Exception as e:
                    logger.warning("Failed to get total job_data count: %s", str(e))
                    # Fallback to execution logs if job_data_repository fails
                    total_jobs = total_execution_jobs
            else:
                # Fallback to execution logs if job_data_repository not provided
                total_jobs = total_execution_jobs
            
            success_rate = round((successful_jobs / total_execution_jobs * 100), 2) if total_execution_jobs > 0 else 0
            
            # Service breakdown (treat blank/empty service as 'scheduler')
            service_breakdown = {}
            for job in jobs:
                service = job.get("service", "").strip()
                if not service:  # Blank or empty service
                    service = "scheduler"
                if service not in service_breakdown:
                    service_breakdown[service] = {
                        "total": 0,
                        "successful": 0,
                        "failed": 0,
                        "running": 0
                    }
                
                service_breakdown[service]["total"] += 1
                if job.get("success", False):
                    service_breakdown[service]["successful"] += 1
                elif job.get("status") == "running":
                    service_breakdown[service]["running"] += 1
                else:
                    service_breakdown[service]["failed"] += 1
            
            # Add job_data counts to service breakdown for ALL services
            if job_data_repository:
                try:
                    # Get all jobs to ensure we include all services (including "unknown")
                    all_jobs = job_data_repository.get_all_jobs(enabled_only=True)
                    
                    # Group jobs by service (treat blank/empty service as 'scheduler')
                    service_job_counts = {}
                    for job in all_jobs:
                        service = job.get('service', '').strip()
                        if not service:  # Blank or empty service
                            service = 'scheduler'
                        if service not in service_job_counts:
                            service_job_counts[service] = 0
                        service_job_counts[service] += 1
                    
                    # Update service breakdown with job counts
                    for service, job_count in service_job_counts.items():
                        if job_count > 0:
                            # Initialize or update service breakdown with total available jobs
                            # Preserve existing execution statistics if they exist
                            existing_stats = service_breakdown.get(service, {})
                            service_breakdown[service] = {
                                "total": job_count,  # This is the job count from job_data
                                "successful": existing_stats.get("successful", 0), 
                                "failed": existing_stats.get("failed", 0), 
                                "running": existing_stats.get("running", 0)
                            }
                            
                            logger.info("Service %s: %d total available jobs, %d successful, %d failed, %d running", 
                                      service, job_count, 
                                      existing_stats.get("successful", 0),
                                      existing_stats.get("failed", 0),
                                      existing_stats.get("running", 0))
                        
                except Exception as e:
                    logger.warning("Failed to add job_data counts to service breakdown: %s", str(e))
            
            # Calculate success rates for each service
            for service, data in service_breakdown.items():
                # For success rate, use execution data (successful/total executions)
                # For total jobs, use job_data count (available jobs in system)
                total_executions = data["successful"] + data["failed"] + data["running"]
                data["success_rate"] = round((data["successful"] / total_executions * 100), 2) if total_executions > 0 else 0
            
            return {
                "total_jobs": total_jobs,
                "successful_jobs": successful_jobs,
                "failed_jobs": failed_jobs,
                "running_jobs": running_jobs,
                "success_rate": round(success_rate, 2),
                "service_breakdown": service_breakdown,
                "time_period_hours": hours,
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error("Error getting overall job statistics: %s", str(e))
            return {
                "total_jobs": 0,
                "successful_jobs": 0,
                "failed_jobs": 0,
                "running_jobs": 0,
                "success_rate": 0,
                "service_breakdown": {},
                "time_period_hours": hours,
                "generated_at": datetime.now().isoformat()
            }
    
    def update_job_progress(self, job_id: str, progress: int, current_step: str = "",
                           estimated_completion: Optional[datetime] = None) -> bool:
        """
        Update job progress for a running job
        
        Args:
            job_id: ID of the job to update
            progress: Progress percentage (0-100)
            current_step: Current step description
            estimated_completion: Estimated completion time
            
        Returns:
            bool: True if update was successful
        """
        try:
            update_data = {
                "progress": progress,
                "current_step": current_step,
                "updated_at": datetime.now()
            }
            
            if estimated_completion:
                update_data["estimated_completion"] = estimated_completion
            
            result = self.collection.update_one(
                {"job_id": job_id, "status": "running"},
                {"$set": update_data}
            )
            
            if result.modified_count > 0:
                logger.debug("Updated progress for job %s: %s%% - %s", job_id, progress, current_step)
                return True
            else:
                logger.warning("No running job found with job_id: %s", job_id)
                return False
                
        except Exception as e:
            logger.error("Error updating job progress for %s: %s", job_id, str(e))
            return False
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to human-readable string"""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}h"
    
    def get_all_jobs_from_job_data(self) -> List[Dict[str, Any]]:
        """
        Get all jobs from job_data collection
        
        Returns:
            List[Dict[str, Any]]: List of all jobs from job_data collection
        """
        try:
            # This method would need access to job_data_repository
            # For now, return empty list as placeholder
            logger.info("get_all_jobs_from_job_data not yet implemented in libs")
            return []
            
        except Exception as e:
            logger.error("Error getting all jobs from job_data: %s", str(e))
            return []
    
    def get_job_execution_details(self, job_id: str, job_data_repository=None) -> Dict[str, Any]:
        """
        Get detailed execution information for a specific job
        
        Args:
            job_id: Job ID to get execution details for
            job_data_repository: Optional job data repository for additional details
            
        Returns:
            Dict[str, Any]: Detailed execution information
        """
        try:
            # Get basic job information from job_data collection
            job_info = {}
            if job_data_repository:
                try:
                    logger.info("Getting job data for job_id: %s", job_id)
                    job_data = job_data_repository.get_job_by_id(job_id)
                    logger.info("Job data retrieved: %s", job_data)
                    if job_data:
                        job_info = {
                            "job_id": job_data.get("_id", job_id),
                            "job_type": job_data.get("job_type", "Unknown"),
                            "name": job_data.get("name", job_data.get("job_name", "Unknown")),
                            "service": job_data.get("service", "Unknown"),
                            "enabled": job_data.get("enabled", False),
                            "schedule": job_data.get("schedule", "Not scheduled"),
                            "type": job_data.get("type", "Unknown"),
                            "priority": job_data.get("priority", "Unknown"),
                            "description": job_data.get("description", ""),
                            "created_at": job_data.get("created_at"),
                            "updated_at": job_data.get("updated_at")
                        }
                except Exception as e:
                    logger.warning("Failed to get job data for %s: %s", job_id, str(e))
            
            # Get execution history from job_detail collection
            execution_history = []
            try:
                logger.info("Getting execution history for job_id: %s", job_id)
                # Get all execution records for this job
                executions = self.collection.find(
                    {"job_id": job_id}
                ).sort("execution_id", -1).limit(50).to_list(length=10000)  # Limit to last 50 executions

                logger.info("Found %d execution records for job_id: %s", len(executions_list), job_id)
                
                
                for execution in executions_list:
                    execution_record = {
                        "execution_id": execution.get("execution_id"),
                        "status": execution.get("status"),
                        "started_at": execution.get("execution_start"),  # Fixed field name
                        "completed_at": execution.get("execution_end"),   # Fixed field name
                        "duration_seconds": execution.get("duration_seconds"),
                        "error_message": execution.get("error_message"),
                        "retry_count": execution.get("retry_count", 0),
                        "service": execution.get("service"),
                        "job_type": execution.get("job_type"),
                        "success": execution.get("success", False),
                        "logs": execution.get("logs", [])
                    }
                    execution_history.append(execution_record)
                    
            except Exception as e:
                logger.warning("Failed to get execution history for %s: %s", job_id, str(e))
            
            # Calculate execution statistics
            total_executions = len(execution_history)
            successful_executions = len([e for e in execution_history if e.get("status") == "success"])
            failed_executions = len([e for e in execution_history if e.get("status") == "error"])
            running_executions = len([e for e in execution_history if e.get("status") == "running"])
            
            # Get latest execution
            latest_execution = execution_history[0] if execution_history else None
            
            # Calculate average duration
            completed_executions = [e for e in execution_history if e.get("duration_seconds") is not None]
            avg_duration = sum(e.get("duration_seconds", 0) for e in completed_executions) / len(completed_executions) if completed_executions else 0
            
            # Create execution pairs for frontend compatibility
            # Group executions by execution_id to avoid duplicates
            execution_groups = {}
            for execution in execution_history:
                execution_id = execution.get("execution_id")
                if execution_id not in execution_groups:
                    execution_groups[execution_id] = {
                        "execution_id": execution_id,
                        "start_record": None,
                        "completion_record": None
                    }
                
                # Determine if this is a start or completion record based on status and timing
                status = execution.get("status")
                started_at = execution.get("started_at")
                completed_at = execution.get("completed_at")
                
                # Check if this is a completion record (has completion time or status indicates completion)
                if completed_at or status in ["success", "error", "completed", "failed"]:
                    execution_groups[execution_id]["completion_record"] = {
                        "execution_end": completed_at or started_at,  # Use started_at as fallback
                        "status": status,
                        "success": execution.get("success", False),
                        "service": execution.get("service"),
                        "error_message": execution.get("error_message"),
                        "result_data": {},
                        "logs": execution.get("logs", [])  # Preserve logs in completion record
                    }
                    # Set start record if not already set
                    if not execution_groups[execution_id]["start_record"]:
                        execution_groups[execution_id]["start_record"] = {
                            "execution_start": started_at,
                            "status": "started",
                            "service": execution.get("service"),
                            "metadata": {"job_type": execution.get("job_type")},
                            "logs": execution.get("logs", [])  # Preserve logs in start record too
                        }
                # If it's a start record (has start time and no completion indicators)
                elif started_at and not completed_at and status not in ["success", "error", "completed", "failed"]:
                    execution_groups[execution_id]["start_record"] = {
                        "execution_start": started_at,
                        "status": status,
                        "service": execution.get("service"),
                        "metadata": {"job_type": execution.get("job_type")},
                        "logs": execution.get("logs", [])  # Preserve logs in start record
                    }
            
            # Convert groups to list of execution pairs and filter out incomplete pairs
            execution_pairs = []
            for group in execution_groups.values():
                # Only include pairs that have at least a start record
                if group["start_record"]:
                    execution_pairs.append(group)
            
            # Sort execution pairs by execution_start time (most recent first)
            execution_pairs.sort(key=lambda x: x["start_record"]["execution_start"] if x["start_record"] else "", reverse=True)
            
            logger.info("Created %d execution pairs for job_id: %s", len(execution_pairs), job_id)
            
            return {
                "job_id": job_id,
                "job_info": job_info,
                "execution_pairs": execution_pairs,
                "execution_history": execution_history,  # Keep for backward compatibility
                "statistics": {
                    "total_executions": total_executions,
                    "successful_executions": successful_executions,
                    "failed_executions": failed_executions,
                    "running_executions": running_executions,
                    "success_rate": round((successful_executions / total_executions * 100), 2) if total_executions > 0 else 0,
                    "average_duration_seconds": round(avg_duration, 2)
                },
                "latest_execution": latest_execution
            }
            
        except Exception as e:
            logger.error("Error getting job execution details for %s: %s", job_id, str(e))
            return {}
    
    def migrate_execution(self, job_detail) -> str:
        """
        Migrate an execution log to historical collection
        
        Args:
            job_detail: Job execution details to migrate
            
        Returns:
            str: ID of the migrated execution
        """
        try:
            # This would need access to historical collection
            # For now, return a placeholder
            logger.info("migrate_execution not yet fully implemented in libs")
            return "placeholder_id"
            
        except Exception as e:
            logger.error("Failed to migrate job execution: %s", str(e))
            raise
    
    def migrate_batch_executions(self, executions: List[Dict[str, Any]]) -> int:
        """
        Migrate a batch of execution logs to historical collection
        
        Args:
            executions: List of execution logs to migrate
            
        Returns:
            int: Number of executions migrated
        """
        try:
            # This would need access to historical collection
            # For now, return 0 as placeholder
            logger.info("migrate_batch_executions not yet fully implemented in libs")
            return 0
            
        except Exception as e:
            logger.error("Failed to migrate batch executions: %s", str(e))
            return 0
    
    def get_historical_executions(self, job_id: str, start_date: datetime, 
                                end_date: datetime) -> List[Dict[str, Any]]:
        """
        Get historical executions for a job within a date range
        
        Args:
            job_id: Job ID to get historical data for
            start_date: Start date for the range
            end_date: End date for the range
            
        Returns:
            List[Dict[str, Any]]: List of historical executions
        """
        try:
            # This would need access to historical collection
            # For now, return empty list as placeholder
            logger.info("get_historical_executions not yet fully implemented in libs")
            return []
            
        except Exception as e:
            logger.error("Error getting historical executions: %s", str(e))
            return []
    
    def get_job_historical_statistics(self, job_id: str, start_date: datetime, 
                                    end_date: datetime) -> Dict[str, Any]:
        """
        Get historical statistics for a job within a date range
        
        Args:
            job_id: Job ID to get statistics for
            start_date: Start date for the range
            end_date: End date for the range
            
        Returns:
            Dict[str, Any]: Historical statistics
        """
        try:
            # This would need access to historical collection
            # For now, return empty dict as placeholder
            logger.info("get_job_historical_statistics not yet fully implemented in libs")
            return {}
            
        except Exception as e:
            logger.error("Error getting job historical statistics: %s", str(e))
            return {}
    
    def get_old_historical_data_for_cleanup(self, days_old: int) -> List[Dict[str, Any]]:
        """
        Get old historical data that can be cleaned up
        
        Args:
            days_old: Number of days old the data should be
            
        Returns:
            List[Dict[str, Any]]: List of old historical data
        """
        try:
            # This would need access to historical collection
            # For now, return empty list as placeholder
            logger.info("get_old_historical_data_for_cleanup not yet fully implemented in libs")
            return []
            
        except Exception as e:
            logger.error("Error getting old historical data: %s", str(e))
            return []
    
    def cleanup_old_historical_data(self, days_old: int) -> int:
        """
        Clean up old historical data
        
        Args:
            days_old: Number of days old the data should be
            
        Returns:
            int: Number of records cleaned up
        """
        try:
            # This would need access to historical collection
            # For now, return 0 as placeholder
            logger.info("cleanup_old_historical_data not yet fully implemented in libs")
            return 0
            
        except Exception as e:
            logger.error("Error cleaning up old historical data: %s", str(e))
            return 0
    
    def get_historical_data_count(self, job_id: Optional[str] = None, 
                                days: Optional[int] = None) -> int:
        """
        Get count of historical data
        
        Args:
            job_id: Optional job ID to filter by
            days: Optional number of days to look back
            
        Returns:
            int: Count of historical data
        """
        try:
            # This would need access to historical collection
            # For now, return 0 as placeholder
            logger.info("get_historical_data_count not yet fully implemented in libs")
            return 0
            
        except Exception as e:
            logger.error("Error getting historical data count: %s", str(e))
            return 0
    
    def cleanup_old_logs(self, retention_days: int) -> int:
        """
        Clean up old logs from historical collection
        
        Args:
            retention_days: Number of days to retain logs
            
        Returns:
            int: Number of logs cleaned up
        """
        # Method added for cron_manager compatibility
        try:
            # This would need access to historical collection
            # For now, return 0 as placeholder
            logger.info("cleanup_old_logs not yet fully implemented in libs")
            return 0
            
        except Exception as e:
            logger.error("Error cleaning up old logs: %s", str(e))
            return 0
    
    def find_by_unique_key(self, job_type: str) -> Dict[str, Any]:
        """
        Find job by unique key
        
        Args:
            job_type: Job type to find
            
        Returns:
            Dict[str, Any]: Job configuration
        """
        try:
            return self.collection.find_one({"job_type": job_type})
            
        except Exception as e:
            logger.error("Error finding job by unique key: %s", str(e))
            return None
    
    def migrate_to_historical(self, migration_threshold_days: int) -> Dict[str, Any]:
        """
        Migrate old data to historical collection
        
        Args:
            migration_threshold_days: Number of days threshold for migration
            
        Returns:
            Dict[str, Any]: Migration results
        """
        # Method added for cron_manager compatibility
        try:
            # This would need access to historical collection
            # For now, return empty dict as placeholder
            logger.info("migrate_to_historical not yet fully implemented in libs")
            return {
                "migrated_count": 0,
                "migration_threshold_days": migration_threshold_days,
                "status": "placeholder"
            }
            
        except Exception as e:
            logger.error("Error migrating to historical: %s", str(e))
            return {
                "migrated_count": 0,
                "migration_threshold_days": migration_threshold_days,
                "status": "error",
                "error": str(e)
            }