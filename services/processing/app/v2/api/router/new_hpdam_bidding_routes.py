from typing import Optional, List, Dict, Any
import logging
import uuid

from fastapi import APIRouter, Query, Body, Depends, Request, HTTPException, status, Response
from pydantic import BaseModel, Field

from services.processing.app.v2.controller.new_hpdam_bidding_controller import (
    hpdam_bidding_controller,
)
from services.processing.app.v2.controller.hpdam_transaction_controller import (
    hpdam_transaction_controller,
)
from libs.core.dependencies import get_current_user
from libs.core.jwt_validator import TokenPayload
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ...dependencies import get_user_context
from datetime import datetime


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hpdam-bidding", tags=["HPDAM Bidding V2"])
public_router = APIRouter(
    prefix="/hpdam-bidding/public",
    tags=["HPDAM Bidding V2 (Public)"],
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


class ReferenceData(BaseModel):
    reference_number: str
    bid_status: str
    Remarks: str
    action_by: str
    block_no: int


@public_router.post(
    "/submit/single-bid",
    summary="Submit HPDAM Single Bid",
    response_description="HPDAM Bid Submission Response",
    response_model=Dict[str, Any]
)
async def submit_single_bid(
    request: SingleBidRequest,
    response: Response,
    current_user: TokenPayload = Depends(get_current_user)
):
    """
    Submit HPDAM Single Bid to external exchange API
    """
    correlation_id = str(uuid.uuid4())
    response.headers["X-Correlation-ID"] = correlation_id
    submit_start_time = datetime.now().time()

    result = await hpdam_transaction_controller.submit_bid(
        ocf_toggle=request.OCFToggle,
        bid_area_id=request.bidAreaId or "",
        current_user=current_user,
        bid_data=request.Data,
        correlation_id=correlation_id,
        start_time=submit_start_time
    )
    return result


@register_route(
    "get",
    "/single-bid/bid-submission-detail",
    summary="Get HPDAM bid submission details",
    response_description="HPDAM bid submission details response",
)
async def get_bid_submission_detail(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get HPDAM bid submission details for a specific date.
    """
    return await hpdam_bidding_controller.get_bid_submission_details(date)


@register_route(
    "get",
    "/single-bid/bid-book",
    summary="Get HPDAM Single Bid Book",
    response_description="HPDAM Single Bid Book Response",
)
async def get_hpdam_single_bid_book(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get HPDAM Single Bid Book details for the valid delivery date.
    """
    return await hpdam_bidding_controller.get_dam_single_bid_book(date)


@register_route(
    "get",
    "/single-bid/trade-book",
    summary="Get HPDAM Single Trade Book",
    response_description="HPDAM Single Trade Book Response",
)
async def get_hpdam_single_trade_book(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get HPDAM Single Trade Book details for the valid delivery date.
    """
    return await hpdam_bidding_controller.get_dam_single_trade_book(date)


@public_router.get(
    "/single-bid-graph",
    summary="Get HPDAM single bid graph data",
    response_description="HPDAM single bid graph response",
)
async def get_hpdam_single_bid_graph(
    schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)"),
):
    """Returns single bid graph data for the requested schedule date and blocks."""
    return await hpdam_bidding_controller.dam_single_bid_graph(schedule_date, from_block, to_block)


@public_router.get(
    "/single-bid-table",
    summary="Get HPDAM single bid table data",
    response_description="HPDAM single bid table response",
)
async def get_hpdam_single_bid_table(
    schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)"),
):
    """Returns detailed bid table data for the requested schedule date and blocks."""
    return await hpdam_bidding_controller.dam_single_bid_table(schedule_date, from_block, to_block)


@public_router.get(
    "/approval-request-list",
    summary="List HPDAM single bid approval requests",
    response_description="Approval request list response",
)
async def get_hpdam_approval_request_list(
    schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)"),
):
    """Returns approval request list for the requested schedule date and blocks."""
    return await hpdam_bidding_controller.approval_request_list(schedule_date, from_block, to_block)


@public_router.post(
    "/bid-creation",
    summary="Create HPDAM bid data",
    response_description="Processed bid block data",
)
async def hpdam_bid_creation_post(
    post_data: dict = Body(..., description="Bid data for multiple blocks"),
):
    """Submit bid data for sell/buy types for given time blocks."""
    return await hpdam_bidding_controller.BidCreationpost(post_data)


@public_router.post(
    "/submit-for-approval",
    summary="Submit HPDAM bid approval data",
    response_description="Returns success status after processing bid data",
)
async def hpdam_submit_for_approval_post_data(
    post_data: dict = Body(..., description="Submit bid approval data."),
    current_user: TokenPayload = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    """Submit bid data for approval for multiple blocks and plants."""
    user_id = user_context.get("user_id")
    username = user_context.get("username")
    correlation_id = user_context.get("correlation_id")
    try:
        logger.info(f"[HPDAM Submit For Approval] Request - User: {username} (ID: {user_id}), Correlation ID: {correlation_id}")
        return await hpdam_bidding_controller.submit_for_approval_post_data(post_data, current_user, audit_logger)
    except Exception as e:
        logger.error(f"[HPDAM Submit For Approval] Error - User: {username} (ID: {user_id}), Correlation ID: {correlation_id}, Error: {str(e)}")
        raise


@public_router.post(
    "/capture-daily-snapshot",
    summary="Trigger HPDAM daily bid capture snapshot",
    response_description="Returns status of HPDAM daily bid capture snapshot",
)
async def capture_hpdam_daily_snapshot():
    """Creates daily HPDAM bid-capture snapshot for both auto and manual capture collections."""
    return await hpdam_bidding_controller.capture_daily_snapshot()


@public_router.post(
    "/submit-pre-exchange-data",
    summary="Submit HPDAM pre-exchange data",
    response_description="Returns success status after processing pre-exchange data",
)
async def hpdam_submit_for_pre_exchange_data(
    data: List[ReferenceData],
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Accepts a list of bid data for pre-exchange processing."""
    user_id = user_context.get("user_id")
    username = user_context.get("username")
    correlation_id = user_context.get("correlation_id")
    try:
        logger.info(f"[HPDAM Submit Pre-Exchange Data] Request - User: {username} (ID: {user_id}), Correlation ID: {correlation_id}")
        from types import SimpleNamespace
        current_user = SimpleNamespace(user_id=user_id, username=username, role=user_context.get("roles", "anonymous"))
        return await hpdam_bidding_controller.submitForPreExchangeData(data, current_user, audit_logger)
    except Exception as e:
        logger.error(f"[HPDAM Submit Pre-Exchange Data] Error - User: {username} (ID: {user_id}), Correlation ID: {correlation_id}, Error: {str(e)}")
        raise


@public_router.get(
    "/trade-status",
    summary="Get HPDAM trade status data",
    response_description="Returns comprehensive trade status data",
)
async def get_hpdam_trade_status(
    request: Request,
    trade_status_date: Optional[str] = Query(None, description="Trade status date in YYYY-MM-DD format"),
):
    """Retrieves comprehensive trade status data for HPDAM bidding."""
    request_dict = {}
    if trade_status_date:
        request_dict["trade_status_date"] = trade_status_date
    return await hpdam_bidding_controller.trade_status(request_dict)


@public_router.post(
    "/cancel-bid",
    summary="Cancel HPDAM Single Bid",
    response_description="HPDAM Bid Cancellation Response",
    response_model=Dict[str, Any],
)
async def cancel_hpdam_bid(
    response: Response,
    current_user: TokenPayload = Depends(get_current_user),
):
    """Cancel all HPDAM Single Bids for today's delivery date."""
    correlation_id = str(uuid.uuid4())
    response.headers["X-Correlation-ID"] = correlation_id
    return await hpdam_transaction_controller.cancel_bid(correlation_id=correlation_id)


__all__ = ["public_router"]
