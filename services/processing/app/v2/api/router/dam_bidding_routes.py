from functools import wraps
from typing import Any, Dict, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from datetime import datetime
import logging

import pytz

from ...controller.dam_bidding_controller import dam_bidding_controller
from ...controller.dam_transaction_controller import dam_transaction_controller
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from libs.middleware import get_correlation_id
from ...dependencies import get_user_context

router = APIRouter(prefix="/dam-bidding", tags=["DAM Bidding V2"])
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
    block_no: str


class DAMBiddingResponse(BaseModel):
    """
    Response schema for DAM Bidding APIs.
    """
    data: Dict[str, Any] = Field(..., description="Payload data")


class ErrorResponse(BaseModel):
    detail: Any

    class Config:
        json_schema_extra = {"example": {"detail": "Failed to process DAM bidding request."}}


# -------- Helper Functions --------
def build_req_meta(request: Request) -> Dict[str, Any]:
    """
    Build request metadata for audit logging.
    """
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "method": request.method,
        "path": request.url.path,
        "timestamp": datetime.now().isoformat()
    }


@router.get(
    "/single-bid-graph",
    response_model=DAMBiddingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DAM Single Bid Graph Data",
    response_description="DAM single bid graph data with all 96 time blocks",
    responses={
        200: {"description": "OK", "model": DAMBiddingResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def get_dam_single_bid_graph(
    request: Request,
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to tomorrow's date for DAM"
    ),
    from_block: int = Query(1, description="Starting time block (1-96, default=1)"),
    to_block: int = Query(96, description="Ending time block (1-96, default=96)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns DAM single bid graph data for visualization.
    For DAM, shows all 96 time blocks by default and uses tomorrow's data.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_single_bid_graph_fetch",
            message="Starting DAM single bid graph data fetch operation",
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            user_id=user_id
        )
        
        result = await dam_bidding_controller.dam_single_bid_graph(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dam_single_bid_graph_view",
            message="User successfully fetched DAM single bid graph data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=len(result.get("data", [])) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"DAM single bid graph data fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
        return DAMBiddingResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMSingleBidGraphFetchError",
            error_message=f"Failed to fetch DAM single bid graph data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )
        
        logger.exception("Error fetching DAM single bid graph data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DAM single bid graph data."
        )


@router.get(
    "/single-bid-table",
    response_model=DAMBiddingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DAM Single Bid Table Data",
    response_description="DAM single bid table data with all 96 time blocks",
    responses={
        200: {"description": "OK", "model": DAMBiddingResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def get_dam_single_bid_table(
    request: Request,
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to tomorrow's date for DAM"
    ),
    from_block: int = Query(1, description="Starting time block (1-96, default=1)"),
    to_block: int = Query(96, description="Ending time block (1-96, default=96)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns DAM single bid table data.
    For DAM, shows all 96 time blocks by default and uses tomorrow's data.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_single_bid_table_fetch",
            message="Starting DAM single bid table data fetch operation",
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            user_id=user_id
        )
        
        result = await dam_bidding_controller.dam_single_bid_table(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dam_single_bid_table_view",
            message="User successfully fetched DAM single bid table data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=len(result.get("data", {}).get("bid_data", [])) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"DAM single bid table data fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
        return DAMBiddingResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMSingleBidTableFetchError",
            error_message=f"Failed to fetch DAM single bid table data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )
        
        logger.exception("Error fetching DAM single bid table data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DAM single bid table data."
        )


@router.get(
    "/approval-request-list",
    response_model=DAMBiddingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DAM Approval Request List",
    response_description="DAM approval request list data",
    responses={
        200: {"description": "OK", "model": DAMBiddingResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def get_dam_approval_request_list(
    request: Request,
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to tomorrow's date for DAM"
    ),
    from_block: int = Query(1, description="Starting time block (1-96, default=1)"),
    to_block: int = Query(96, description="Ending time block (1-96, default=96)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns DAM approval request list data.
    For DAM, shows all 96 time blocks by default and uses tomorrow's data.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_approval_request_list_fetch",
            message="Starting DAM approval request list fetch operation",
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            user_id=user_id
        )
        
        result = await dam_bidding_controller.approval_request_list(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dam_approval_request_list_view",
            message="User successfully fetched DAM approval request list",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=len(result.get("data", [])) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"DAM approval request list fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
        return DAMBiddingResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMApprovalRequestListFetchError",
            error_message=f"Failed to fetch DAM approval request list: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )
        
        logger.exception("Error fetching DAM approval request list")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DAM approval request list."
        )


@router.post(
    "/bid-creation",
    response_model=DAMBiddingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create DAM Bid Data",
    response_description="DAM bid creation result",
    responses={
        201: {"description": "Created", "model": DAMBiddingResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def create_dam_bid(
    request: Request,
    post_data: Dict[str, Any],
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Create DAM bid data for multiple blocks.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_bid_creation",
            message="Starting DAM bid creation operation",
            correlation_id=correlation_id,
            user_id=user_id
        )
        
        result = await dam_bidding_controller.bid_creation_post(
            post_data=post_data,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dam_bid_creation",
            message="User successfully created DAM bid data",
            username=username,
            correlation_id=correlation_id,
            record_count=result.get("inserted_count", 0) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"DAM bid creation completed successfully for user {user_id}")
        
        return DAMBiddingResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMBidCreationError",
            error_message=f"Failed to create DAM bid data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        
        logger.exception("Error creating DAM bid data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create DAM bid data."
        )


@router.post(
    "/submit-for-approval",
    response_model=DAMBiddingResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit DAM Bid for Approval",
    response_description="DAM bid approval submission result",
    responses={
        200: {"description": "OK", "model": DAMBiddingResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def submit_dam_bid_for_approval(
    request: Request,
    request_data: RequestModel,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Submit DAM bid data for approval workflow.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_bid_approval_submission",
            message="Starting DAM bid approval submission operation",
            correlation_id=correlation_id,
            user_id=user_id
        )
        
        result = await dam_bidding_controller.submit_for_approval_post_data(
            request_data=request_data.dict(),
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="approval",
            message="Approver will approve DAM single BId",
            username=username,
            correlation_id=correlation_id,
            record_count=result.get("submitted_count", 0) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"DAM bid approval submission completed successfully for user {user_id}")
        
        return DAMBiddingResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMBidApprovalSubmissionError",
            error_message=f"Failed to submit DAM bid for approval: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        
        logger.exception("Error submitting DAM bid for approval")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit DAM bid for approval."
        )


@router.post(
    "/submit-for-pre-exchange",
    response_model=DAMBiddingResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit DAM Bid for Pre-Exchange Processing",
    response_description="DAM pre-exchange processing result",
    responses={
        200: {"description": "OK", "model": DAMBiddingResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def submit_dam_bid_for_pre_exchange(
    request: Request,
    data: List[ReferenceData],
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Submit DAM bid data for pre-exchange processing.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_bid_pre_exchange_submission",
            message="Starting DAM bid pre-exchange submission operation",
            correlation_id=correlation_id,
            user_id=user_id
        )
        
        result = await dam_bidding_controller.submit_for_pre_exchange_data(
            data=[item.dict() for item in data],
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dam_bid_pre_exchange_submission",
            message="User successfully submitted DAM bid for pre-exchange processing",
            username=username,
            correlation_id=correlation_id,
            record_count=result.get("updated_count", 0) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"DAM bid pre-exchange submission completed successfully for user {user_id}")
        
        return DAMBiddingResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMBidPreExchangeSubmissionError",
            error_message=f"Failed to submit DAM bid for pre-exchange processing: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        
        logger.exception("Error submitting DAM bid for pre-exchange processing")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit DAM bid for pre-exchange processing."
        )


@router.get(
    "/scheduling-calculation",
    response_model=DAMBiddingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DAM Scheduling Calculation Data",
    response_description="DAM scheduling calculation data",
    responses={
        200: {"description": "OK", "model": DAMBiddingResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def get_dam_scheduling_calculation(
    request: Request,
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to tomorrow's date for DAM"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns DAM scheduling calculation data.
    For DAM, uses tomorrow's date by default.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_scheduling_calculation_fetch",
            message="Starting DAM scheduling calculation fetch operation",
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            user_id=user_id
        )
        
        result = await dam_bidding_controller.dam_scheduling_cal_table(
            schedule_date=schedule_date,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dam_scheduling_calculation_view",
            message="User successfully fetched DAM scheduling calculation data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=len(result.get("data", [])) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"DAM scheduling calculation data fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
        return DAMBiddingResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMSchedulingCalculationFetchError",
            error_message=f"Failed to fetch DAM scheduling calculation data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )
        
        logger.exception("Error fetching DAM scheduling calculation data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DAM scheduling calculation data."
        )


@router.get(
    "/trade-book",
    response_model=DAMBiddingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DAM Trade Book Data",
    response_description="DAM trade book data from exchange",
    responses={
        200: {"description": "OK", "model": DAMBiddingResponse},
        400: {"description": "Bad Request", "model": ErrorResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def get_dam_trade_book(
    request: Request,
    delivery_date: Optional[int] = Query(
        None, description="DAM Delivery Date timestamp (if not provided, fetches current delivery date)"
    ),
    from_period: str = Query("ALL", description="From Period Id, 'ALL' for all periods"),
    to_period: str = Query("ALL", description="To Period Id, 'ALL' for all periods"),
    buy_sell: str = Query("0", description="Buy/Sell filter: '0'=Both, '1'=Buy, '2'=Sell"),
    bid_area_id: str = Query("ALL", description="Bid Area Id, 'ALL' for all bid areas"),
    trade_id: int = Query(0, description="Trade Id filter, 0 for all trades"),
    order_id: int = Query(0, description="Order Id filter, 0 for all orders"),
    page_number: int = Query(0, description="Page number, 0 for all records"),
    page_size: int = Query(0, description="Page size, 0 for all records (max 96)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns DAM trade book data from the exchange API.
    
    This endpoint fetches completed trade details including:
    - Trade identifiers (TradeId, OrderId)
    - Period information (FromPeriodId, ToPeriodId)
    - Trade details (TradePrice, TradeQuantity, BuySell)
    - Bid type and OCF source information
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_trade_book_fetch",
            message="Starting DAM trade book fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            delivery_date=delivery_date
        )
        
        result = await dam_transaction_controller.get_trade_book(
            delivery_date=delivery_date,
            from_period=from_period,
            to_period=to_period,
            buy_sell=buy_sell,
            bid_area_id=bid_area_id,
            trade_id=trade_id,
            order_id=order_id,
            page_number=page_number,
            page_size=page_size,
            correlation_id=correlation_id
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dam_trade_book_view",
            message="User successfully fetched DAM trade book data",
            username=username,
            correlation_id=correlation_id,
            delivery_date=delivery_date,
            record_count=result.get("total_records", 0) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"DAM trade book data fetched successfully for user {user_id}")
        
        return DAMBiddingResponse(data=result)
        
    except HTTPException:
        raise
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMTradeBookFetchError",
            error_message=f"Failed to fetch DAM trade book data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            delivery_date=delivery_date,
            error_type=type(e).__name__
        )
        
        logger.exception("Error fetching DAM trade book data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DAM trade book data."
        )


@router.get(
    "/price-quantity",
    response_model=DAMBiddingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DAM Price Quantity (MCP) Data",
    response_description="DAM price quantity data from exchange",
    responses={
        200: {"description": "OK", "model": DAMBiddingResponse},
        400: {"description": "Bad Request", "model": ErrorResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def get_dam_price_quantity(
    request: Request,
    delivery_date: Optional[int] = Query(
        None, description="DAM Delivery Date timestamp (if not provided, fetches current delivery date)"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns DAM Price Quantity (MCP) data from the exchange API.
    
    This endpoint fetches Market Clearing Price and Quantity data including:
    - Period information (FromPeriodId, ToPeriodId)
    - Bid Area details (BidArea, Price, BuyQty, SellQty)
    - Last updated time
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dam_price_quantity_fetch",
            message="Starting DAM price quantity fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            delivery_date=delivery_date
        )
        
        result = await dam_transaction_controller.get_price_quantity(
            delivery_date=delivery_date,
            correlation_id=correlation_id
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dam_price_quantity_view",
            message="User successfully fetched DAM price quantity data",
            username=username,
            correlation_id=correlation_id,
            delivery_date=delivery_date,
            period_count=result.get("period_count", 0) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"DAM price quantity data fetched successfully for user {user_id}")
        
        return DAMBiddingResponse(data=result)
        
    except HTTPException:
        raise
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DAMPriceQuantityFetchError",
            error_message=f"Failed to fetch DAM price quantity data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            delivery_date=delivery_date,
            error_type=type(e).__name__
        )
        
        logger.exception("Error fetching DAM price quantity data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DAM price quantity data."
        )
