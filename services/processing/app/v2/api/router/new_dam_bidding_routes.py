from typing import Optional, List, Callable, Dict, Any
import logging

from fastapi import APIRouter, Query, Body, Depends, Request, HTTPException, status, Response
from pydantic import BaseModel, Field

from services.processing.app.v2.controller.new_dam_bidding_controller import (
    dam_bidding_controller,
)
from libs.core.dependencies import get_current_user
from libs.core.jwt_validator import TokenPayload
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ...dependencies import get_user_context
from ...dependencies import get_user_context
from services.processing.app.v2.controller.dam_transaction_controller import dam_transaction_controller
from datetime import datetime
from libs.authz.service_auth import internal_headers


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dam-bidding", tags=["DAM Single Bid V2"])
public_router = APIRouter(
    prefix="/dam-bidding/public",
    tags=["DAM Single Bid V2 (Public)"],
    include_in_schema=True,
)


def register_route(method: str, path: str, **kwargs) -> Callable:
    """
    Attach the same handler to both authenticated and public routers.
    This keeps the underlying handler logic untouched as requested.
    """

    def decorator(func: Callable) -> Callable:
        getattr(router, method)(path, **kwargs)(func)
        getattr(public_router, method)(path, **kwargs)(func)
        return func

    return decorator


class PlantModel(BaseModel):
    bid_plant_name: str
    bid_plant_key: str
    bid_plant_category: str
    bid_default_volume: int
    bid_volume: int
    bid_price: float
    bid_default_price: float
    dam_forecast_Price: float
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


class ReferenceData(BaseModel):
    reference_number: str
    bid_status: str
    Remarks: str
    action_by: str
    block_no: int


class SingleBidRequest(BaseModel):
    """Request body for single bid submission"""
    OCFToggle: bool = Field(default=False, description="If true, fetch OCF values from limit_values")
    Data: Dict[str, Any] = Field(..., description="Bid creation data")
    bidAreaId: Optional[str] = Field(default=None, description="Optional bid area ID override")
    SubmitStartTime: Optional[str] = Field(None, description="Submit start time in HH:MM format")


class SingleBidResponse(BaseModel):
    """Response for single bid submission"""
    success: bool
    message: str
    request: Optional[Dict[str, Any]] = None
    response: Optional[Dict[str, Any]] = None


@register_route(
    "get",
    "/single-bid-graph",
    summary="Get DAM single bid graph data",
    response_description="DAM single bid graph response",
)
async def get_dam_single_bid_graph(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)"),
):
    """
    Returns single bid graph data for the requested schedule date and blocks.
    """
    return await dam_bidding_controller.dam_single_bid_graph(schedule_date, from_block, to_block)


@register_route(
    "get",
    "/single-bid-table",
    summary="Get DAM single bid table data",
    response_description="DAM single bid table response",
)
async def get_dam_single_bid_table(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)"),
):
    """
    Returns detailed bid table data for the requested schedule date and blocks.
    """
    return await dam_bidding_controller.dam_single_bid_table(schedule_date, from_block, to_block)


@register_route(
    "get",
    "/approval-request-list",
    summary="List DAM single bid approval requests",
    response_description="Approval request list response",
)
async def get_approval_request_list(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)"),
):
    """
    Returns approval request list for the requested schedule date and blocks.
    """
    return await dam_bidding_controller.approval_request_list(schedule_date, from_block, to_block)


@register_route(
    "post",
    "/bid-creation",
    summary="Create DAM bid data",
    response_description="Processed bid block data",
)
async def bid_creation_post(
    post_data: dict = Body(
        ...,
        description=(
            "Bid data for multiple blocks. Include Ex_split_string_{blockNo} and "
            "optional ocf_config_{blockNo} (or ocf_config) as "
            "{allocation%}_{O|S}_{premium_or_discount}[_{max_ocf_qty}]."
        ),
    ),
):
    """
    Submit bid data for sell/buy types for given time blocks.
    """
    return await dam_bidding_controller.BidCreationpost(post_data)


