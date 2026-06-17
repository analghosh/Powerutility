"""
Cron Orchestrator Routes
Exposes a single POST endpoint that triggers all registered crons across
the ingestion and scheduler services and waits for all of them to complete.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends

from ...dependencies import get_user_context
from ...services.cron_orchestrator_service import cron_orchestrator_service
from typing import Dict, Any

router = APIRouter(prefix="/cron", tags=["Cron Orchestrator"])
logger = logging.getLogger(__name__)


@router.post(
    "/trigger-all",
    status_code=status.HTTP_200_OK,
    summary="Trigger all cron jobs across all services",
    description="""
    Triggers every registered cron job in the **ingestion** service and the
    **scheduler** service in sequence, waiting for all HTTP trigger calls to
    complete before returning.

    Order:
    1. Fetches job list from ingestion (`GET /cron/status`) and triggers each one.
    2. Fetches job list from scheduler (`GET /scheduler/jobs/all`) and triggers each one.

    Returns a breakdown of how many jobs were found and triggered per service.
    """,
)
async def trigger_all_crons(
    user_context: Dict[str, Any] = Depends(get_user_context)
) -> Dict[str, Any]:
    user_id = user_context.get("user_id", "anonymous")
    logger.info(f"User '{user_id}' triggered all cron jobs")

    try:
        result = await cron_orchestrator_service.trigger_all()
        return {
            "data": result,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.exception("Error in trigger_all_crons")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger all crons: {str(e)}"
        )
