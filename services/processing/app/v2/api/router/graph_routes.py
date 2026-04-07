from typing import List, Optional, Literal, Union, Dict, Any
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, Query, HTTPException, status, Depends
import logging
from pydantic import BaseModel, Field

from ...controller.graph_controller import graph_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from libs.middleware import get_correlation_id

router = APIRouter(prefix="/graph", tags=["Graph Analysis V2"])

logger = logging.getLogger(__name__)

# ---------- Time (IST) ----------
IST = timezone(timedelta(hours=5, minutes=30))
def now_ist() -> datetime:
    return datetime.now(IST)

# ---------- Simplified Schemas ----------
class DemandMetrics(BaseModel):
    comp_act_demand: Optional[float] = None
    comp_udm: Optional[float] = None        
    forecast: Optional[float] = None
    MAPE: str

class DemandBlockItem(BaseModel):
    time_block: int
    data: DemandMetrics

class DemandDateGroup(BaseModel):
    schedule_date: str
    blocks: List[DemandBlockItem]

class DemandFlatItem(BaseModel):
    schedule_date: str
    time_block: int
    data: DemandMetrics

class DemandDataGrouped(BaseModel):
    result: List[DemandDateGroup]

class DemandDataFlat(BaseModel):
    result: List[DemandFlatItem]

class DemandResponseGrouped(BaseModel):
    timestamp: str
    data: DemandDataGrouped

class DemandResponseFlat(BaseModel):
    timestamp: str
    data: DemandDataFlat

# ---------- All Graph Response ----------
class AllGraphResponse(BaseModel):
    timestamp: str
    data: Dict[str, Any]

# ---------- Selected Days Response ----------
class SelectedDaysResponse(BaseModel):
    timestamp: str
    data: Dict[str, Any]

# ---------- Endpoints ----------

