from enum import Enum
from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import HTMLResponse
from typing import Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import plotly.express as px
import logging

from ....controller.dashboard_controller import dashboard_controller
from ....controller.dashboard_pg_controller import dashboard_pg_controller
from ....controller.dashboard_new import dashboard_pg_new_controller


router = APIRouter(
    prefix="/processing/dashboard",
    tags=["Dashboard"]
)

logger = logging.getLogger(__name__)


class DashboardResponse(BaseModel):
    """
    Response schema for Dashboard APIs.
    """
    # message: str = Field(..., description="Success message")
    # timestamp: str = Field(..., description="ISO formatted timestamp")
    data: Dict[str, Any] = Field(..., description="Payload data")


@router.get(
    "/demand",
    response_model=DashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Demand Data",
    response_description="Demand data with count and records"
)
async def demand_data():
    """
    Fetch demand ingestion records from the database.

    Returns:
        DashboardResponse: Demand data with metadata.
    """
    try:
        result = await dashboard_controller.demand()
        return DashboardResponse(
            # message="Demand Data Fetched Successfully.",
            # timestamp=datetime.now().isoformat(),
            data=result
        )
    except Exception as e:
        logger.exception("Error fetching demand data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch demand data."
        )
    

@router.get(
    "/visits",
    response_model=DashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Demand Data",
    response_description="Demand data with count and records"
)
async def dashboard_api(request: Request):
    """
    Fetch demand ingestion records from the database.
    """
    try:
        schedule_date = "2025-07-27"
        # request.scope["query_string"] = f"schedule_date={schedule_date}".encode()
        result = dashboard_pg_controller.dashboard(request= request,)
        return DashboardResponse(
            # message="Demand Data Fetched Successfully.",
            # timestamp=datetime.now().isoformat(),
            data=result
        )
    except Exception as e:
        logger.exception("Error fetching demand data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch demand data."
        )
    
# @router.get("/capacity-chart")
# def get_capacity_chart(request: Request):
#     return dashboard_pg_controller.dashboard_capacity_chart(request)



@router.get("/capacity-chart", summary="Get Power Plant Capacity Sunburst Chart Data")
async def get_capacity_chart():
    """
    API endpoint to retrieve the hierarchical data for the power plant capacity sunburst chart.
    """
    return await dashboard_pg_new_controller.dashboard_capacity_chart()


@router.get(
    "/home",
    response_model=DashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Dashboard Data (Demand, Frequency, OD-UD, MCP, etc.)",
    description="""
    Fetches **dashboard insights** for the Power Grid system including:

    - 🔹 Demand (Actual & Forecast) upto current_block show Actual and after current_block shows Forecast
    - 🔹 Frequency trends (last 24 blocks)
    - 🔹 OD-UD (Over/Under Drawal)
    - 🔹 MCP (Market Clearing Price) for RTM & DAM
    - 🔹 Scheduled Drawal (InterState & IntraState)
    - 🔹 Peak Demand (last 31 days)

    ### Notes:
    - `schedule_date` is required in `YYYY-MM-DD` format.
    - Current block is auto-detected from IST.
    """,
    response_description="Returns structured dashboard data for visualization.",
    responses={
        404: {"description": "Data not found for given schedule_date or block"},
        500: {"description": "Internal Server Error - Failed to fetch demand data"},
    },
)
async def dashboard_api_new(request: Request):
    """
    Fetch demand ingestion records from the database.
    """
    try:
        schedule_date = "2025-07-27"
        # request.scope["query_string"] = f"schedule_date={schedule_date}".encode()
        result = await dashboard_pg_new_controller.dashboard(request=request)
        return DashboardResponse(
            # message="Demand Data Fetched Successfully.",
            # timestamp=datetime.now().isoformat(),
            data=result
        )
    except Exception as e:
        logger.exception("Error fetching demand data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch demand data."
        )
    

