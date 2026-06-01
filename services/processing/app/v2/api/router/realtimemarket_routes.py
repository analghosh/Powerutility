from functools import wraps
from typing import Any, Dict, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from datetime import datetime
import logging

import pytz

from services.processing.app.controller.realtimemarket_controller import realtimemarket_controller
from libs.audit_queue.simple_logger import AuditLogger
from libs.middleware import get_correlation_id
from services.processing.app.core.dependencies import get_audit_logger
from ...dependencies import get_user_context

router = APIRouter(prefix="/processing", tags=["Real-Time-Market"])
logger = logging.getLogger(__name__)

# Define request schema
class PlantModel(BaseModel):
    bid_plant_name: str
    bid_plant_key: str
    bid_plant_category: str
    bid_default_volume: int
    bid_volume: int
    bid_price: float
    bid_default_price: float
    rtm_forecast_Price: float
    buy_sell_bid_type: str

class BlockData(BaseModel):
    bid_date: str
    time_block: str
    start_time: str
    end_time: str
    block_no: int
    revision_no: int
    session_block: int
    current_user: str
    Ex_split_string: str
    buy_sell_bid: str
    buy_sell_bid_type: str
    selected_action_type: str
    bid_plant_name: List[PlantModel]

class RequestModel(BaseModel):
    current_user: str
    data: List[BlockData]


# -------- Helper Functions --------
def build_req_meta(request: Request) -> Dict[str, Any]:
    ip = (request.headers.get("X-Forwarded-For") or request.client.host or "").split(",")[0].strip()
    ua = request.headers.get("User-Agent")
    return {"ip_address": ip, "user_agent": ua}

def audit_endpoint(operation_name: str, audit_logger: AuditLogger = Depends(get_audit_logger)):
    """
    Decorator for logging system/user actions and handling errors in RTM endpoints.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, user_context: dict = {}, **kwargs):
            user_id = user_context.get("user_id", "anonymous")
            username = user_context.get("username", "anonymous")
            correlation_id = user_context.get("correlation_id", "unknown")
            log_meta = {
                "service": "processing",
                "correlation_id": correlation_id,
                "user_id": user_id
            }
            try:
                # Log system operation start
                audit_logger.log_system_operation(
                    **log_meta,
                    operation=operation_name,
                    message=f"Starting {operation_name}"
                )

                # Execute the endpoint
                result = await func(*args, user_context=user_context, **kwargs)

                # Log user action success
                record_count = len(result.get("blocks", [])) if isinstance(result, dict) and "blocks" in result else None
                audit_logger.log_user_action(
                    **log_meta,
                    action=operation_name,
                    message=f"{operation_name} executed successfully",
                    username=username,
                    record_count=record_count,
                    success=True
                )
                return result

            except HTTPException:
                raise

            except Exception as e:
                # Log error
                audit_logger.log_error_event(
                    service="processing",
                    error_name=f"{operation_name}Error",
                    error_message=str(e),
                    user_id=user_id,
                    correlation_id=correlation_id,
                    error_type=type(e).__name__,
                )
                logger.exception(f"[RTM] Error in {operation_name} for user {user_id}")
                raise HTTPException(status_code=500, detail=f"Failed to execute {operation_name}")

        return wrapper
    return decorator

@router.get("/rtm-single-bid-graph")
@audit_endpoint("rtm_single_bid_graph")
async def get_rtm_single_bid_graph(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD. Defaults to today's date in Asia/Kolkata.", example="2025-01-15"
    ),
    from_block: int = Query(1, ge=1, le=96, description="Time block start (1..96). Default 1."),
    to_block: int = Query(96, ge=1, le=96, description="Time block end (1..96). Default 96."),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch RTM Single Bid Graph data for a given schedule date and block range.
    """

    try:
        # ✅ Handle defaults cleanly
        if not schedule_date:
            ist = pytz.timezone("Asia/Kolkata")
            schedule_date = datetime.now(ist).strftime("%Y-%m-%d")

        user_id = user_context.get("user_id", "anonymous")
       
        audit_logger.log_system_operation(
            service="processing",
            correlation_id=user_context.get("correlation_id", "unknown"),
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            user_id=user_id,
            operation="rtm_single_bid_graph",
            message="Fetching RTM Single Bid Graph"
        )

        # 🧠 Core business logic call
        result = await realtimemarket_controller.rtm_single_bid_graph(
            schedule_date, from_block, to_block
        )
        audit_logger.log_user_action(
            service="processing",
            correlation_id=user_context.get("correlation_id", "unknown"),
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            action="rtm_single_bid_graph",
            message="RTM Single Bid Graph fetched successfully",
            username=user_context.get("username", "anonymous"),
            record_count=len(result.get("data", [])) if isinstance(result, dict) else 0,
            success=True,
            user_id=user_id
        )

        return result
    except HTTPException:
        raise
    except Exception as e:
       
        logger.exception(f"[RTM] Error fetching Single Bid Graph for user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to fetch RTM Single Bid Graph")


