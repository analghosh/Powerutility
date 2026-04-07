from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, Body
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
import logging
from datetime import datetime, timedelta

from ..processors.rtm_scheduling_processor import RTMSchedulingProcessor
from libs.middleware import get_user_context

router = APIRouter(
    prefix="/rtm-scheduling",
    tags=["RTM Scheduling"]
)

logger = logging.getLogger(__name__)

class TriggerSchedulingRequest(BaseModel):
    schedule_date: Optional[str] = Field(None, description="Schedule date in YYYY-MM-DD format. Defaults to yesterday if not provided.")
    run_with_exchange: bool = Field(False, description="When True, writes computed sell/buy bids back to exchange_serve after scheduling.")

@router.post("/trigger", response_model=Dict[str, Any], include_in_schema=False)
async def trigger_rtm_scheduling(
    request: TriggerSchedulingRequest = Body(...)
):
    """
    Trigger RTM scheduling calculation.

    Args:
        schedule_date: Optional schedule date in YYYY-MM-DD format. Defaults to yesterday if not provided.
        run_with_exchange: When True, writes computed sell/buy bids back to exchange_serve.
    """
    try:
        schedule_date = request.schedule_date
        run_with_exchange = request.run_with_exchange

        # Default to yesterday if no date provided (matches cron job behavior)
        if not schedule_date:
            schedule_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        logger.info(f"Triggering RTM scheduling for date: {schedule_date}, run_with_exchange={run_with_exchange}")

        # Initialize processor
        processor = RTMSchedulingProcessor()

        # Run calculation
        result = await processor.calSchedulingTableData(schedule_date, run_with_exchange=run_with_exchange)
        
        return {
            "status": "success",
            "message": f"RTM scheduling calculation completed successfully for {schedule_date}",
            "data": result
        }
        
    except Exception as e:
        logger.error(f"Error triggering RTM scheduling: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to trigger RTM scheduling: {str(e)}")
