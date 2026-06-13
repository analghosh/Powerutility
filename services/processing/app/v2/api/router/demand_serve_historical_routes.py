"""
Demand Serve Historical API — read demand_serve_historical by date and time block range.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ...controller.demand_serve_historical_controller import (
    demand_serve_historical_controller,
)
from ...dependencies import get_user_context

router = APIRouter(
    prefix="/demand-serve-historical",
    tags=["Demand Serve Historical"],
)

logger = logging.getLogger(__name__)


class DemandServeHistoricalRecord(BaseModel):
    """Single time-block row from demand_serve_historical."""

    date: str = Field(..., description="Schedule date (YYYY-MM-DD)")
    time_block: int = Field(..., ge=1, le=96, description="Time block (1–96)")
    drawal: Optional[float] = Field(None, description="Drawal (MW)")
    actual_demand: Optional[float] = Field(
        None, description="Computed actual demand (comp_act_demand)"
    )
    schedule: Optional[float] = Field(None, description="Scheduled demand (MW)")
    od_ud: Optional[float] = Field(
        None, description="Overdrawal / underdrawal (drawal − schedule)"
    )
    dsm_rate: Optional[float] = Field(None, description="DSM rate")
    net_dsm_amount: Optional[float] = Field(None, description="Net DSM amount")
    frequency: Optional[float] = Field(None, description="Raw frequency (Hz)")


class DemandServeHistoricalResponse(BaseModel):
    schedule_date: str
    type: str = Field("IDF", description="Market type — always IDF for this endpoint")
    from_block: int
    to_block: int
    count: int = Field(..., description="Number of block records returned")
    records: List[DemandServeHistoricalRecord] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "schedule_date": "2025-05-22",
                    "type": "IDF",
                    "from_block": 1,
                    "to_block": 2,
                    "count": 2,
                    "records": [
                        {
                            "date": "2025-05-22",
                            "time_block": 1,
                            "drawal": 1500.0,
                            "actual_demand": 1480.0,
                            "schedule": 1490.0,
                            "od_ud": 10.0,
                            "dsm_rate": 2.5,
                            "net_dsm_amount": 25000.0,
                            "frequency": 49.98,
                        },
                        {
                            "date": "2025-05-22",
                            "time_block": 2,
                            "drawal": 1510.0,
                            "actual_demand": 1505.0,
                            "schedule": 1500.0,
                            "od_ud": 10.0,
                            "dsm_rate": 2.5,
                            "net_dsm_amount": 25000.0,
                            "frequency": 49.99,
                        },
                    ],
                }
            ]
        }
    }


@router.get(
    "/",
    response_model=DemandServeHistoricalResponse,
    status_code=status.HTTP_200_OK,
    summary="Get demand_serve_historical IDF records by date and time block range",
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch demand_serve_historical data"},
    },
)
async def get_demand_serve_historical(
    request: Request,
    schedule_date: str = Query(
        ...,
        description="Schedule date (YYYY-MM-DD)",
        example="2025-05-22",
        regex=r"^\d{4}-\d{2}-\d{2}$",
    ),
    from_block: int = Query(
        1,
        ge=1,
        le=96,
        description="Starting time block (1–96)",
    ),
    to_block: int = Query(
        96,
        ge=1,
        le=96,
        description="Ending time block (1–96), inclusive",
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Fetch **IDF-only** rows from **demand_serve_historical** for the given date and block range.

    Only records with `type = IDF` are returned. DAF rows are excluded.

    Returns per-block: date, time_block, drawal, actual_demand, schedule, od_ud,
    dsm_rate, net_dsm_amount, and frequency (sorted by time_block).
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        if from_block > to_block:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="from_block must be less than or equal to to_block",
            )

        audit_logger.log_system_operation(
            service="processing",
            operation="demand_serve_historical_fetch",
            message="Fetching demand_serve_historical data",
            correlation_id=correlation_id,
            user_id=user_id,
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )

        result = await demand_serve_historical_controller.get_by_date_and_blocks(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )

        record_count = result.get("count", 0)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="demand_serve_historical_view",
            message="User fetched demand_serve_historical data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            record_count=record_count,
            success=True,
        )

        return DemandServeHistoricalResponse(**result)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="DemandServeHistoricalFetchError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )
        logger.exception(
            "Error fetching demand_serve_historical for user %s date=%s blocks=%s-%s",
            user_id,
            schedule_date,
            from_block,
            to_block,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch demand_serve_historical data.",
        ) from e
