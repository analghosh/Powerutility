"""Trigger IDF forecast merge (forecast_serve -> demand_serve) on demand."""

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from ..processors.idf_forecast_merge_processor import IdfForecastMergeProcessor

router = APIRouter(
    prefix="/idf-forecast-merge",
    tags=["IDF Forecast Merge"],
)

logger = logging.getLogger(__name__)


@router.post("/trigger", response_model=Dict[str, Any], include_in_schema=False)
async def trigger_idf_forecast_merge():
    """
    Run IDF forecast merge only (no demand processing or DSM).

    Reads idf_forecast_switch and publishes the active forecast source from
    forecast_serve into demand_serve IDF records.
    """
    try:
        logger.info("Manual IDF forecast merge trigger: starting run_merge")
        merged, skipped, failed = await IdfForecastMergeProcessor().run_merge()
        status = "success" if failed == 0 else "partial"
        return {
            "status": status,
            "message": (
                f"IDF forecast merge — merged={merged}, skipped={skipped}, failed={failed}"
            ),
            "merged": merged,
            "skipped": skipped,
            "failed": failed,
        }
    except Exception as e:
        logger.error("Error triggering IDF forecast merge: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger IDF forecast merge: {str(e)}",
        ) from e
