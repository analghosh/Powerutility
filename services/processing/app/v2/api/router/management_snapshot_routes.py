"""
Management Snapshot V2 Router
Enhanced management snapshot endpoints with JWT authentication and correlation tracking
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends, Query
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta
import logging

from ...controller.management_snapshot_controller import management_snapshot_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(
    prefix="/management_snapshot",
    tags=["Management Snapshot V2"]
)

logger = logging.getLogger(__name__)

# Time (IST) configuration
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist() -> datetime:
    return datetime.now(IST)

# --- V2 Generic Response --- 
class V2Response(BaseModel):
    data: Any
    timestamp: str
    correlation_id: Optional[str]

# --- Schemas for existing /demand_supply endpoint ---
class SDTBlockRow(BaseModel):
    time_block: int = Field(..., ge=1, le=96, example=1)
    comp_act_demand: float = Field(..., description="Computed actual demand")
    comp_udm: float = Field(..., description="Computed under-drawal margin")
    total_supply: float = Field(..., description="Total supply volume")
    gap: float = Field(..., description="Gap between demand and supply")
    dam_trade: float = Field(..., description="DAM traded volume")
    rtm_trade: float = Field(..., description="RTM traded volume")

class SDTPayload(BaseModel):
    schedule_date: str = Field(..., description="Schedule date in YYYY-MM-DD format")
    from_block: int = Field(..., ge=1, le=96)
    to_block: int = Field(..., ge=1, le=96)
    blocks: list[SDTBlockRow] = Field(..., description="Time block data")

# --- Schemas for new endpoints (from v1) ---
class SnapshotBlockRow(BaseModel):
    time_block: int = Field(..., ge=1, le=96, example=1)
    dam_mcv: Optional[float] = Field(None, example=3463.0)
    rtm_mcv: Optional[float] = Field(None, example=3521.0)
    dam_mcp: Optional[float] = Field(None, example=2889.49)
    rtm_mcp: Optional[float] = Field(None, example=2892.10)

class ManagementSnapshotPayload(BaseModel):
    schedule_date: str = Field(..., example="2025-08-29")
    data: List[SnapshotBlockRow]

class MarketTotals(BaseModel):
    buy: Optional[float] = Field(None, example=6157.40)
    sell: Optional[float] = Field(None, example=12558.00)
    mcp: Optional[float] = Field(None, example=2889.49)
    mcv: Optional[float] = Field(None, example=3463.00)

class MarketOurs(BaseModel):
    buy: Optional[float] = Field(None, example=69.25)
    sell: Optional[float] = Field(None, example=0.0)
    mcp: Optional[float] = Field(None, example=2889.49)
    mcv: Optional[float] = Field(None, example=69.25)

class MarketPair(BaseModel):
    Total: Optional[MarketTotals] = None
    Ours: Optional[MarketOurs] = None

class MOBBlockRow(BaseModel):
    time_block: int = Field(..., ge=1, le=96, example=1)
    DAM: MarketPair
    RTM: MarketPair

class MOBPayload(BaseModel):
    schedule_date: str = Field(..., example="2025-08-31")
    data: List[MOBBlockRow]

# --- Existing Endpoint --- 
@router.get("/demand_supply", response_model=V2Response, status_code=status.HTTP_200_OK, summary="Supply vs Demand + DAM/RTM trades by time_block (V2)")
async def get_demand_supply_snapshot(request: Request, schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD. Defaults to today's date in Asia/Kolkata.", example="2025-01-15"), from_block: Optional[int] = Query(default=1, ge=1, le=96), to_block: Optional[int] = Query(default=96, ge=1, le=96), user_context: Dict[str, Any] = Depends(get_user_context), audit_logger: AuditLogger = Depends(get_audit_logger)):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id")
    try:
        audit_logger.log_system_operation(service="processing", operation="demand_supply_snapshot_fetch", message="Starting demand-supply snapshot fetch operation", correlation_id=correlation_id, user_id=user_id)
        result = await management_snapshot_controller.get_demand_supply_snapshot(schedule_date=schedule_date, from_block=from_block, to_block=to_block)
        audit_logger.log_user_action(service="processing", user_id=user_id, action="demand_supply_snapshot_view", message="User successfully fetched demand-supply snapshot", username=username, correlation_id=correlation_id, success=True)
        return V2Response(data=result, timestamp=now_ist().isoformat(), correlation_id=correlation_id)
    except Exception as e:
        logger.exception(f"Error fetching demand-supply snapshot for user {user_id}")
        audit_logger.log_error_event(service="processing", error_name="DemandSupplySnapshotFetchError", error_message=f"Failed to fetch demand-supply snapshot: {str(e)}", user_id=user_id, correlation_id=correlation_id, error_type=type(e).__name__)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch demand-supply snapshot.")

# --- New Endpoints --- 
@router.get("/iex_dam_rtm", response_model=V2Response, status_code=status.HTTP_200_OK, summary="DAM/RTM per-block snapshot (V2)")
async def iex_dam_rtm_snapshot(schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD. Defaults to today's date."), from_block: Optional[int] = Query(1, ge=1, le=96), to_block: Optional[int] = Query(96, ge=1, le=96), user_context: Dict[str, Any] = Depends(get_user_context), audit_logger: AuditLogger = Depends(get_audit_logger)):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id")
    try:
        audit_logger.log_system_operation(service="processing", operation="iex_dam_rtm_snapshot_fetch", message="Starting IEX DAM/RTM snapshot fetch", correlation_id=correlation_id, user_id=user_id)
        result = await management_snapshot_controller.get_iex_dam_rtm_snapshot(schedule_date=schedule_date, from_block=from_block, to_block=to_block)
        audit_logger.log_user_action(service="processing", user_id=user_id, action="iex_dam_rtm_snapshot_view", message="Successfully fetched IEX DAM/RTM snapshot", username=username, correlation_id=correlation_id, success=True)
        return V2Response(data=result, timestamp=now_ist().isoformat(), correlation_id=correlation_id)
    except Exception as e:
        logger.exception(f"Error fetching IEX DAM/RTM snapshot for user {user_id}")
        audit_logger.log_error_event(service="processing", error_name="IEXDamRtmSnapshotError", error_message=f"Failed to fetch IEX DAM/RTM snapshot: {str(e)}", user_id=user_id, correlation_id=correlation_id, error_type=type(e).__name__)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch IEX DAM/RTM snapshot.")

@router.get("/variable_cost", response_model=V2Response, status_code=status.HTTP_200_OK, summary="Variable Cost Snapshot (V2)")
async def get_variable_cost_snapshot(schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD. Defaults to today's date."), from_block: Optional[int] = Query(1, ge=1, le=96), to_block: Optional[int] = Query(96, ge=1, le=96), exchange: Optional[str] = Query(None, example="IEX"), user_context: Dict[str, Any] = Depends(get_user_context), audit_logger: AuditLogger = Depends(get_audit_logger)):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id")
    try:
        audit_logger.log_system_operation(service="processing", operation="variable_cost_snapshot_fetch", message="Starting variable cost snapshot fetch", correlation_id=correlation_id, user_id=user_id)
        result = await management_snapshot_controller.get_variable_cost_snapshot(schedule_date=schedule_date, from_block=from_block, to_block=to_block, exchange=exchange)
        audit_logger.log_user_action(service="processing", user_id=user_id, action="variable_cost_snapshot_view", message="Successfully fetched variable cost snapshot", username=username, correlation_id=correlation_id, success=True)
        return V2Response(data=result, timestamp=now_ist().isoformat(), correlation_id=correlation_id)
    except Exception as e:
        logger.exception(f"Error fetching variable cost snapshot for user {user_id}")
        audit_logger.log_error_event(service="processing", error_name="VariableCostSnapshotError", error_message=f"Failed to fetch variable cost snapshot: {str(e)}", user_id=user_id, correlation_id=correlation_id, error_type=type(e).__name__)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch variable cost snapshot.")

@router.get("/variable_cost/range", response_model=V2Response, status_code=status.HTTP_200_OK, summary="Variable Cost Snapshot for Date Range (V2)")
async def get_variable_cost_snapshot_range(date_from: str = Query(..., example="2025-08-29"), date_to: str = Query(..., example="2025-08-31"), from_block: Optional[int] = Query(1, ge=1, le=96), to_block: Optional[int] = Query(96, ge=1, le=96), exchange: Optional[str] = Query(None, example="IEX"), user_context: Dict[str, Any] = Depends(get_user_context), audit_logger: AuditLogger = Depends(get_audit_logger)):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id")
    try:
        audit_logger.log_system_operation(service="processing", operation="variable_cost_range_fetch", message="Starting variable cost snapshot range fetch", correlation_id=correlation_id, user_id=user_id)
        result = await management_snapshot_controller.get_variable_cost_snapshot_range(date_from=date_from, date_to=date_to, from_block=from_block, to_block=to_block, exchange=exchange)
        audit_logger.log_user_action(service="processing", user_id=user_id, action="variable_cost_range_view", message="Successfully fetched variable cost snapshot range", username=username, correlation_id=correlation_id, success=True)
        return V2Response(data=result, timestamp=now_ist().isoformat(), correlation_id=correlation_id)
    except Exception as e:
        logger.exception(f"Error fetching variable cost snapshot range for user {user_id}")
        audit_logger.log_error_event(service="processing", error_name="VariableCostRangeError", error_message=f"Failed to fetch variable cost snapshot range: {str(e)}", user_id=user_id, correlation_id=correlation_id, error_type=type(e).__name__)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch variable cost snapshot range.")
