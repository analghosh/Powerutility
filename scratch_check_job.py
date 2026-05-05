import asyncio
import os
import sys

# Add project root to path
sys.path.append(r"E:\demand_optimization")

from services.scheduler.app.core.config import settings
from libs.core.job_factory import create_job_data_repository

async def check_job():
    print(f"Connecting to {settings.AUDIT_DB_URI}")
    repo = create_job_data_repository(
        settings.AUDIT_DB_URI,
        settings.AUDIT_DB_NAME,
        settings.JOB_DATA_COLLECTION
    )
    job = await repo.get_job("bid_trade_live_sync")
    print(f"Job data: {job}")
    
    jobs = await repo.get_all_jobs(enabled_only=True)
    enabled_job_types = [j['job_type'] for j in jobs]
    print(f"Enabled jobs: {enabled_job_types}")

if __name__ == "__main__":
    asyncio.run(check_job())
