"""
Job Execution Repository
Handles database operations for job execution monitoring
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from bson import ObjectId

from ...core.database import get_audit_database

logger = logging.getLogger(__name__)


class JobExecutionRepository:
    """Repository for job execution data access"""

    def __init__(self):
        self.audit_db = get_audit_database()
        self.job_data_collection = self.audit_db["job_data"]
        self.job_details_collection = self.audit_db["job_detail"]

    async def get_job_executions_paginated(
        self,
        filter_query: Dict[str, Any],
        skip: int,
        limit: int
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        Get paginated job executions with filters

        Returns:
            Tuple of (job_details_list, total_count)
        """
        try:
            total_count = await self.job_details_collection.count_documents(filter_query)

            cursor = self.job_details_collection.find(filter_query).sort(
                "execution_start", -1
            ).skip(skip).limit(limit)

            job_details_list = await cursor.to_list(10000)

            return job_details_list, total_count

        except Exception as e:
            logger.error(f"Error fetching paginated job executions: {e}", exc_info=True)
            raise

    async def get_job_data_by_ids(self, job_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Get job data for multiple job IDs

        Returns:
            Dictionary mapping job_id to job_data
        """
        try:
            job_data_map = {}

            if not job_ids:
                return job_data_map

            object_ids = [ObjectId(jid) for jid in job_ids if ObjectId.is_valid(jid)]

            if not object_ids:
                return job_data_map

            cursor = self.job_data_collection.find({
                "_id": {"$in": object_ids}
            })

            for job_data in await cursor.to_list(10000):
                job_data_map[str(job_data["_id"])] = job_data

            return job_data_map

        except Exception as e:
            logger.error(f"Error fetching job data by IDs: {e}", exc_info=True)
            raise

    async def get_job_execution_by_id(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get a single job execution by execution_id"""
        try:
            return await self.job_details_collection.find_one({"execution_id": execution_id})
        except Exception as e:
            logger.error(f"Error fetching job execution by ID: {e}", exc_info=True)
            raise

    async def get_job_data_by_object_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job data by ObjectId"""
        try:
            if not ObjectId.is_valid(job_id):
                return None

            return await self.job_data_collection.find_one({"_id": ObjectId(job_id)})
        except Exception as e:
            logger.error(f"Error fetching job data by ObjectId: {e}", exc_info=True)
            raise

    async def get_all_jobs(self) -> List[Dict[str, Any]]:
        """Get all job configurations"""
        try:
            cursor = self.job_data_collection.find({})
            return await cursor.to_list(10000)
        except Exception as e:
            logger.error(f"Error fetching all jobs: {e}", exc_info=True)
            raise

    async def get_latest_execution_for_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest execution for a specific job"""
        try:
            if not ObjectId.is_valid(job_id):
                return None

            return await self.job_details_collection.find_one(
                {"job_id": ObjectId(job_id)},
                sort=[("execution_start", -1)]
            )
        except Exception as e:
            logger.error(f"Error fetching latest execution for job: {e}", exc_info=True)
            raise


# Singleton instance
job_execution_repository = JobExecutionRepository()
