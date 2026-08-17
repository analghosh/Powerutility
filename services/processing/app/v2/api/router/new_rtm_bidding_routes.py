from typing import Optional, List, Callable, Dict, Any
import logging

from fastapi import APIRouter, Query, Body, Depends, Request, HTTPException, status
from pydantic import BaseModel

from services.processing.app.v2.controller.new_rtm_bidding_controller import (
    rtm_bidding_controller,
)
from services.processing.app.v2.controller.rtm_transaction_controller import rtm_transaction_controller
from fastapi import Response
from libs.core.dependencies import get_current_user
from libs.core.jwt_validator import TokenPayload
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ...dependencies import get_user_context
from datetime import datetime
from libs.authz.service_auth import internal_headers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rtm-bidding", tags=["RTM Single Bid V2"])
public_router = APIRouter(
    prefix="/rtm-bidding/public",
    tags=["RTM Single Bid V2 (Public)"],
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


class ReferenceData(BaseModel):
    reference_number: str
    bid_status: str
    Remarks: str
    action_by: str
    block_no: int


@register_route(
    "get",
    "/single-bid-graph",
    summary="Get RTM single bid graph data",
    response_description="RTM single bid graph response",
)
async def get_rtm_single_bid_graph(
    request: Request,
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Returns single bid graph data for the requested schedule date and blocks.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[Single Bid Graph] Request - User: {username} (ID: {user_id}), Date: {schedule_date}, Blocks: {from_block}-{to_block}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_single_bid_graph_fetch",
            message=f"User {username} (ID: {user_id}) requested RTM single bid graph data",
            correlation_id=correlation_id,
            user_id=user_id,
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block
        )
        
        result = await rtm_bidding_controller.rtm_single_bid_graph(schedule_date, from_block, to_block)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_single_bid_graph_view",
            message=f"User {username} successfully fetched RTM single bid graph data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            success=True
        )
        
        logger.info(f"[Single Bid Graph] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException as e:
        logger.warning(f"[Single Bid Graph] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSingleBidGraphHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code
        )
        raise
    except Exception as e:
        logger.exception(f"[Single Bid Graph] Error - User: {username}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSingleBidGraphError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process single bid graph request."
        )


@register_route(
    "get",
    "/single-bid-table",
    summary="Get RTM single bid table data",
    response_description="RTM single bid table response",
)
async def get_rtm_single_bid_table(
    request: Request,
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Returns detailed bid table data for the requested schedule date and blocks.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[Single Bid Table] Request - User: {username} (ID: {user_id}), Date: {schedule_date}, Blocks: {from_block}-{to_block}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_single_bid_table_fetch",
            message=f"User {username} (ID: {user_id}) requested RTM single bid table data",
            correlation_id=correlation_id,
            user_id=user_id,
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block
        )
        
        result = await rtm_bidding_controller.rtm_single_bid_table(schedule_date, from_block, to_block)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_single_bid_table_view",
            message=f"User {username} successfully fetched RTM single bid table data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            success=True
        )
        
        logger.info(f"[Single Bid Table] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException as e:
        logger.warning(f"[Single Bid Table] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSingleBidTableHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code
        )
        raise
    except Exception as e:
        logger.exception(f"[Single Bid Table] Error - User: {username}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSingleBidTableError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process single bid table request."
        )


@register_route(
    "get",
    "/approval-request-list",
    summary="List RTM single bid approval requests",
    response_description="Approval request list response",
)
async def get_approval_request_list(
    request: Request,
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Returns approval request list for the requested schedule date and blocks.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[Approval Request List] Request - User: {username} (ID: {user_id}), Date: {schedule_date}, Blocks: {from_block}-{to_block}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_approval_request_list_fetch",
            message=f"User {username} (ID: {user_id}) requested RTM approval request list",
            correlation_id=correlation_id,
            user_id=user_id,
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block
        )
        
        result = await rtm_bidding_controller.approval_request_list(schedule_date, from_block, to_block)
        
        record_count = len(result.get("data", [])) if isinstance(result, dict) and "data" in result else 0
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_approval_request_list_view",
            message=f"User {username} successfully fetched RTM approval request list",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=record_count,
            success=True
        )
        
        logger.info(f"[Approval Request List] Success - User: {username}, Records: {record_count}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException as e:
        logger.warning(f"[Approval Request List] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMApprovalRequestListHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code
        )
        raise
    except Exception as e:
        logger.exception(f"[Approval Request List] Error - User: {username}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMApprovalRequestListError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process approval request list."
        )


@register_route(
    "post",
    "/bid-creation",
    summary="Create RTM bid data",
    response_description="Processed bid block data",
)
async def bid_creation_post(
    request: Request,
    post_data: dict = Body(..., description="Bid data for multiple blocks"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Submit bid data for sell/buy types for given time blocks.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Extract relevant info from post_data for logging
        blocks_count = len(post_data.get("data", [])) if isinstance(post_data, dict) else 0
        
        logger.info(f"[Bid Creation] Request - User: {username} (ID: {user_id}), Blocks: {blocks_count}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_bid_creation",
            message=f"User {username} (ID: {user_id}) creating RTM bid data for {blocks_count} blocks",
            correlation_id=correlation_id,
            user_id=user_id,
            blocks_count=blocks_count
        )
        
        result = await rtm_bidding_controller.BidCreationpost(post_data, user_context.get("username"))
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_bid_creation",
            message=f"User {username} successfully created RTM bid data",
            username=username,
            correlation_id=correlation_id,
            blocks_count=blocks_count,
            success=True
        )
        
        logger.info(f"[Bid Creation] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException as e:
        logger.warning(f"[Bid Creation] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMBidCreationHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code
        )
        raise
    except Exception as e:
        logger.exception(f"[Bid Creation] Error - User: {username}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMBidCreationError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process bid creation request."
        )


@register_route(
    "post",
    "/submit-for-approval",
    summary="Submit RTM bid approval data",
    response_description="Returns success status after processing bid data",
)
async def submit_for_approval_post_data(
    request: Request,
    post_data: dict = Body(..., description="Submit bid approval data"),
    current_user: TokenPayload = Depends(get_current_user),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Submit bid data for approval for multiple blocks and plants.
    Body may include "manual_capture_bid": true to additionally store capture in manual table.
    """
    user_id = user_context.get("user_id", current_user.user_id if hasattr(current_user, 'user_id') else "anonymous")
    username = user_context.get("username", current_user.username if hasattr(current_user, 'username') else "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Extract relevant info from post_data for logging
        blocks_count = len(post_data.get("data", [])) if isinstance(post_data, dict) else 0
        
        logger.info(f"[Submit for Approval] Request - User: {username} (ID: {user_id}), Blocks: {blocks_count}, Correlation ID: {correlation_id}")
        
        result = await rtm_bidding_controller.submit_for_approval_post_data(post_data, current_user, audit_logger)
        
        logger.info(f"[Submit for Approval] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException as e:
        logger.warning(f"[Submit for Approval] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        raise
    except Exception as e:
        logger.exception(f"[Submit for Approval] Error - User: {username}, Correlation ID: {correlation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process submit for approval request."
        )


@register_route(
    "post",
    "/submit-pre-exchange-data",
    summary="Submit RTM pre-exchange data",
    response_description="Returns success status after processing pre-exchange data",
)
async def submit_for_pre_exchange_data(
    request: Request,
    data: List[ReferenceData],
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Accepts a list of bid data for pre-exchange processing.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        records_count = len(data) if isinstance(data, list) else 0
        
        logger.info(f"[Submit Pre-Exchange Data] Request - User: {username} (ID: {user_id}), Records: {records_count}, Correlation ID: {correlation_id}")
        
        from types import SimpleNamespace
        current_user = SimpleNamespace(user_id=user_id, username=username, role=user_context.get("roles", "anonymous"))
        result = await rtm_bidding_controller.submitForPreExchangeData(data, current_user, audit_logger)  # type: ignore
        
        logger.info(f"[Submit Pre-Exchange Data] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException as e:
        logger.warning(f"[Submit Pre-Exchange Data] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        raise
    except Exception as e:
        logger.exception(f"[Submit Pre-Exchange Data] Error - User: {username}, Correlation ID: {correlation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process pre-exchange data request."
        )


@register_route(
    "get",
    "/bid-status",
    summary="Get RTM bid status data",
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
    Retrieves comprehensive bid status data for RTM bidding including:
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
        logger.info(f"[Bid Status] Request - User: {username} (ID: {user_id}), Date: {bid_status_date}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_bid_status_fetch",
            message=f"User {username} (ID: {user_id}) requested RTM bid status data for date {bid_status_date or 'auto'}",
            correlation_id=correlation_id,
            user_id=user_id,
            bid_status_date=bid_status_date
        )
        
        # Convert query parameter to dict format expected by controller
        request_dict = {}
        if bid_status_date:
            request_dict["bid_status_date"] = bid_status_date
        
        result = await rtm_bidding_controller.bid_status(request_dict)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_bid_status_view",
            message=f"User {username} successfully fetched RTM bid status data",
            username=username,
            correlation_id=correlation_id,
            bid_status_date=bid_status_date,
            success=True
        )
        
        logger.info(f"[Bid Status] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException as e:
        logger.warning(f"[Bid Status] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMBidStatusHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code
        )
        raise
    except Exception as e:
        logger.exception(f"[Bid Status] Error - User: {username}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMBidStatusError",
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
    summary="Get RTM trade status data",
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
    Retrieves comprehensive trade status data for RTM bidding including:
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
        logger.info(f"[Trade Status] Request - User: {username} (ID: {user_id}), Date: {trade_status_date}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_trade_status_fetch",
            message=f"User {username} (ID: {user_id}) requested RTM trade status data for date {trade_status_date or 'auto'}",
            correlation_id=correlation_id,
            user_id=user_id,
            trade_status_date=trade_status_date
        )
        
        # Convert query parameter to dict format expected by controller
        request_dict = {}
        if trade_status_date:
            request_dict["trade_status_date"] = trade_status_date
        
        result = await rtm_bidding_controller.trade_status(request_dict)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_trade_status_view",
            message=f"User {username} successfully fetched RTM trade status data",
            username=username,
            correlation_id=correlation_id,
            trade_status_date=trade_status_date,
            success=True
        )
        
        logger.info(f"[Trade Status] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException as e:
        logger.warning(f"[Trade Status] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMTradeStatusHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code
        )
        raise
    except Exception as e:
        logger.exception(f"[Trade Status] Error - User: {username}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMTradeStatusError",
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
    summary="Submit RTM Single Bid",
    response_description="RTM Bid Submission Response",
    response_model=Dict[str, Any]
)
async def submit_single_bid(
    request: Request,
    response: Response,
    bid_data: Dict[str, Any] = Body(..., description="RTM Bid Data"),
    current_user: TokenPayload = Depends(get_current_user),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    RTM Single Bid Submission
    Triggers internal bid creation and submits to Exchange API
    """
    import uuid
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id") or str(uuid.uuid4())
    
    response.headers["X-Correlation-ID"] = correlation_id
    
    try:
        submit_start_time = datetime.now().time()
        logger.info(f"[RTM Submit Bid] Request - User: {username}, Correlation ID: {correlation_id}, Start Time: {submit_start_time}")
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_submit_bid",
            message=f"User {username} initiating RTM Bid Submission",
            correlation_id=correlation_id,
            user_id=user_id
        )
        
        result = await rtm_transaction_controller.submit_bid(
            current_user=current_user,
            bid_data=bid_data,
            correlation_id=correlation_id,
            start_time=submit_start_time
        )
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_submit_bid",
            message=f"User {username} successfully submitted RTM bid",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        
        return result
        
    except HTTPException as e:
        logger.warning(f"[RTM Submit Bid] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        raise
    except Exception as e:
        logger.exception(f"[RTM Submit Bid] Error - User: {username}, Correlation ID: {correlation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit RTM bid: {str(e)}"
        )


@register_route(
    "post",
    "/submit/sequential-bid",
    summary="Submit RTM Sequential Bid (one per block)",
    response_description="RTM Sequential Bid Submission Response",
    response_model=Dict[str, Any]
)
async def submit_sequential_bid(
    request: Request,
    response: Response,
    bid_data: Dict[str, Any] = Body(..., description="RTM Bid Data"),
    current_user: TokenPayload = Depends(get_current_user),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    RTM Sequential Bid Submission
    Submits one bid per block sequentially to avoid 429 rate-limit issues.
    Same data format as /submit/single-bid but processes each block individually.
    """
    import uuid
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id") or str(uuid.uuid4())
    
    response.headers["X-Correlation-ID"] = correlation_id
    
    try:
        submit_start_time = datetime.now().time()
        logger.info(f"[RTM Sequential Submit] Request - User: {username}, Correlation ID: {correlation_id}, Start Time: {submit_start_time}")
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_submit_sequential_bid",
            message=f"User {username} initiating RTM Sequential Bid Submission",
            correlation_id=correlation_id,
            user_id=user_id
        )
        
        result = await rtm_transaction_controller.submit_sequential_bid(
            current_user=current_user,
            bid_data=bid_data,
            correlation_id=correlation_id,
            start_time=submit_start_time
        )
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_submit_sequential_bid",
            message=f"User {username} completed RTM sequential bid submission",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        
        return result
        
    except HTTPException as e:
        logger.warning(f"[RTM Sequential Submit] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        raise
    except Exception as e:
        logger.exception(f"[RTM Sequential Submit] Error - User: {username}, Correlation ID: {correlation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit RTM sequential bid: {str(e)}"
        )

@register_route(
    "get",
    "/single-bid/bid-submission-detail",
    summary="Get RTM bid submission details",
    response_description="RTM bid submission details response",
)
async def get_bid_submission_detail(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get RTM bid submission details for a specific date.
    """
    return await rtm_bidding_controller.get_bid_submission_details(date)


class WithdrawBidRequest(BaseModel):
    """Request body for RTM bid withdrawal"""
    time_blocks: List[int]
    schedule_date: Optional[str] = None  # YYYY-MM-DD format


@router.post("/withdraw-bid", response_model=Dict[str, Any])
async def withdraw_rtm_bid(
    request_body: WithdrawBidRequest,
    response: Response,
    current_user: TokenPayload = Depends(get_current_user),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Withdraw RTM bids for specific time blocks.
    
    Accepts a list of time block integers (1-96) and cancels matching bids:
    1. Converts time blocks to session IDs
    2. Fetches existing RTM bids
    3. Matches sessions with request
    4. Cancels matching orders
    """
    import uuid
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id") or str(uuid.uuid4())
    
    response.headers["X-Correlation-ID"] = correlation_id
    
    try:
        logger.info(f"[RTM Withdraw Bid] Request - User: {username}, Blocks: {request_body.time_blocks}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_withdraw_bid",
            message=f"User {username} withdrawing RTM bids for blocks {request_body.time_blocks}",
            correlation_id=correlation_id,
            user_id=user_id,
            time_blocks=request_body.time_blocks
        )
        
        result = await rtm_transaction_controller.withdraw_bid(
            time_blocks=request_body.time_blocks,
            correlation_id=correlation_id,
            schedule_date=request_body.schedule_date
        )
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_withdraw_bid",
            message=f"User {username} withdrew RTM bids",
            username=username,
            correlation_id=correlation_id,
            success=result.get("success", False)
        )
        
        return result
        
    except HTTPException as e:
        logger.warning(f"[RTM Withdraw Bid] HTTP error - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        raise
    except Exception as e:
        logger.exception(f"[RTM Withdraw Bid] Error - User: {username}, Correlation ID: {correlation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to withdraw RTM bids: {str(e)}"
        )


@register_route(
    "get",
    "/single-bid/bid-book",
    summary="Get RTM Single Bid Book",
    response_description="RTM Single Bid Book Response",
)
async def get_rtm_single_bid_book(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get RTM Single Bid Book details for a specific date.
    """
    return await rtm_bidding_controller.get_rtm_single_bid_book(date)


@register_route(
    "get",
    "/single-bid/trade-book",
    summary="Get RTM Single Trade Book",
    response_description="RTM Single Trade Book Response",
)
async def get_rtm_single_trade_book(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get RTM Single Trade Book details.
    """
    return await rtm_bidding_controller.get_rtm_single_trade_book(date)


@register_route(
    "get",
    "/trade-book",
    summary="Get RTM Trade Book Data",
    response_description="RTM trade book data from exchange",
)
async def get_rtm_trade_book(
    request: Request,
    delivery_date: Optional[int] = Query(
        None, description="RTM Delivery Date timestamp (if not provided, fetches current delivery date)"
    ),
    session: int = Query(0, description="Session filter, 0 for all sessions"),
    from_period: str = Query("ALL", description="From Period Id, 'ALL' for all periods"),
    to_period: str = Query("ALL", description="To Period Id, 'ALL' for all periods"),
    buy_sell: str = Query("ALL", description="Buy/Sell filter: 'ALL'=Both, 'B'=Buy, 'S'=Sell"),
    bid_area_id: str = Query("ALL", description="Bid Area Id, 'ALL' for all bid areas"),
    trade_id: int = Query(0, description="Trade Id filter, 0 for all trades"),
    order_id: int = Query(0, description="Order Id filter, 0 for all orders"),
    sorting_type: int = Query(
        2, 
        description="Sorting options: 3=OrderId Asc, 4=OrderId Desc, 5=PortfolioId Asc, 6=PortfolioId Desc, 7=FromPeriodId Asc, 8=FromPeriodId Desc, 9=TradeId Asc, 10=TradeId Desc"
    ),
    page_number: int = Query(0, description="Page number, 0 for all records"),
    page_size: int = Query(0, description="Page size, 0 for all records (max 96)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns RTM trade book data from the exchange API.
    
    This endpoint fetches completed trade details including:
    - Trade identifiers (TradeId, OrderId)
    - Session and Period information
    - Trade details (TradePrice, TradeQuantity, BuySell)
    - Bid type information
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[RTM Trade Book] Request - User: {username}, DeliveryDate: {delivery_date}, Session: {session}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_trade_book_fetch",
            message="Starting RTM trade book fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            delivery_date=delivery_date,
            session=session
        )
        
        result = await rtm_transaction_controller.get_trade_book(
            delivery_date=delivery_date,
            session=session,
            from_period=from_period,
            to_period=to_period,
            buy_sell=buy_sell,
            bid_area_id=bid_area_id,
            trade_id=trade_id,
            order_id=order_id,
            sorting_type=sorting_type,
            page_number=page_number,
            page_size=page_size,
            correlation_id=correlation_id
        )
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_trade_book_view",
            message="User successfully fetched RTM trade book data",
            username=username,
            correlation_id=correlation_id,
            delivery_date=delivery_date,
            record_count=result.get("total_records", 0) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"[RTM Trade Book] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMTradeBookFetchError",
            error_message=f"Failed to fetch RTM trade book data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            delivery_date=delivery_date,
            error_type=type(e).__name__
        )
        
        logger.exception(f"[RTM Trade Book] Error - User: {username}, Correlation ID: {correlation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch RTM trade book data."
        )


@register_route(
    "get",
    "/price-quantity",
    summary="Get RTM Price Quantity (MCP) Data",
    response_description="RTM price quantity data from exchange",
)
async def get_rtm_price_quantity(
    request: Request,
    delivery_date: Optional[int] = Query(
        None, description="RTM Delivery Date timestamp (if not provided, fetches current delivery date using T/T+1 logic)"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns RTM Price Quantity (MCP) data from the exchange API.
    
    This endpoint fetches Market Clearing Price and Quantity data including:
    - Session information
    - Period information (FromPeriodId, ToPeriodId)
    - Bid Area details (BidAreaId, Price, BuyQty, SellQty)
    - Last updated time
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[RTM Price Quantity] Request - User: {username}, DeliveryDate: {delivery_date}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_price_quantity_fetch",
            message="Starting RTM price quantity fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            delivery_date=delivery_date
        )
        
        result = await rtm_transaction_controller.get_price_quantity(
            delivery_date=delivery_date,
            correlation_id=correlation_id
        )
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_price_quantity_view",
            message="User successfully fetched RTM price quantity data",
            username=username,
            correlation_id=correlation_id,
            delivery_date=delivery_date,
            period_count=result.get("period_count", 0) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"[RTM Price Quantity] Success - User: {username}, Correlation ID: {correlation_id}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMPriceQuantityFetchError",
            error_message=f"Failed to fetch RTM price quantity data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            delivery_date=delivery_date,
            error_type=type(e).__name__
        )
        
        logger.exception(f"[RTM Price Quantity] Error - User: {username}, Correlation ID: {correlation_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch RTM price quantity data."
        )


@register_route(
    "get",
    "/run-live-sync",
    summary="Trigger RTM Bid Trade Live Sync",
    response_description="RTM bid trade live sync result from scheduler",
    response_model=Dict[str, Any]
)
async def run_rtm_live_sync(
    request: Request,
):
    """
    Trigger RTM bid trade live sync via the scheduler service.
    Proxies the request to the scheduler's BidTradeLiveSyncProcessor.execute_rtm_live_sync().
    """
    import httpx
    from services.processing.app.core.config import settings

    try:
        scheduler_url = settings.SCHEDULER_SERVICE_URL
        endpoint = f"{scheduler_url}/api/v1/scheduler/bid-trade-live-sync/trigger-rtm"

        logger.info(f"[RTM Live Sync] Calling scheduler service at {endpoint}")

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                endpoint,
                headers=internal_headers(settings.INTERNAL_SERVICE_TOKEN),
            )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"[RTM Live Sync] Scheduler service returned error: {response.status_code} - {error_detail}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Scheduler service error: {error_detail}"
                )

            result = response.json()
            logger.info(f"[RTM Live Sync] Received response from scheduler: status={result.get('status')}")
            return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[RTM Live Sync] Error calling scheduler service")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger RTM live sync: {str(e)}"
        )


__all__ = ["router", "public_router"]