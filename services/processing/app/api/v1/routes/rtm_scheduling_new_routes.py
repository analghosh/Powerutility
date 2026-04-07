# api/v1/routes/processing/rtm_scheduling_routes.py
from fastapi import APIRouter, Query, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List
from datetime import datetime
import logging

from ....controller.rtm_scheduling_controller_new import rtm_scheduling_controller

router = APIRouter(
    prefix="/processing",
    tags=["RTM Scheduling (New)"],
)

logger = logging.getLogger(__name__)


# ----------- Schema models (Swagger-friendly) -----------

class RtmPlantData(BaseModel):
    plant_id: str = Field(..., description="Plant identifier")
    plant_name: str = Field(..., description="Plant name from supply table")
    category: Optional[str] = Field(None, description="Plant category (from supply, fallback plant_master)")
    px_price: Optional[float] = Field(None, description="PX price from plant_master (fallback supply)")
    scheduled_volume: float = Field(..., description="Scheduled volume (MW) in this block")

class RtmBlockData(BaseModel):
    forecast: float
    total_supply: float
    surplus_defecit: float
    remaining_volume: float
    updated_volume: float
    rtm_forecasted_price: Optional[float] = None
    bid_volume: Optional[float] = None

class RtmBlock(BaseModel):
    time_block: int = Field(..., ge=1, le=96)
    data: RtmBlockData
    plant_data: List[RtmPlantData]

class RtmSchedulingPayload(BaseModel):
    result: List[RtmBlock]

class RtmSchedulingResponse(BaseModel):
    timestamp: str = Field(..., description="ISO formatted timestamp (UTC)")
    data: RtmSchedulingPayload

    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2025-10-08T04:40:00.000000",
                "data": {
                    "result": [
                        {
                            "time_block": 1,
                            "data": {
                                "forecast": 415.55,
                                "total_supply": 7.84,
                                "surplus_defecit": -407.71,
                                "remaining_volume": -407.71,
                                "updated_volume": -407.71,
                                "rtm_forecasted_price": 10000,
                                "bid_volume": 3300.96
                            },
                            "plant_data": [
                                {
                                    "plant_id": "NLCEXP",
                                    "plant_name": "TPS-1 Expansion/NLC India Limited",
                                    "px_price": 16,
                                    "scheduled_volume": 7.84
                                }
                            ]
                        }
                    ]
                }
            }
        }

class ErrorResponse(BaseModel):
    detail: Any

    class Config:
        json_schema_extra = {"example": {"detail": "Failed to fetch RTM schedule."}}


# ----------------------------- Routes -----------------------------

@router.get(
    "/rtm-scheduling",
    response_model=RtmSchedulingResponse,
    responses={
        200: {"description": "OK", "model": RtmSchedulingResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="RTM schedule view up to current IST block",
    description=(
        "Builds the RTM view per 15-minute block **up to the current IST block**.\n"
        "- Demand from `serve_demand_table` with `type: IDF` using `comp_act_demand` as forecast.\n"
        "- Supply from `serve_supply_table` where `dc_sg: 'SG'` (sum volume per block) + plant list.\n"
        "- Exchange price from `serve_exchange_table` where `market: 'RTM'` and `exchange: 'IEX'` (MCP).\n"
        "- `bid_volume` chosen from `limit_values`: `noc_buy` if deficit, else `noc_sell`.\n"
        "- `schedule_date` is interpreted in **IST (UTC+05:30)**. Defaults to today if omitted."
    ),
    operation_id="getRtmSchedulingByDate",
    response_model_exclude_none=False,
)
async def rtm_schedule_by_date(
    schedule_date: Optional[str] = Query(
        None,
        description="Schedule date in YYYY-MM-DD (IST). Defaults to today's date if omitted.",
        examples={
            "today": {"summary": "Use today's date (default in service)", "value": None},
            "custom": {"summary": "Specific date", "value": "2025-10-09"},
        },
        regex=r"^\d{4}-\d{2}-\d{2}$",
    ),
):
    """
    Returns the RTM scheduling view for the given date, up to the current 15-min IST block.
    """
    try:
        payload = rtm_scheduling_controller.rtm_by_date(schedule_date)
        return RtmSchedulingResponse(
            timestamp=datetime.now().isoformat(),
            data=payload,
        )
    except HTTPException as e:
        raise e
    except Exception:
        logger.exception("Error fetching RTM schedule by date")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch RTM schedule.",
        )