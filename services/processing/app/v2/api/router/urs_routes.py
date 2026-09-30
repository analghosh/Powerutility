"""
URS Availed API — un-requisitioned surplus per plant, per 15-minute block.

Reads ``urs_serve``, which the scheduler fills from the RLDC (Grid India) feed.
Nothing here is uploaded: the data arrives on the same 15-minute cron as the
rest of the supply pipeline.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ...controller.urs_controller import urs_controller
from ...dependencies import get_user_context
from ...helpers.urs_audit import log_urs_access_attempt, log_urs_outcome

router = APIRouter(
    prefix="/urs-availed",
    tags=["URS Availed"],
)

logger = logging.getLogger(__name__)

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class UrsPlantColumn(BaseModel):
    """One column of the grid: a plant that reported surplus that day."""

    plant_id: str = Field(..., description="Plant identifier (RLDC SellerAcronym)")
    plant_name: str = Field(..., description="Display name (falls back to plant_id)")
    technology: Optional[str] = Field(None, description="Thermal / Hydro / Nuclear / …")
    rate: Optional[float] = Field(
        None,
        description="FC + VC in Rs/MWh, as it stood when the day was ingested",
    )
    rate_complete: bool = Field(
        False,
        description=(
            "False when the plant has no fixed cost on record, so `rate` is "
            "variable cost only. Most plants are currently in this state."
        ),
    )


class UrsBlock(BaseModel):
    """One row of the grid: a 15-minute block across every plant."""

    time_block: int = Field(..., description="Time block, 1–96")
    start_time: str = Field(..., description="Block start, HH:MM")
    end_time: str = Field(..., description="Block end, HH:MM")
    volumes: Dict[str, float] = Field(
        default_factory=dict,
        description="URS in MW, keyed by plant_id. Absent plants had no row.",
    )
    total: float = Field(0.0, description="Sum of every plant's URS for this block")
    weighted_avg_rate: Optional[float] = Field(
        None,
        description=(
            "Σ(volume × rate) / Σ(volume) over the plants that carry a rate, "
            "in Rs/MWh. Null when no priced surplus was scheduled in the block."
        ),
    )
    rate_is_partial: bool = Field(
        False,
        description="At least one plant in the average lacks a fixed cost",
    )


class UrsSummary(BaseModel):
    plant_count: int = Field(0, description="Plants with surplus in the range")
    block_count: int = Field(0, description="Blocks returned")
    total_volume_mw_blocks: float = Field(
        0.0,
        description="MW summed across blocks. Divide by 4 for MWh.",
    )
    blocks_with_rate: int = Field(0, description="Blocks that produced an average")
    plants_missing_fixed_cost: int = Field(
        0, description="Plants priced on variable cost alone"
    )
    plants_missing_rate: int = Field(0, description="Plants with no price at all")
    source_revision_number: Optional[int] = Field(None, description="RLDC revision")
    schedule_published_time: Optional[Any] = Field(
        None, description="RLDC publish timestamp for the day"
    )


class UrsAvailedResponse(BaseModel):
    query: Dict[str, Any] = Field(..., description="Echo of the requested window")
    plants: List[UrsPlantColumn] = Field(default_factory=list)
    blocks: List[UrsBlock] = Field(default_factory=list)
    summary: UrsSummary


class UrsDate(BaseModel):
    date: str = Field(..., description="Schedule date, YYYY-MM-DD")
    plant_count: int = Field(0, description="Plants with surplus that day")


@router.get(
    "/backfill-status",
    summary="What the one-time URS backfill did",
    responses={500: {"description": "Failed to read the backfill state"}},
)
async def get_urs_backfill_status(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    One row per day the backfill attempted, with what it found.

    Exists because the people who need this answer have no shell and no
    database access to the host. Without it, "the backfill never ran", "it ran
    and the gateway refused it" and "it ran and those days were genuinely
    quiet" are indistinguishable from outside - all three look like an empty
    report.

    An empty list means the backfill has not run at all.
    """
    try:
        return await urs_controller.get_backfill_state()
    except Exception as exc:
        logger.error("Failed to read URS backfill state: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Failed to read the backfill state"},
        ) from exc


