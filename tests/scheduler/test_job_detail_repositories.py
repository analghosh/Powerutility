#!/usr/bin/env python3
"""
Test script for Phase 2: Job Detail Repository (Time Series)
Tests all components including repositories and data migration service
"""

import sys
import os
import logging
import asyncio
from datetime import datetime, timedelta

# Add the project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# Global imports
from libs.core.job_factory import create_job_detail_repository
from libs.core.job_entities import JobDetail, JobDetailQuery
from services.scheduler.app.core.config import settings
from services.scheduler.app.services.data_migration_service import data_migration_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def test_job_detail_repository():
    """Test JobDetailRepository functionality"""
    logger.info("Testing JobDetailRepository...")
    
    try:
        job_detail_repository = create_job_detail_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DETAIL_COLLECTION
        )
        
        # Test creating a job execution log
        job_detail = JobDetail(
            job_id="test_job_001",
            job_name="Test Job",
            execution_id="exec_001",
            status="running",
            success=False,  # Required field, can't be None
            execution_start=datetime.now(),
            execution_end=None,
            duration_seconds=None,
            service="scheduler",
            error_message="Test execution started",
            error_details=None,
            result_data=None,
            metadata={"test": True}
        )
        
        # Log execution
        execution_id = await job_detail_repository.log_execution(job_detail)
        logger.info("Created execution log with ID: %s", execution_id)
        
        # Test updating execution
        update_data = {
            "status": "completed",
            "success_flag": True,
            "execution_end": datetime.now(),
            "duration_seconds": 5.5,
            "error_message": "Test execution completed successfully"
        }
        
        update_success = await job_detail_repository.update_execution(job_detail.execution_id, update_data)
        logger.info("Updated execution: %s", update_success)
        
        # Test getting execution by ID
        execution = await job_detail_repository.get_execution_by_id(job_detail.execution_id)
        logger.info("Retrieved execution: %s", execution is not None)
        
        # Test getting job statistics
        stats = await job_detail_repository.get_job_statistics("test_job_001", days=1)
        logger.info("Job statistics: %s", stats)
        
        # Test getting execution count
        count = await job_detail_repository.get_execution_count(job_id="test_job_001")
        logger.info("Execution count: %s", count)
        
        logger.info("✅ JobDetailRepository tests passed")
        return True
        
    except Exception as e:
        logger.error("❌ JobDetailRepository tests failed: %s", str(e))
        return False

async def test_job_detail_historical_repository():
    """Test JobDetailHistoricalRepository functionality"""
    logger.info("Testing JobDetailHistoricalRepository...")
    
    try:
        job_detail_historical_repository = create_job_detail_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DETAIL_COLLECTION
        )
        
        # Test migrating a job execution to historical
        job_detail = JobDetail(
            job_id="test_job_002",
            job_name="Test Historical Job",
            execution_id="exec_002",
            status="completed",
            success=True,
            execution_start=datetime.now() - timedelta(days=10),
            execution_end=datetime.now() - timedelta(days=10, seconds=5),
            duration_seconds=5.0,
            service="scheduler",
            error_message=None,
            error_details=None,
            result_data={"historical": True},
            metadata={"historical": True}
        )
        
        # Migrate to historical
        historical_id = await job_detail_historical_repository.migrate_execution(job_detail)
        logger.info("Migrated to historical with ID: %s", historical_id)
        
        # Test getting historical execution
        historical_execution = await job_detail_historical_repository.get_execution_by_id(job_detail.execution_id)
        logger.info("Retrieved historical execution: %s", historical_execution is not None)
        
        # Test getting historical statistics
        start_date = datetime.now() - timedelta(days=30)
        end_date = datetime.now()
        stats = job_detail_historical_repository.get_job_historical_statistics(
            "test_job_002", start_date, end_date
        )
        logger.info("Historical statistics: %s", stats)
        
        # Test getting historical data count
        count = job_detail_historical_repository.get_historical_data_count(job_id="test_job_002")
        logger.info("Historical data count: %s", count)
        
        logger.info("✅ JobDetailHistoricalRepository tests passed")
        return True
        
    except Exception as e:
        logger.error("❌ JobDetailHistoricalRepository tests failed: %s", str(e))
        return False

async def test_data_migration_service():
    """Test DataMigrationService functionality"""
    logger.info("Testing DataMigrationService...")
    
    try:
        
        # Test getting migration status
        status = await data_migration_service.get_migration_status()
        logger.info("Migration status: %s", status)
        
        # Test migration (should be empty since we don't have old data)
        migration_result = await data_migration_service.migrate_old_executions_to_historical()
        logger.info("Migration result: %s", migration_result)
        
        # Test cleanup (should be empty since we don't have old historical data)
        cleanup_result = await data_migration_service.cleanup_old_historical_data()
        logger.info("Cleanup result: %s", cleanup_result)
        
        # Test full lifecycle
        lifecycle_result = await data_migration_service.run_full_data_lifecycle()
        logger.info("Full lifecycle result: %s", lifecycle_result)
        
        logger.info("✅ DataMigrationService tests passed")
        return True
        
    except Exception as e:
        logger.error("❌ DataMigrationService tests failed: %s", str(e))
        return False

async def test_query_functionality():
    """Test query functionality with JobDetailQuery"""
    logger.info("Testing query functionality...")
    
    try:
        job_detail_repository = create_job_detail_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DETAIL_COLLECTION
        )
        
        # Test query with filters
        query = JobDetailQuery(
            job_id="test_job_001",
            status="completed",
            start_date=datetime.now() - timedelta(days=1),
            end_date=datetime.now(),
            skip=0,
            limit=10
        )
        
        logs = await job_detail_repository.get_execution_logs(query)
        logger.info("Query logs count: %s", len(logs))
        
        # Test getting job execution history
        history = await job_detail_repository.get_job_execution_history("test_job_001", limit=5)
        logger.info("Job history count: %s", len(history))
        
        # Test getting recent executions
        recent = await job_detail_repository.get_recent_executions("test_job_001")
        logger.info("Recent executions result: %s", recent)
        
        # Test getting failed executions
        failed = await job_detail_repository.get_failed_executions(days=7)
        logger.info("Failed executions result: %s", failed)
        
        logger.info("✅ Query functionality tests passed")
        return True
        
    except Exception as e:
        logger.error("❌ Query functionality tests failed: %s", str(e))
        return False

def main():
    """Run all Phase 2 tests"""
    logger.info("🚀 Starting Phase 2: Job Detail Repository (Time Series) Tests")
    
    tests = [
        ("JobDetailRepository", test_job_detail_repository),
        ("JobDetailHistoricalRepository", test_job_detail_historical_repository),
        ("DataMigrationService", test_data_migration_service),
        ("Query Functionality", test_query_functionality)
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        logger.info("=" * 60)
        logger.info("Testing: %s", test_name)
        logger.info("=" * 60)
        
        try:
            if asyncio.run(test_func()):
                passed += 1
                logger.info("✅ %s: PASSED", test_name)
            else:
                logger.error("❌ %s: FAILED", test_name)
        except Exception as e:
            logger.error("❌ %s: FAILED with exception: %s", test_name, str(e))
    
    logger.info("=" * 60)
    logger.info("📊 Test Results: %s/%s tests passed", passed, total)
    logger.info("=" * 60)
    
    if passed == total:
        logger.info("🎉 All Phase 2 tests passed! Ready for Phase 3.")
        return True
    else:
        logger.error("💥 Some Phase 2 tests failed. Please fix issues before proceeding.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