@router.get("/rtm-single-bid-table")
async def get_rtm_single_bid_table(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD. Defaults to today's date in Asia/Kolkata.", example="2025-01-15"
    ),
    from_block: int = Query(1, ge=1, le=96, description="Starting time block (default=1)"),
    to_block: int = Query(96, ge=1, le=96, description="Ending time block (default=96)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch RTM Single Bid Table data by exchange for the given schedule_date and block range.
    """

    try:
        # ✅ Default to today's date (Asia/Kolkata)
        if not schedule_date:
            ist = pytz.timezone("Asia/Kolkata")
            schedule_date = datetime.now(ist).strftime("%Y-%m-%d")

        user_id = user_context.get("user_id", "anonymous")
        username = user_context.get("username", "anonymous")
        correlation_id = user_context.get("correlation_id", "unknown")

        # ✅ Unified log metadata
        log_meta = {
            "service": "processing",
            "correlation_id": correlation_id,
            "schedule_date": schedule_date,
            "from_block": from_block,
            "to_block": to_block,
            "user_id": user_id,
        }

        # 🪵 Log start of operation
        audit_logger.log_system_operation(
            **log_meta,
            operation="rtm_single_bid_table",
            message="Fetching RTM Single Bid Table"
        )

        # 🧠 Core logic
        result = await realtimemarket_controller.rtm_single_bid_table(schedule_date, from_block, to_block)

        if not result:
            logger.info(f"[RTM] No table data found | Date={schedule_date}")
            raise HTTPException(status_code=404, detail="No data found for the requested schedule_date.")

        record_count = len(result.get("data", [])) if isinstance(result, dict) else 0

        # 🪵 Log success
        audit_logger.log_user_action(
            **log_meta,
            action="rtm_single_bid_table",
            message="RTM Single Bid Table fetched successfully",
            username=username,
            record_count=record_count,
            success=True
        )

        logger.info(
            f"[RTM] User={user_id} | Date={schedule_date} | Blocks={from_block}-{to_block} | Records={record_count}"
        )

        return result

    except HTTPException:
        raise

    except Exception as e:
        # ❌ Centralized error logging
        audit_logger.log_error_event(
            service="processing",
            error_name="RealTimeMarketTableFetchError",
            error_message=str(e),
            user_id=user_context.get("user_id", "anonymous"),
            correlation_id=user_context.get("correlation_id", "unknown"),
            error_type=type(e).__name__,
        )
        logger.exception(f"[RTM] Error fetching Single Bid Table for user {user_context.get('user_id')}")
        raise HTTPException(status_code=500, detail="Failed to fetch RTM Single Bid Table")



@router.post("/submit_for_approval_post_data")
async def submit_for_approval_post_data(
    request: RequestModel,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Submit RTM market data for approval.
    """

    try:
        # ✅ Extract user context
        user_id = user_context.get("user_id", "anonymous")
        username = user_context.get("username", "anonymous")
        correlation_id = user_context.get("correlation_id", "unknown")

        # ✅ Unified log metadata
        log_meta = {
            "service": "processing",
            "correlation_id": correlation_id,
            "user_id": user_id,
        }

        # 🪵 Log start
        audit_logger.log_system_operation(
            **log_meta,
            operation="submit_for_approval_post_data",
            message="Submitting RTM market data for approval"
        )

        # 🧠 Core logic
        payload = request.dict()
        result = await realtimemarket_controller.submit_for_approval_post_data(payload)

        # ✅ Handle no result scenario
        if not result:
            logger.info(f"[RTM] No response received for approval submission | User={user_id}")
            raise HTTPException(status_code=404, detail="No response received for approval submission.")

        # 🪵 Log success
        audit_logger.log_user_action(
            **log_meta,
            action="submit_for_approval_post_data",
            message="RTM market data submitted for approval successfully",
            username=username,
            success=True,
        )

        logger.info(f"[RTM] Approval submission successful | User={user_id} | Correlation={correlation_id}")

        return result

    except HTTPException:
        raise

    except Exception as e:
        # ❌ Centralized error logging
        audit_logger.log_error_event(
            service="processing",
            error_name="SubmitForApprovalError",
            error_message=str(e),
            user_id=user_context.get("user_id", "anonymous"),
            correlation_id=user_context.get("correlation_id", "unknown"),
            error_type=type(e).__name__,
        )
        logger.exception(f"[RTM] Error submitting approval data for user {user_context.get('user_id')}")
        raise HTTPException(status_code=500, detail="Failed to submit approval data")


@router.get("/rtm-scheduling_table_calculation")
async def get_rtm_scheduling_table_calculation(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
   
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")    
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_scheduling_table_calculation",
            message="Starting RTM scheduling table calculation",
            correlation_id=correlation_id,
            user_id=user_id,
            schedule_date=schedule_date
        )
        result = await realtimemarket_controller.rtm_scheduling_cal_table(schedule_date)
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_scheduling_table_calculation",
            message="RTM scheduling table calculation successful    ",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=len(result.get("data", [])) if isinstance(result, dict) else   0,
            success=True    
        )
        logger.info(f"RTM scheduling table calculation successful for user {user_id}, schedule_date: {schedule_date}")
        return result
    
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingTableCalculationError",
            error_message=f"Failed to fetch RTM scheduling table data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )
        logger.exception(f"Error fetching RTM scheduling table data for user {user_id}, schedule_date: {schedule_date}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch RTM scheduling table data."
        )
