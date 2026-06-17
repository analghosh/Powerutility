from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import logging

from ..processors.demand_temp_sync_processor import DemandTempSyncProcessor
from ..processors.demand_serve_pipeline_processor import DemandServePipelineProcessor

router = APIRouter(
    prefix="/demand-trigger",
    tags=["Demand Trigger"]
)

logger = logging.getLogger(__name__)


@router.post("/trigger", response_model=Dict[str, Any], include_in_schema=False)
async def trigger_demand_processing():
    """
    Trigger the manual demand flow, mirroring how the scheduler cron runs it.

    Runs in strict order:
      1. DemandTempSyncProcessor       (temp_demand_serve -> demand_serve)
      2. DemandServePipelineProcessor  (IDF merge -> demand processing -> new DSM)

    The temp sync runs first; if it errors the pipeline is skipped. The pipeline
    itself stops on its first failing step.
    """
    try:
        # Step 1: demand temp sync
        logger.info("Manual demand trigger: starting demand temp sync")
        temp_sync_result = await DemandTempSyncProcessor().execute_cron_job()

        if temp_sync_result.get("status") == "error":
            logger.error("Demand temp sync failed; skipping pipeline: %s",
                         temp_sync_result.get("message"))
            raise HTTPException(
                status_code=500,
                detail=f"Demand temp sync failed: {temp_sync_result.get('message')}"
            )

        # Step 2: demand serve pipeline (IDF -> demand -> DSM)
        logger.info("Manual demand trigger: starting demand serve pipeline")
        pipeline_result = await DemandServePipelineProcessor().execute_cron_job()

        if pipeline_result.get("status") == "error":
            raise HTTPException(
                status_code=500,
                detail=f"Demand serve pipeline failed: {pipeline_result.get('message')}"
            )

        overall = "success" if pipeline_result.get("status") == "success" else "partial_success"

        return {
            "status": overall,
            "message": "Demand temp sync + serve pipeline triggered successfully",
            "data": {
                "demand_temp_sync": temp_sync_result,
                "demand_serve_pipeline": pipeline_result,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering demand flow: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to trigger demand flow: {str(e)}")
