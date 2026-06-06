from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import logging

from ..processors.demand_temp_sync_processor import DemandTempSyncProcessor
from ..processors.demand_processor import DemandProcessor

router = APIRouter(
    prefix="/demand-trigger",
    tags=["Demand Trigger"]
)

logger = logging.getLogger(__name__)


@router.post("/trigger", response_model=Dict[str, Any], include_in_schema=False)
async def trigger_demand_processing():
    """
    Trigger the demand pipeline manually, mirroring how the scheduler cron runs it.

    Runs two processors sequentially via their cron entry points:
      1. DemandTempSyncProcessor.execute_cron_job()  (temp_demand_serve -> demand_serve)
      2. DemandProcessor.execute_cron_job()          (raw demand -> demand_serve)

    Step 2 runs only if step 1 does not error, so the demand processing always
    works on freshly synced temp data.
    """
    try:
        logger.info("Manual demand trigger: starting demand temp sync")
        temp_sync_result = await DemandTempSyncProcessor().execute_cron_job()

        if temp_sync_result.get("status") == "error":
            logger.error("Demand temp sync failed; skipping demand processing: %s",
                         temp_sync_result.get("message"))
            raise HTTPException(
                status_code=500,
                detail=f"Demand temp sync failed: {temp_sync_result.get('message')}"
            )

        logger.info("Manual demand trigger: starting demand data processing")
        demand_result = await DemandProcessor().execute_cron_job()

        overall = "success" if demand_result.get("status") == "success" else "partial_success"

        return {
            "status": overall,
            "message": "Demand pipeline triggered successfully",
            "data": {
                "demand_temp_sync": temp_sync_result,
                "demand_processing": demand_result,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering demand pipeline: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to trigger demand pipeline: {str(e)}")