@register_route(
    "post",
    "/submit-for-approval",
    summary="Submit DAM bid approval data",
    response_description="Returns success status after processing bid data",
)
async def submit_for_approval_post_data(
    post_data: dict = Body(
        ...,
        description=(
            "Submit bid approval data. Optional: manual_capture_bid=true for manual capture. "
            "Optional ocf-config (or per-block ocf-config_blk_{n}): "
            "{allocation%}_{O|S}_{premium_or_discount}[_{max_ocf_qty}] e.g. 60_O_14, 20_S_0, "
            "or 60_O_14_100 (buy: stores ocf_premium_discount and max_ocf_qty). Stored as `ocf`."
        ),
    ),
    current_user: TokenPayload = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    """
    Submit bid data for approval for multiple blocks and plants.
    Auto capture always runs; manual capture runs when `manual_capture_bid` is true.
    """
    user_id = user_context.get("user_id")
    username = user_context.get("username")
    correlation_id = user_context.get("correlation_id")
    try:
        logger.info(f"[Submit For Approval] Request - User: {username} (ID: {user_id}), Correlation ID: {correlation_id}")
        result = await dam_bidding_controller.submit_for_approval_post_data(post_data, current_user, audit_logger)  # type: ignore
        return result
    except Exception as e:
        logger.error(f"[Submit For Approval] Error - User: {username} (ID: {user_id}), Correlation ID: {correlation_id}, Error: {str(e)}")


@register_route(
    "post",
    "/capture-daily-snapshot",
    summary="Trigger DAM daily bid capture snapshot",
    response_description="Returns status of DAM daily bid capture snapshot",
)
async def capture_dam_daily_snapshot():
    """
    Creates daily DAM bid-capture snapshot for both auto and manual DAM capture collections.
    Intended for scheduler trigger at 10:59 IST.
    """
    return await dam_bidding_controller.capture_daily_snapshot()


@register_route(
    "post",
    "/submit-pre-exchange-data",
    summary="Submit DAM pre-exchange data",
    response_description="Returns success status after processing pre-exchange data",
)
async def submit_for_pre_exchange_data(data: List[ReferenceData],user_context: Dict[str, Any] = Depends(get_user_context),audit_logger: AuditLogger = Depends(get_audit_logger)):
    """
    Accepts a list of bid data for pre-exchange processing.
    """
    user_id = user_context.get("user_id")
    username = user_context.get("username")
    correlation_id = user_context.get("correlation_id")
    try:
        logger.info(f"[Submit Pre-Exchange Data] Request - User: {username} (ID: {user_id}), Correlation ID: {correlation_id}")
        
        from types import SimpleNamespace
        current_user = SimpleNamespace(user_id=user_id, username=username, role=user_context.get("roles", "anonymous"))
        result = await dam_bidding_controller.submitForPreExchangeData(data, current_user, audit_logger)  # type: ignore
        return result
    except Exception as e:
        logger.error(f"[Submit Pre-Exchange Data] Error - User: {username} (ID: {user_id}), Correlation ID: {correlation_id}, Error: {str(e)}")


@register_route(
    "get",
    "/bid-status",
    summary="Get DAM bid status data",
    response_description="Returns comprehensive bid status data including trade book, exchange data, and approval status",
)
async def get_bid_status(
    request: Request,
    bid_status_date: Optional[str] = Query(
        None,
        description="Bid status date in YYYY-MM-DD format. If not provided, uses current date or tomorrow based on time.",
        example="2024-01-15"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Retrieves comprehensive bid status data for DAM bidding including:
    - Trade book response data
    - Exchange bid status (single and block bids)
    - Approval status data
    - Revision and order information
    - MCP timeblock data
    - Dispatch schedule history
    
    If bid_status_date is not provided, automatically determines the date:
    - If current time >= 22:30, uses tomorrow's date
    - Otherwise, uses today's date
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[DAM Bid Status] Request - User: {username} (ID: {user_id}), Date: {bid_status_date}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_bid_status_fetch",
            message=f"User {username} (ID: {user_id}) requested DAM bid status data for date {bid_status_date or 'auto'}",
            correlation_id=correlation_id,
            user_id=user_id,
            bid_status_date=bid_status_date
        )
        
        # Convert query parameter to dict format expected by controller
        request_dict = {}
        if bid_status_date:
            request_dict["bid_status_date"] = bid_status_date
        
        result = await dam_bidding_controller.bid_status(request_dict)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dam_bid_status_view",
            message=f"User {username} successfully fetched DAM bid status data",
            username=username,
            correlation_id=correlation_id,
            bid_status_date=bid_status_date,
            success=True
        )
        
        logger.info(f"[DAM Bid Status] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException as e:
        logger.warning(f"[DAM Bid Status] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMBidStatusHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code
        )
        raise
    except Exception as e:
        logger.exception(f"[DAM Bid Status] Error - User: {username}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMBidStatusError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process bid status request."
        )


@register_route(
    "get",
    "/trade-status",
    summary="Get DAM trade status data",
    response_description="Returns comprehensive trade status data including trade book, exchange data, and MCP information",
)
async def get_trade_status(
    request: Request,
    trade_status_date: Optional[str] = Query(
        None,
        description="Trade status date in YYYY-MM-DD format. If not provided, uses current date or tomorrow based on time.",
        example="2024-01-15"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Retrieves comprehensive trade status data for DAM bidding including:
    - Trade book response data
    - Exchange bid status (single and block bids)
    - MCP timeblock data
    - Dispatch schedule history
    
    If trade_status_date is not provided, automatically determines the date:
    - If current time >= 22:30, uses tomorrow's date
    - Otherwise, uses today's date
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[DAM Trade Status] Request - User: {username} (ID: {user_id}), Date: {trade_status_date}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_trade_status_fetch",
            message=f"User {username} (ID: {user_id}) requested DAM trade status data for date {trade_status_date or 'auto'}",
            correlation_id=correlation_id,
            user_id=user_id,
            trade_status_date=trade_status_date
        )
        
        # Convert query parameter to dict format expected by controller
        request_dict = {}
        if trade_status_date:
            request_dict["trade_status_date"] = trade_status_date
        
        result = await dam_bidding_controller.trade_status(request_dict)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dam_trade_status_view",
            message=f"User {username} successfully fetched DAM trade status data",
            username=username,
            correlation_id=correlation_id,
            trade_status_date=trade_status_date,
            success=True
        )
        
        logger.info(f"[DAM Trade Status] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException as e:
        logger.warning(f"[DAM Trade Status] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMTradeStatusHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code
        )
        raise
    except Exception as e:
        logger.exception(f"[DAM Trade Status] Error - User: {username}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMTradeStatusError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process trade status request."
        )

@register_route(
    "post",
    "/submit/single-bid",
    summary="Submit DAM Single Bid",
    response_description="DAM Bid Submission Response",
    response_model=Dict[str, Any]
)
async def submit_single_bid(
    request: SingleBidRequest,
    response: Response,
    current_user: TokenPayload = Depends(get_current_user)
):
    """
    Submit DAM Single Bid to external exchange API
    
    Flow:
    1. Generate Correlation ID
    2. Fetch exchange config
    3. Process bid data (Data)
    4. Submit Bid
    5. Fetch Bid Details (using TransactionId from Submit)
    6. Return Bid Details
    """
    import uuid
    correlation_id = str(uuid.uuid4())
    
    # Set response header
    response.headers["X-Correlation-ID"] = correlation_id

    submit_start_time = datetime.now().time()
    
    result = await dam_transaction_controller.submit_bid(
        ocf_toggle=request.OCFToggle,
        bid_area_id=request.bidAreaId or "",
        current_user=current_user,
        bid_data=request.Data,
        correlation_id=correlation_id,
        start_time=submit_start_time
    )
    
    return result


@register_route(
    "post",
    "/cancel-bid",
    summary="Cancel DAM Single Bid",
    response_description="DAM Bid Cancellation Response",
    response_model=Dict[str, Any]
)
async def cancel_dam_bid(
    response: Response,
    current_user: TokenPayload = Depends(get_current_user)
):
    """
    Cancel all DAM Single Bids for today's delivery date
    
    Flow:
    1. Get delivery date from exchange
    2. Check for existing bids
    3. Cancel all if found
    4. Return cancellation result
    """
    import uuid
    correlation_id = str(uuid.uuid4())
    
    # Set response header
    response.headers["X-Correlation-ID"] = correlation_id
    
    result = await dam_transaction_controller.cancel_bid(
        correlation_id=correlation_id
    )
    
    return result


@register_route(
    "get",
    "/single-bid/bid-submission-detail",
    summary="Get DAM bid submission details",
    response_description="DAM bid submission details response",
)
async def get_bid_submission_detail(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get DAM bid submission details for a specific date.
    """
    return await dam_bidding_controller.get_bid_submission_details(date)

@register_route(
    "get",
    "/single-bid/bid-book",
    summary="Get DAM Single Bid Book",
    response_description="DAM Single Bid Book Response (T+1)",
)
async def get_dam_single_bid_book(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get DAM Single Bid Book details for the valid delivery date (T+1).
    """
    return await dam_bidding_controller.get_dam_single_bid_book(date)

@register_route(
    "get",
    "/single-bid/trade-book",
    summary="Get DAM Single Trade Book",
    response_description="DAM Single Trade Book Response (T+1)",
)
async def get_dam_single_trade_book(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get DAM Single Trade Book details for the valid delivery date (T+1).
    """
    return await dam_bidding_controller.get_dam_single_trade_book(date)


@register_route(
    "get",
    "/run-live-sync",
    summary="Trigger DAM Bid Trade Live Sync",
    response_description="DAM bid trade live sync result from scheduler",
    response_model=Dict[str, Any]
)
async def run_dam_live_sync(
    request: Request,
):
    """
    Trigger DAM bid trade live sync via the scheduler service.
    Proxies the request to the scheduler's BidTradeLiveSyncProcessor.execute_dam_live_sync().
    """
    import httpx
    from services.processing.app.core.config import settings

    try:
        scheduler_url = settings.SCHEDULER_SERVICE_URL
        endpoint = f"{scheduler_url}/api/v1/scheduler/bid-trade-live-sync/trigger-dam"

        logger.info(f"[DAM Live Sync] Calling scheduler service at {endpoint}")

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                endpoint,
                headers=internal_headers(settings.INTERNAL_SERVICE_TOKEN),
            )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"[DAM Live Sync] Scheduler service returned error: {response.status_code} - {error_detail}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Scheduler service error: {error_detail}"
                )

            result = response.json()
            logger.info(f"[DAM Live Sync] Received response from scheduler: status={result.get('status')}")
            return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[DAM Live Sync] Error calling scheduler service")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger DAM live sync: {str(e)}"
        )


__all__ = ["router", "public_router"]