@router.get(
    "/all_graph",
    response_model=AllGraphResponse,
    status_code=status.HTTP_200_OK,
    summary="All-in-one: blocks + demand curve + MAPE curve (shared filters, single meta)",
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to compute combined analysis."},
    },
)
async def forecast_analysis_all_unified(
    request: Request,
    market: Optional[List[Literal["IDF", "DAF"]]] = Query(
        default=None,
        description='Market filter. Defaults to ["IDF"] if omitted.',
        example=["IDF"],
    ),
    zone: Optional[List[str]] = Query(
        default=None,
        description='Zone filter (case-insensitive). Defaults to ["PUDUCHERRY"] if omitted.',
        example=["PUDUCHERRY"],
    ),
    date_from: Optional[str] = Query(
        default=None,
        description="Start date (YYYY-MM-DD). Defaults to today (IST).",
        example="2025-08-26",
    ),
    date_to: Optional[str] = Query(
        default=None,
        description="End date (YYYY-MM-DD). Defaults to same as date_from.",
        example="2025-08-27",
    ),
    from_block: Optional[int] = Query(
        default=1, ge=1, le=96,
        description="Per-day block window start (1..96). Default 1.",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96, ge=1, le=96,
        description="Per-day block window end (1..96). Default 96.",
        example=96,
    ),
    group_by_date: bool = Query(
        default=True,
        description="If true, blocks are grouped per date; if false, a flat list is returned.",
        example=True,
    ),
    denominator: Literal["matched", "expected"] = Query(
        default="matched",
        description="Shared denominator for BOTH curves.",
        example="expected",
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Runs **blocks**, **demand curve (comp_act_demand)**, and **MAPE curve** with the SAME filters & SAME denominator.
    Returns a single unified `meta`.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="all_graph_fetch",
            message="Starting all graph data fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
            market=market,
            zone=zone
        )
        
        payload = await graph_controller.all_three_unified(
            markets=[str(m) for m in market] if market else None,
            zones=zone,
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            group_by_date=group_by_date,
            denominator=denominator,
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="all_graph_view",
            message="User successfully fetched all graph data",
            username=username,
            correlation_id=correlation_id,
            date_from=date_from,
            date_to=date_to,
            market=market,
            zone=zone,
            success=True
        )
        
        logger.info(f"All graph data fetched successfully for user {user_id}, date_from: {date_from}, date_to: {date_to}")
        
        return {"timestamp": now_ist().isoformat(), "data": payload}
    except HTTPException:
        raise
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="AllGraphDataFetchError",
            error_message=f"Failed to fetch all graph data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            date_from=date_from,
            date_to=date_to,
            market=market,
            zone=zone,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching all graph data for user {user_id}, date_from: {date_from}, date_to: {date_to}")
        raise HTTPException(status_code=500, detail="Failed to compute combined analysis.")

@router.get(
    "/selected_days",
    response_model=SelectedDaysResponse,
    status_code=status.HTTP_200_OK,
    summary="Selected days: blocks (comp_act_demand & comp_udm) + per-date stats table",
    response_description=(
        "Returns comp_act_demand and comp_udm by block for up to 10 dates (grouped or flat) "
        "and a per-date stats table (min, max, avg, sum, count) for both fields."
    ),
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch selected days data."},
    },
)
async def selected_days(
    request: Request,
    date: List[str] = Query(
        ...,
        min_items=1,
        max_items=10,
        description="One or more dates (YYYY-MM-DD). Max 10.",
        example=["2025-09-01", "2025-09-03"],
    ),
    market: Optional[List[Literal["IDF", "DAF"]]] = Query(
        default=None,
        description='Market filter. Defaults to ["IDF"] if omitted.',
        example=["IDF"],
    ),
    zone: Optional[List[str]] = Query(
        default=None,
        description='Zone filter (case-insensitive). Defaults to ["PUDUCHERRY"] if omitted.',
        example=["PUDUCHERRY"],
    ),
    from_block: Optional[int] = Query(
        default=1,
        ge=1,
        le=96,
        description="Per-day block window start (1..96). Default 1.",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96,
        ge=1,
        le=96,
        description="Per-day block window end (1..96). Default 96.",
        example=96,
    ),
    group_by_date: bool = Query(
        default=True,
        description="If true, groups by date; if false, returns a flat list with schedule_date on each row.",
        example=True,
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Single-shot API that returns:
    - **blocks**: comp_act_demand & comp_udm per (date, time_block) for the provided dates (max 10),
                  shaped for your chart (grouped-by-date or flat).
    - **stats**:  per-date min, max, avg, sum, count across the selected `from_block..to_block`
                  for both comp_act_demand and comp_udm.

    Sorting: schedule_date ASC, time_block ASC.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="selected_days_fetch",
            message="Starting selected days data fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            dates=date,
            market=market,
            zone=zone
        )
        
        payload = await graph_controller.selected_days_blocks_and_stats(
            dates=date,
            markets=[str(m) for m in market] if market else None,
            zones=zone,
            from_block=from_block,
            to_block=to_block,
            group_by_date=group_by_date,
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="selected_days_view",
            message="User successfully fetched selected days data",
            username=username,
            correlation_id=correlation_id,
            dates=date,
            market=market,
            zone=zone,
            success=True
        )
        
        logger.info(f"Selected days data fetched successfully for user {user_id}, dates: {date}")
        
        return {"timestamp": now_ist().isoformat(), "data": payload}
    except HTTPException:
        raise
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="SelectedDaysDataFetchError",
            error_message=f"Failed to fetch selected days data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            dates=date,
            market=market,
            zone=zone,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching selected days data for user {user_id}, dates: {date}")
        raise HTTPException(status_code=500, detail="Failed to fetch selected days data.")

@router.get(
    "/raw",
    response_model=Union[DemandResponseGrouped, DemandResponseFlat],
    status_code=status.HTTP_200_OK,
    summary="Raw demand data (per-day block window, grouped by date or flat)",
    response_description=(
        "Returns raw per-block demand data for one or more dates. "
        "Filter by market(s), zone(s), date range, and per-day block window (1–96). "
        "Set group_by_date=true (default) for grouped per-date result; "
        "or group_by_date=false for a flat list with schedule_date on each row."
    ),
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch raw demand data."},
    },
)
async def raw_demand_data(
    request: Request,
    market: Optional[List[Literal["IDF", "DAF"]]] = Query(
        default=None,
        description='Market filter. Defaults to ["IDF"] if omitted. '
                    'If your documents use only "type", we fallback to that field.',
    ),
    zone: Optional[List[str]] = Query(
        default=None,
        description='Zone filter (case-insensitive). Defaults to ["PUDUCHERRY"] if omitted. '
                    'Example: ?zone=North&zone=South',
    ),
    date_from: Optional[str] = Query(
        default=None,
        description="Start date (YYYY-MM-DD). Defaults to today (IST) if omitted.",
    ),
    date_to: Optional[str] = Query(
        default=None,
        description="End date (YYYY-MM-DD). Defaults to same as date_from.",
    ),
    from_block: Optional[int] = Query(
        default=1,
        ge=1,
        le=96,
        description="Per-day block window start (1..96). Applied to each date. Default 1.",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96,
        ge=1,
        le=96,
        description="Per-day block window end (1..96). Applied to each date. Default 96.",
        example=96,
    ),
    group_by_date: bool = Query(
        default=True,
        description="If true, returns one object per date with its blocks; "
                    "if false, returns a flat list with schedule_date per row.",
        example=True,
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Raw demand data endpoint - same as demand_graph but with different naming for clarity.
    Defaults when omitted:
    - market -> ["IDF"]
    - zone   -> ["PUDUCHERRY"]
    - date_from -> today (IST)
    - date_to   -> date_from (same day)
    - from_block/to_block -> 1..96
    Sorting: schedule_date ASC, time_block ASC.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="raw_demand_fetch",
            message="Starting raw demand data fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
            market=market,
            zone=zone
        )
        
        payload = await graph_controller.demand_blocks(
            markets=[str(m) for m in market] if market else None,
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            group_by_date=group_by_date,
            zones=zone,
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="raw_demand_view",
            message="User successfully fetched raw demand data",
            username=username,
            correlation_id=correlation_id,
            date_from=date_from,
            date_to=date_to,
            market=market,
            zone=zone,
            success=True
        )
        
        logger.info(f"Raw demand data fetched successfully for user {user_id}, date_from: {date_from}, date_to: {date_to}")
        
        return {
            "timestamp": now_ist().isoformat(),
            "data": payload,
        }
    except HTTPException:
        raise
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="RawDemandDataFetchError",
            error_message=f"Failed to fetch raw demand data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            date_from=date_from,
            date_to=date_to,
            market=market,
            zone=zone,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching raw demand data for user {user_id}, date_from: {date_from}, date_to: {date_to}")
        raise HTTPException(status_code=500, detail="Failed to fetch raw demand data.")
