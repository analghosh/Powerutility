#!/usr/bin/env python3
"""
Test script for cron job seeder and repository functionality
"""

import sys
import os
import logging
import asyncio

# Add the project root to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from services.scheduler.app.seeder.job_seeder import create_scheduler_job_seeder
from libs.core.job_factory import create_job_data_repository
from services.scheduler.app.core.config import settings

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_seeder():
    """Test the cron job seeder functionality"""
    try:
        logger.info("Starting cron job seeder test...")
        
        # Create job data repository
        job_data_repo = create_job_data_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DATA_COLLECTION
        )
        
        # Create scheduler job seeder
        scheduler_seeder = create_scheduler_job_seeder(job_data_repo)
        
        # Test seeding default jobs
        results = await scheduler_seeder.seed_default_jobs()
        logger.info("Seeding results: %s", results)
        
        # Test getting seeding status
        status = await scheduler_seeder.get_seeding_status()
        logger.info("Seeding status: %s", status)
        
        # Test getting all jobs
        all_jobs = await job_data_repo.get_all_jobs()
        logger.info("Total jobs in database: %s", len(all_jobs))
        
        # Test getting enabled jobs
        enabled_jobs = await job_data_repo.get_enabled_jobs()
        logger.info("Enabled jobs: %s", len(enabled_jobs))
        
        # Test getting specific job
        if all_jobs:
            first_job = all_jobs[0]
            job_id = first_job["job_id"]
            job = await job_data_repo.get_job(job_id)
            logger.info("Retrieved job %s: %s", job_id, job["name"] if job else "Not found")
        
        logger.info("Seeder test completed successfully!")
        return True
        
    except Exception as e:
        logger.error("Seeder test failed: %s", str(e))
        return False


if __name__ == "__main__":
    success = asyncio.run(test_seeder())
    sys.exit(0 if success else 1)
