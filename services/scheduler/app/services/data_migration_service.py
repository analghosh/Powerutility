"""
Data Migration Service
Handles data lifecycle management between active and historical collections
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import time

from libs.core.job_factory import create_job_detail_repository
from ..core.config import settings

# Create job detail repository instance
job_detail_repository = create_job_detail_repository(
    settings.AUDIT_DB_URI,
    settings.AUDIT_DB_NAME,
    settings.JOB_DETAIL_COLLECTION
)

logger = logging.getLogger(__name__)


class DataMigrationService:
    """Service for managing data lifecycle between collections"""
    
    def __init__(self):
        """Initialize the data migration service"""
        self.migration_threshold_days = settings.JOB_LOGS_MIGRATION_THRESHOLD_DAYS
        self.historical_retention_days = settings.JOB_LOGS_HISTORICAL_RETENTION_DAYS
        self.batch_size = settings.JOB_LOGS_MIGRATION_BATCH_SIZE
        self.migration_timeout = settings.JOB_LOGS_MIGRATION_TIMEOUT
    
    async def migrate_old_executions_to_historical(self) -> Dict[str, Any]:
        """
        Migrate old execution logs from active to historical collection
        
        Returns:
            Dictionary with migration results
        """
        start_time = time.time()
        total_migrated = 0
        total_deleted = 0
        errors = []
        
        try:
            logger.info("Starting data migration from active to historical collection")
            
            # Get old executions for migration
            old_executions = await job_detail_repository.get_old_executions_for_migration(
                self.migration_threshold_days
            )
            
            if not old_executions:
                logger.info("No old executions found for migration")
                return {
                    "success": True,
                    "migrated_count": 0,
                    "deleted_count": 0,
                    "errors": [],
                    "duration_seconds": time.time() - start_time
                }
            
            logger.info("Found %s old executions to migrate", len(old_executions))
            
            # Migrate in batches
            for i in range(0, len(old_executions), self.batch_size):
                batch = old_executions[i:i + self.batch_size]
                
                try:
                    # Migrate batch to historical collection
                    migrated_count = job_detail_repository.migrate_batch_executions(batch)
                    total_migrated += migrated_count
                    
                    # Delete migrated records from active collection
                    execution_ids = [execution["execution_id"] for execution in batch]
                    deleted_count = self._delete_migrated_executions(execution_ids)
                    total_deleted += deleted_count
                    
                    logger.info("Migrated batch %s: %s records migrated, %s records deleted", 
                               i // self.batch_size + 1, migrated_count, deleted_count)
                    
                    # Check timeout
                    if time.time() - start_time > self.migration_timeout:
                        logger.warning("Migration timeout reached, stopping migration")
                        break
                        
                except Exception as e:
                    error_msg = f"Failed to migrate batch {i // self.batch_size + 1}: {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)
            
            duration = time.time() - start_time
            logger.info("Data migration completed: %s migrated, %s deleted, %s errors, %.2f seconds", 
                       total_migrated, total_deleted, len(errors), duration)
            
            return {
                "success": len(errors) == 0,
                "migrated_count": total_migrated,
                "deleted_count": total_deleted,
                "errors": errors,
                "duration_seconds": duration
            }
            
        except Exception as e:
            error_msg = f"Data migration failed: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "migrated_count": total_migrated,
                "deleted_count": total_deleted,
                "errors": [error_msg],
                "duration_seconds": time.time() - start_time
            }
    
    def _delete_migrated_executions(self, execution_ids: List[str]) -> int:
        """
        Delete migrated executions from active collection
        
        Args:
            execution_ids: List of execution IDs to delete
            
        Returns:
            int: Number of deleted records
        """
        try:
            result = job_detail_repository.collection.delete_many({
                "execution_id": {"$in": execution_ids}
            })
            return result.deleted_count
        except Exception as e:
            logger.error("Failed to delete migrated executions: %s", str(e))
            return 0
    
    async def cleanup_old_historical_data(self) -> Dict[str, Any]:
        """
        Clean up old historical data beyond retention period
        
        Returns:
            Dictionary with cleanup results
        """
        start_time = time.time()
        
        try:
            logger.info("Starting cleanup of old historical data")
            
            # Get count of old data
            old_data = job_detail_repository.get_old_historical_data_for_cleanup(
                self.historical_retention_days
            )
            
            if not old_data:
                logger.info("No old historical data found for cleanup")
                return {
                    "success": True,
                    "deleted_count": 0,
                    "duration_seconds": time.time() - start_time
                }
            
            logger.info("Found %s old historical records for cleanup", len(old_data))
            
            # Clean up old data
            deleted_count = job_detail_repository.cleanup_old_historical_data(
                self.historical_retention_days
            )
            
            duration = time.time() - start_time
            logger.info("Historical data cleanup completed: %s deleted, %.2f seconds", 
                       deleted_count, duration)
            
            return {
                "success": True,
                "deleted_count": deleted_count,
                "duration_seconds": duration
            }
            
        except Exception as e:
            error_msg = f"Historical data cleanup failed: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "deleted_count": 0,
                "errors": [error_msg],
                "duration_seconds": time.time() - start_time
            }
    
    async def get_migration_status(self) -> Dict[str, Any]:
        """
        Get current migration status and statistics
        
        Returns:
            Dictionary with migration status
        """
        try:
            # Get counts from both collections
            active_count = await job_detail_repository.get_execution_count()
            historical_count = job_detail_repository.get_historical_data_count()
            
            # Get old data counts
            old_active_count = len(await job_detail_repository.get_old_executions_for_migration(
                self.migration_threshold_days
            ))
            
            old_historical_count = len(job_detail_repository.get_old_historical_data_for_cleanup(
                self.historical_retention_days
            ))
            
            return {
                "active_collection": {
                    "total_records": active_count,
                    "old_records_ready_for_migration": old_active_count
                },
                "historical_collection": {
                    "total_records": historical_count,
                    "old_records_ready_for_cleanup": old_historical_count
                },
                "migration_config": {
                    "migration_threshold_days": self.migration_threshold_days,
                    "historical_retention_days": self.historical_retention_days,
                    "batch_size": self.batch_size,
                    "migration_timeout": self.migration_timeout
                }
            }
            
        except Exception as e:
            logger.error("Failed to get migration status: %s", str(e))
            return {
                "error": str(e),
                "active_collection": {"total_records": 0, "old_records_ready_for_migration": 0},
                "historical_collection": {"total_records": 0, "old_records_ready_for_cleanup": 0},
                "migration_config": {
                    "migration_threshold_days": self.migration_threshold_days,
                    "historical_retention_days": self.historical_retention_days,
                    "batch_size": self.batch_size,
                    "migration_timeout": self.migration_timeout
                }
            }
    
    async def run_full_data_lifecycle(self) -> Dict[str, Any]:
        """
        Run complete data lifecycle management (migration + cleanup)
        
        Returns:
            Dictionary with complete lifecycle results
        """
        logger.info("Starting full data lifecycle management")
        
        # Step 1: Migrate old data to historical
        migration_result = await self.migrate_old_executions_to_historical()
        
        # Step 2: Clean up old historical data
        cleanup_result = await self.cleanup_old_historical_data()
        
        # Combine results
        total_duration = migration_result.get("duration_seconds", 0) + cleanup_result.get("duration_seconds", 0)
        
        return {
            "success": migration_result.get("success", False) and cleanup_result.get("success", False),
            "migration": migration_result,
            "cleanup": cleanup_result,
            "total_duration_seconds": total_duration
        }


# Global service instance
data_migration_service = DataMigrationService()
