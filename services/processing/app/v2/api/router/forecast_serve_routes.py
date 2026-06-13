"""
Forecast Serve API — read forecast_serve collection by date and time block range.
"""

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from libs.audit_queue.simple_logger import AuditLogger
from libs.models.forecast_serve_entity import (
    FORECAST_SOURCE_CSV,
    FORECAST_SOURCE_DISPLAY_AUTO,
    FORECAST_SOURCE_DISPLAY_MANUAL,
    FORECAST_SOURCE_RTM_API,
)
from ....core.dependencies import get_audit_logger
from ...controller.forecast_serve_controller import forecast_serve_controller
from ...dependencies import get_user_context

router = APIRouter(
    prefix="/forecast-serve",
    tags=["Forecast Serve"],
)

logger = logging.getLogger(__name__)


class ForecastServeBlockRecord(BaseModel):
    """Single time-block row (source is on the parent auto/manual object)."""

    id: Optional[str] = None
    scheduled_at: Optional[str] = None
    time_block: int
    type: Optional[str] = None
    forecast: Optional[float] = None
    forecasted_frequency: Optional[float] = None
    forecast_revision: Optional[int] = Field(
        None,
        description="Per delivery-day ingest revision for this source",
    )
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ForecastSourceBucket(BaseModel):
    """All blocks for one forecast source (Auto or Manual)."""

    source: str = Field(
        ...,
        description=f"`{FORECAST_SOURCE_RTM_API}` (auto) or `{FORECAST_SOURCE_CSV}` (manual)",
    )
    forecast_source: Literal["Auto", "Manual"]
    count: int = Field(..., description="Number of block records in this bucket")
    records: List[ForecastServeBlockRecord] = Field(default_factory=list)


class ManualForecastStatusResponse(BaseModel):
    schedule_date: str
    manual_forecast: bool = Field(
        ...,
        description="True when manual (CSV) forecast exists for at least one time block",
    )
    message: Optional[str] = Field(
        None,
        description="Present when manual_forecast is false",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "schedule_date": "2025-05-22",
                    "manual_forecast": True,
                    "message": None,
                },
                {
                    "schedule_date": "2025-05-22",
                    "manual_forecast": False,
                    "message": "No Data Available for this selected date.",
                },
            ]
        }
    }


class ForecastServeResponse(BaseModel):
    schedule_date: str
    from_block: int
    to_block: int
    auto: ForecastSourceBucket
    manual: ForecastSourceBucket

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "schedule_date": "2025-05-22",
                    "from_block": 1,
                    "to_block": 2,
                    "auto": {
                        "source": FORECAST_SOURCE_RTM_API,
                        "forecast_source": FORECAST_SOURCE_DISPLAY_AUTO,
                        "count": 2,
                        "records": [
                            {
                                "time_block": 1,
                                "type": "IDF",
                                "forecast": 1200.5,
                                "forecast_revision": 3,
                            },
                            {
                                "time_block": 2,
                                "type": "IDF",
                                "forecast": 1210.0,
                                "forecast_revision": 3,
                            },
                        ],
                    },
                    "manual": {
                        "source": FORECAST_SOURCE_CSV,
                        "forecast_source": FORECAST_SOURCE_DISPLAY_MANUAL,
                        "count": 2,
                        "records": [
                            {"time_block": 1, "type": "IDF", "forecast": 1198.0},
                            {"time_block": 2, "type": "IDF", "forecast": 1205.0},
                        ],
                    },
                }
            ]
        }
    }


@router.get(
    "/",
    response_model=ForecastServeResponse,
    status_code=status.HTTP_200_OK,
    summary="Get forecast_serve records by date and time block range",
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch forecast_serve data"},
    },
)
async def get_forecast_serve(
    request: Request,
    schedule_date: str = Query(
        ...,
        description="Schedule date (YYYY-MM-DD)",
        example="2025-05-22",
        regex=r"^\d{4}-\d{2}-\d{2}$",
    ),
    from_block: int = Query(
        ...,
        ge=1,
        le=96,
        description="Starting time block (1–96)",
    ),
    to_block: int = Query(
        ...,
        ge=1,
        le=96,
        description="Ending time block (1–96), inclusive",
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Fetch IDF forecast rows from **forecast_serve** for the given date and block range.

    Only rows at the **latest** ``forecast_revision`` per source (Auto/Manual) are returned
    (filtered in MongoDB via ``idx_forecast_serve_day_source_revision``).

    Response is split into two objects:

    - **`auto`** — `rtm_forecast_api` (`forecast_source`: Auto)
    - **`manual`** — `csv` (`forecast_source`: Manual)

    Each contains `source`, `forecast_source`, `count`, and `records` (sorted by `time_block`).
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
            operation="forecast_serve_fetch",
            message="Fetching forecast_serve data",
            correlation_id=correlation_id,
            user_id=user_id,
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )

        result = await forecast_serve_controller.get_by_date_and_blocks(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )

        auto_count = result.get("auto", {}).get("count", 0)
        manual_count = result.get("manual", {}).get("count", 0)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="forecast_serve_view",
            message="User fetched forecast_serve data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            auto_count=auto_count,
            manual_count=manual_count,
            record_count=auto_count + manual_count,
            success=True,
        )

        return ForecastServeResponse(**result)

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
            error_name="ForecastServeFetchError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )
        logger.exception(
            "Error fetching forecast_serve for user %s date=%s blocks=%s-%s",
            user_id,
            schedule_date,
            from_block,
            to_block,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch forecast_serve data.",
        ) from e


@router.get(
    "/manual-status",
    response_model=ManualForecastStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Check if manual forecast exists for a schedule date",
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to check manual forecast status"},
    },
)
async def get_manual_forecast_status(
    request: Request,
    schedule_date: str = Query(
        ...,
        description="Schedule date (YYYY-MM-DD)",
        example="2025-05-22",
        regex=r"^\d{4}-\d{2}-\d{2}$",
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Return whether **manual** (CSV) forecast data exists in **forecast_serve** for the date.

    - **`manual_forecast`**: `true` if at least one time block has CSV data at the latest revision
    - **`message`**: ``No Data Available for this selected date.`` when `manual_forecast` is false
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="forecast_serve_manual_status",
            message="Checking manual forecast status",
            correlation_id=correlation_id,
            user_id=user_id,
            schedule_date=schedule_date,
        )

        result = await forecast_serve_controller.get_manual_forecast_status(
            schedule_date=schedule_date,
        )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="forecast_serve_manual_status_view",
            message="User checked manual forecast status",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            manual_forecast=result.get("manual_forecast"),
            success=True,
        )

        return ManualForecastStatusResponse(**result)

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
            error_name="ForecastServeManualStatusError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
        )
        logger.exception(
            "Error checking manual forecast status for user %s date=%s",
            user_id,
            schedule_date,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check manual forecast status.",
        ) from e
