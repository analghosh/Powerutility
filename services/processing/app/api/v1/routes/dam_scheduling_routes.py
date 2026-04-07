from fastapi import APIRouter, Query, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List
from datetime import datetime
import logging

from ....controller.dam_scheduling_controller import dam_scheduling_controller

router = APIRouter(
    prefix="/processing",
    tags=["DAM Scheduling"]
)

logger = logging.getLogger(__name__)


# ----------- Schema models (Swagger-friendly) -----------

class PlantDataOrder(BaseModel):
    px_price: float = Field(..., description="PX price for the plant")
    plant_id: str = Field(..., description="Plant ID")
    category: str = Field(..., description="Plant category")
    plant_name: str = Field(..., description="Plant name")
    volume: float = Field(..., description="Scheduled volume (MW)")

class PlantData(BaseModel):
    forecast: float = Field(..., description="Forecast value")
    total_supply: float = Field(..., description="Total supply")
    surplus: float = Field(..., description="Surplus (total_supply - forecast)")
    deficit: float = Field(..., description="Deficit")
    bid_volume: float = Field(..., description="Bid volume (total_supply - forecast)")
    remaining_volume: float = Field(..., description="Remaining volume (same as bid_volume)")
    updated_volume: float = Field(..., description="Updated volume")
    dam_forecasted_price: float = Field(..., description="DAM forecasted price from limit_values collection")
    updated_total_supply: float = Field(..., description="Updated total supply (cumulative volume of all plants)")
    demand_forecast: float = Field(..., description="Demand forecast")
    plant_data_order: List[PlantDataOrder] = Field(..., description="Plant data ordered by px_price (descending)")

class TimeBlockData(BaseModel):
    time_block: int = Field(..., ge=1, le=96, description="15-min block number (1..96)")
    plant_data: PlantData = Field(..., description="Plant data for this time block")

class DamScheduleResponse(BaseModel):
    timestamp: str = Field(..., description="Response timestamp")
    data: List[TimeBlockData] = Field(..., description="Array of time block data")

    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2025-01-27T10:30:00.000Z",
                "data": [
                    {
                        "time_block": 1,
                        "plant_data": {
                            "forecast": 1000.0,
                            "total_supply": 1200.0,
                            "surplus": 200.0,
                            "deficit": 0.0,
                            "bid_volume": 200.0,
                            "remaining_volume": 200.0,
                            "updated_volume": 1200.0,
                            "dam_forecasted_price": 3.5,
                            "updated_total_supply": 1200.0,
                            "demand_forecast": 1000.0,
                            "plant_data_order": [
                                {
                                    "px_price": 4.5,
                                    "plant_id": "PLANT001",
                                    "category": "Thermal",
                                    "plant_name": "Plant 1",
                                    "volume": 500.0
                                },
                                {
                                    "px_price": 3.8,
                                    "plant_id": "PLANT002",
                                    "category": "Solar",
                                    "plant_name": "Plant 2",
                                    "volume": 300.0
                                }
                            ]
                        }
                    }
                ]
            }
        }

class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error message")

    class Config:
        json_schema_extra = {"example": {"detail": "Failed to fetch fresh DAM schedule."}}


# ----------------------------- Routes -----------------------------

@router.get(
    "/dam-scheduling",
    response_model=DamScheduleResponse,
    responses={
        200: {"description": "OK", "model": DamScheduleResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="DAM schedule for a date",
    description=(
        "Returns DAM scheduling data with the following structure:\n\n"
        "```json\n"
        "{\n"
        "  \"data\": [\n"
        "    {\n"
        "      \"time_block\": 1,\n"
        "      \"plant_data\": {\n"
        "        \"forecast\": <float>,\n"
        "        \"total_supply\": <float>,\n"
        "        \"surplus\": <float>,\n"
        "        \"deficit\": <float>,\n"
        "        \"bid_volume\": <float>,\n"
        "        \"remaining_volume\": <float>,\n"
        "        \"updated_volume\": <float>,\n"
        "        \"dam_forecasted_price\": <float>,\n"
        "        \"updated_total_supply\": <float>,\n"
        "        \"demand_forecast\": <float>,\n"
        "        \"plant_data_order\": [\n"
        "          {\n"
        "            \"px_price\": <float>,\n"
        "            \"plant_id\": <string>,\n"
        "            \"category\": <string>,\n"
        "            \"plant_name\": <string>,\n"
        "            \"volume\": <float>\n"
        "          }\n"
        "        ]\n"
        "      }\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        "- **Time zone**: input `schedule_date` is interpreted in **IST (UTC+05:30)**.\n"
        "- **Default date**: If no date provided, defaults to **tomorrow's date**.\n"
        "- **Blocks**: The `data` array spans **1..96** blocks (15-minute intervals).\n"
        "- **Plant ordering**: Plants are ordered by `px_price` in descending order.\n"
        "- **Precision**: All numeric values are rounded to **2 decimal places**.\n"
        "- **Data sources**: \n"
        "  - Plant data from `serve_dam_scheduling` collection\n"
        "  - Plant details from `plant_master` collection\n"
        "  - Forecast data from `serve_demand_table` collection (type='DAF')\n"
        "  - DAM price from `limit_values` collection (latest date available)"
    ),
    operation_id="getDamScheduleByDate",
    response_model_exclude_none=False,
)
async def dam_schedule_by_date(
    schedule_date: Optional[str] = Query(
        None,
        description="Schedule date in YYYY-MM-DD (IST). Defaults to tomorrow's date if omitted.",
        examples={
            "tomorrow": {"summary": "Use tomorrow's date (default in service)", "value": None},
            "custom": {"summary": "Specific date", "value": "2025-01-27"},
        },
        regex=r"^\d{4}-\d{2}-\d{2}$",
    ),
):
    """
    Get DAM scheduling data for a given date.
    
    This endpoint provides comprehensive DAM scheduling information including:
    - Time block data (1-96 blocks)
    - Plant data with pricing and volume information
    - Forecast and supply calculations
    - Plant ordering by PX price (descending)
    """
    try:
        payload = dam_scheduling_controller.schedule_by_date(schedule_date)
        return DamScheduleResponse(
            timestamp=datetime.now().isoformat(),
            data=payload["data"],
        )
    except HTTPException as e:
        raise e
    except Exception:
        logger.exception("Error fetching DAM schedule by date")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DAM schedule.",
        )
