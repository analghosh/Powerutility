import asyncio
import os
import sys

# Add project root to path
sys.path.append(r"E:\demand_optimization")

from services.scheduler.app.core.config import settings
from libs.core.job_factory import create_job_data_repository

async def enable_job():
    print(f"Connecting to {settings.AUDIT_DB_URI}")
    repo = create_job_data_repository(
        settings.AUDIT_DB_URI,
        settings.AUDIT_DB_NAME,
        settings.JOB_DATA_COLLECTION
    )
    job = await repo.get_job("bid_trade_live_sync")
    print(f"Current job data: {job}")
    
    if job and not job.get("enabled"):
        print("Enabling job...")
        await repo.collection.update_one(
            {"job_type": "bid_trade_live_sync"},
            {"$set": {"enabled": True}}
        )
        print("Job enabled successfully. The scheduler will pick it up on the next reload or restart.")
    else:
        print("Job is already enabled or not found.")

if __name__ == "__main__":
    asyncio.run(enable_job())
