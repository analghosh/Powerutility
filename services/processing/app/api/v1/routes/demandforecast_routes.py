from enum import Enum
from fastapi import APIRouter, Request, HTTPException, status
from typing import Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import logging
from typing import Optional


from ....controller.demandforecast_controller import demandforecast_controller

router = APIRouter(
    prefix="/processing/demandforecast",
    tags=["Demandforecast"]
)

logger = logging.getLogger(__name__)


class DemandforecastResponse(BaseModel):
    """
    Response schema for DemandforecastResponse APIs.
    """
    # message: str = Field(..., description="Success message")
    # timestamp: str = Field(..., description="ISO formatted timestamp")
    data: Dict[str, Any] = Field(..., description="Payload data")


@router.get(
    "/demand-forecast",
    response_model=DemandforecastResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Demand Forecast Data",
    response_description="Demand Forecast data with count and records"
)
async def get_data(schedule_date: Optional[str] = None):
    """
    Fetch DSM ingestion records from the database.

    Args:
        schedule_date (Optional[str]): The schedule date to filter DSM data. 
                                        Format: YYYY-MM-DD depending on your DB.

    Returns:
        DsmoverviewResponse: DSM data with metadata.
    """
    try:
        result = await demandforecast_controller.demandforecast(schedule_date)
        return DemandforecastResponse(
            # message="DSM Data Fetched Successfully.",
            # timestamp=datetime.now().isoformat(),
            data=result
        )
    except Exception as e:
        logger.exception("Error fetching DSM data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DSM data."
        )
    
@router.get(
    "/monthly-average-mape",
    response_model=DemandforecastResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Monthly Average MAPE (DAF & IDF)",
    response_description="Only the monthly average MAPE for DAF and IDF from historical data"
)
async def get_monthly_average_mape(
    month: Optional[str] = None,
    zone: Optional[str] = None
):
    """
    - month: YYYY-MM (defaults to current IST month)
    - zone: optional (e.g., 'Puducherry')
    - Source: serve_demand_table_historical
    - If the range contains today, only includes rows up to the current time block
    """
    result = await demandforecast_controller.monthly_average_mape(month, zone)
    return DemandforecastResponse(data=result)