@router.get(
    "/dates",
    response_model=List[UrsDate],
    summary="Days that hold URS data",
    responses={500: {"description": "Failed to fetch URS dates"}},
)
async def get_urs_dates(
    request: Request,
    limit: int = Query(60, ge=1, le=365, description="How many days to return"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Which schedule dates have URS stored, newest first.

    The report opens on today, and on a quiet day that is an empty grid with
    nothing to click. These are the dates the picker can offer instead.
    """
    try:
        return await urs_controller.get_dates(limit=limit)
    except Exception as exc:
        logger.error("Failed to fetch URS dates: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Failed to fetch URS dates"},
        ) from exc


@router.get(
    "/",
    response_model=UrsAvailedResponse,
    summary="URS Availed — surplus per plant per time block, with the weighted average rate",
    responses={
        200: {"description": "The grid for one schedule date"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch URS data"},
    },
)
async def get_urs_availed(
    request: Request,
    schedule_date: str = Query(
        ...,
        description="Schedule date (YYYY-MM-DD)",
        example="2026-09-20",
        regex=_DATE_PATTERN,
    ),
    from_block: int = Query(1, ge=1, le=96, description="Starting time block (1–96)"),
    to_block: int = Query(96, ge=1, le=96, description="Ending time block (1–96), inclusive"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Un-requisitioned surplus for one day: a row per time block, a column per
    plant, then the block total and the **weighted average URS rate**.

    The rate is `Σ(volume × plant rate) / Σ(volume)` over the plants that carry
    a rate, where a plant's rate is its fixed plus variable cost in Rs/MWh as it
    stood when that day was ingested.

    **One day at a time.** The grid puts plants in columns, so stacking two days
    would repeat every header and average across two days' prices at once.

    URS is *also* counted inside each plant's SG in `supply_serve`, which is
    what SG has always meant here. These figures are the URS half on its own —
    adding them to SG would double-count.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /urs-availed/"

    log_urs_access_attempt(
        audit_logger,
        user_id=user_id,
        username=username,
        correlation_id=correlation_id,
        endpoint=endpoint,
        request=request,
        schedule_date=schedule_date,
        from_block=from_block,
        to_block=to_block,
    )

    try:
        result = await urs_controller.get_urs_availed(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )
    except ValueError as exc:
        log_urs_outcome(
            audit_logger,
            action="urs_availed_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="URS Availed request rejected",
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            failure_reason=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(exc)},
        ) from exc
    except Exception as exc:
        logger.error("Failed to fetch URS Availed data: %s", exc, exc_info=True)
        log_urs_outcome(
            audit_logger,
            action="urs_availed_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="URS Availed request failed",
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            failure_reason=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Failed to fetch URS data"},
        ) from exc

    log_urs_outcome(
        audit_logger,
        action="urs_availed_view",
        user_id=user_id,
        username=username,
        correlation_id=correlation_id,
        endpoint=endpoint,
        request=request,
        success=True,
        message="User fetched URS Availed data",
        schedule_date=schedule_date,
        from_block=from_block,
        to_block=to_block,
        record_count=result.get("summary", {}).get("plant_count", 0),
    )
    return result


@router.get(
    "/download",
    summary="URS Availed as CSV",
    responses={
        200: {"description": "CSV file", "content": {"text/csv": {}}},
        422: {"description": "Validation error"},
        500: {"description": "Failed to build the CSV"},
    },
)
async def download_urs_availed(
    request: Request,
    schedule_date: str = Query(..., regex=_DATE_PATTERN, example="2026-09-20"),
    from_block: int = Query(1, ge=1, le=96),
    to_block: int = Query(96, ge=1, le=96),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """The same grid as a spreadsheet, one row per block."""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /urs-availed/download"

    log_urs_access_attempt(
        audit_logger,
        user_id=user_id,
        username=username,
        correlation_id=correlation_id,
        endpoint=endpoint,
        request=request,
        schedule_date=schedule_date,
        from_block=from_block,
        to_block=to_block,
    )

    try:
        csv_content, filename = await urs_controller.download_csv(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(exc)},
        ) from exc
    except Exception as exc:
        logger.error("Failed to build URS CSV: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Failed to build the CSV"},
        ) from exc

    log_urs_outcome(
        audit_logger,
        action="urs_availed_download",
        user_id=user_id,
        username=username,
        correlation_id=correlation_id,
        endpoint=endpoint,
        request=request,
        success=True,
        message="User downloaded URS Availed CSV",
        schedule_date=schedule_date,
        from_block=from_block,
        to_block=to_block,
    )
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
