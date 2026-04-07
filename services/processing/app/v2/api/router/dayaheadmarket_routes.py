from typing import Any, Dict, Optional, List
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
import logging

from libs.audit_queue.simple_logger import AuditLogger
from services.processing.app.core.dependencies import get_audit_logger
from services.processing.app.v2.dependencies import get_user_context
from services.processing.app.v2.controller.dayaheadmarket_controller import dayaheadmarket_controller


router = APIRouter(prefix="/dam", tags=["Day-Ahead-Market V2"])

logger = logging.getLogger(__name__)


class DayAheadMarketResponse(BaseModel):
    """
    Response schema for Day Ahead Market APIs.
    """
    data: Dict[str, Any] = Field(..., description="Payload data")


class ErrorResponse(BaseModel):
    detail: Any

    class Config:
        json_schema_extra = {"example": {"detail": "Failed to process Day Ahead Market request."}}


class ReferenceData(BaseModel):
    reference_number: str
    bid_status: str
    Remarks: str
    action_by: str
    block_no: int


@router.get(
    "/single-bid-graph",
    response_model=DayAheadMarketResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Day Ahead Market Single Bid Graph Data",
    response_description="Day Ahead Market single bid graph data with all 96 time blocks",
    responses={
        200: {"description": "OK", "model": DayAheadMarketResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def get_single_bid_garph(
        request: Request,
        schedule_date: Optional[str] = Query(
            None, description="YYYY-MM-DD; defaults to tomorrow's date for dayahead market"
        ),
        from_block: int = Query(1, description="Starting time block (default=1)"),
        to_block: int = Query(96, description="Ending time block (default=96)"),
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns day ahead market graph data for a given schedule_date.
    For day ahead market, shows all 96 blocks and uses tomorrow's date by default.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dayahead_single_bid_graph_fetch",
            message="Starting Day Ahead single bid graph data fetch operation",
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            user_id=user_id
        )
        
        result = await dayaheadmarket_controller.dayahead_single_bid_graph(
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
            action="dayahead_single_bid_graph_view",
            message="User successfully fetched Day Ahead single bid graph data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=len(result.get("data", [])) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"Day Ahead single bid graph data fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
        return DayAheadMarketResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DayAheadSingleBidGraphFetchError",
            error_message=f"Failed to fetch Day Ahead single bid graph data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )
        
        logger.exception("Error fetching Day Ahead single bid graph data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Day Ahead single bid graph data."
        )

@router.get(
    "/single-bid-table",
    response_model=DayAheadMarketResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Day Ahead Market Single Bid Table Data",
    response_description="Day Ahead Market single bid table data with all 96 time blocks",
    responses={
        200: {"description": "OK", "model": DayAheadMarketResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def get_single_bid_table(
        request: Request,
        schedule_date: Optional[str] = Query(
            None, description="YYYY-MM-DD; defaults to tomorrow's date for dayahead market"
        ),
        from_block: int = Query(1, description="Starting time block (default=1)"),
        to_block: int = Query(96, description="Ending time block (default=96)"),
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns day ahead market table data for a given schedule_date.
    For day ahead market, shows all 96 blocks and uses tomorrow's date by default.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dayahead_single_bid_table_fetch",
            message="Starting Day Ahead single bid table data fetch operation",
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            user_id=user_id
        )
        
        result = await dayaheadmarket_controller.dayahead_single_bid_table(
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
            action="dayahead_single_bid_table_view",
            message="User successfully fetched Day Ahead single bid table data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=len(result.get("data", [])) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"Day Ahead single bid table data fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
        return DayAheadMarketResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DayAheadSingleBidTableFetchError",
            error_message=f"Failed to fetch Day Ahead single bid table data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )
        
        logger.exception("Error fetching Day Ahead single bid table data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Day Ahead single bid table data."
        )

@router.get(
    "/approval-list",
    response_model=DayAheadMarketResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Day Ahead Market Approval Request List",
    response_description="Day Ahead Market approval request list for given schedule date",
    responses={
        200: {"description": "OK", "model": DayAheadMarketResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def get_approval_request_list(
        request: Request,
        schedule_date: Optional[str] = Query(
            None, description="YYYY-MM-DD; defaults to tomorrow's date for dayahead market"
        ),
        from_block: int = Query(1, description="Starting time block (default=1)"),
        to_block: int = Query(96, description="Ending time block (default=96)"),
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Returns day ahead market approval request list for a given schedule_date.
    For day ahead market, shows all 96 blocks and uses tomorrow's date by default.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dayahead_approval_request_list_fetch",
            message="Starting Day Ahead approval request list fetch operation",
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            user_id=user_id
        )
        
        result = await dayaheadmarket_controller.approval_request_list(
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
            action="dayahead_approval_request_list_view",
            message="User successfully fetched Day Ahead approval request list",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=len(result.get("result", {}).get("plant_data", {})) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"Day Ahead approval request list fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
        return DayAheadMarketResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DayAheadApprovalRequestListFetchError",
            error_message=f"Failed to fetch Day Ahead approval request list: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )
        
        logger.exception("Error fetching Day Ahead approval request list")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Day Ahead approval request list."
        )

@router.post(
    "/bid-creation",
    response_model=DayAheadMarketResponse,
    status_code=status.HTTP_200_OK,
    summary="Create bid data for Day-Ahead Market",
    response_description="Processed bid block data",
    responses={
        200: {"description": "OK", "model": DayAheadMarketResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def dayahead_bid_creation_post(
        request: Request,
        post_data: dict = Body(..., description="Bid data for multiple blocks"),
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Submit bid data for sell/buy types for given time blocks in dayahead market.

    ### Request Body Example
    ```json
    {
      "sell_buy_bid_type": ["sell", "sell"],
      "time_block_range": ["04:00-04:15_17_2025-10-15", "04:15-04:30_18_2025-10-15"],
      "bid_volume_17": ["196", "119", "111"],
      "bid_price_17": ["2307", "2548", "2569"],
      "Ex_split_string_17": "IEX-100_PXIL-0",
      "bid_volume_18": ["196", "119", "111"],
      "bid_price_18": ["2307", "2548", "2569"],
      "Ex_split_string_18": "IEX-100_PXIL-0",
      "approve_by": ["5f158b66cf650f572054ddc2", "5f158b66cf650f572054ddc2"],
      "reference_number": ["68ee9bcbd35c84056106b0e3"],
      "bid_status": ["approved", "approved"],
      "remarks": ["This data is correct"],
      "bidingDate": "15-10-2025"
    }
    ```
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dayahead_bid_creation",
            message="Starting Day Ahead bid creation operation",
            correlation_id=correlation_id,
            user_id=user_id,
            bid_data_keys=list(post_data.keys()) if isinstance(post_data, dict) else []
        )
        
        result = await dayaheadmarket_controller.BidCreationpost(
            post_data=post_data,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dayahead_bid_creation",
            message="User successfully created Day Ahead bid data",
            username=username,
            correlation_id=correlation_id,
            bid_status=result.get("status", "unknown"),
            success=True
        )
        
        logger.info(f"Day Ahead bid creation completed successfully for user {user_id}")
        
        return DayAheadMarketResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DayAheadBidCreationError",
            error_message=f"Failed to create Day Ahead bid data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        
        logger.exception("Error creating Day Ahead bid data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create Day Ahead bid data."
        )

@router.post(
    "/submit-for-approval",
    response_model=DayAheadMarketResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit dayahead bid approval data",
    response_description="Returns success status after processing bid data",
    responses={
        200: {"description": "OK", "model": DayAheadMarketResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def dayahead_submit_for_approval_post_data(
        request: Request,
        request_data: dict = Body(..., description="Approval request data"),
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Submit dayahead market bid data for approval.
    
    ### Request Body Example
    ```json
    {
      "schedule_date": "2025-10-16",
      "from_block": 1,
      "to_block": 96,
      "plant_data": [...],
      "block_data": [...]
    }
    ```
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dayahead_submit_for_approval",
            message="Starting Day Ahead bid approval submission operation",
            correlation_id=correlation_id,
            user_id=user_id,
            request_data_keys=list(request_data.keys()) if isinstance(request_data, dict) else []
        )
        
        result = await dayaheadmarket_controller.submit_for_approval_post_data(
            request_data=request_data,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dayahead_submit_for_approval",
            message="User successfully submitted Day Ahead bid for approval",
            username=username,
            correlation_id=correlation_id,
            submission_status=result.get("status", "unknown"),
            success=True
        )
        
        logger.info(f"Day Ahead bid approval submission completed successfully for user {user_id}")
        
        return DayAheadMarketResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DayAheadSubmitForApprovalError",
            error_message=f"Failed to submit Day Ahead bid for approval: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        
        logger.exception("Error submitting Day Ahead bid for approval")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit Day Ahead bid for approval."
        )

# As Dam scheduling is not required for now, so we are not implementing this route
# @router.get(
#     "/scheduling-cal-table",
#     response_model=DayAheadMarketResponse,
# @router.get(
#     "/scheduling-cal-table",
#     response_model=DayAheadMarketResponse,
#     status_code=status.HTTP_200_OK,
#     summary="Get Day Ahead Market Scheduling Calculation Table",
#     response_description="Day Ahead Market scheduling calculation table for given schedule date",
#     responses={
#         200: {"description": "OK", "model": DayAheadMarketResponse},
#         422: {"description": "Validation Error", "model": ErrorResponse},
#         500: {"description": "Server Error", "model": ErrorResponse},
#     }
# )
# async def get_dayahead_scheduling_cal_table(
#         request: Request,
#         schedule_date: Optional[str] = Query(
#             None, description="YYYY-MM-DD; defaults to tomorrow's date for dayahead market"
#         ),
#         user_context: Dict[str, Any] = Depends(get_user_context),
#         audit_logger: AuditLogger = Depends(get_audit_logger)
# ):
#     """
#     Returns dayahead market scheduling calculation table for a given schedule_date.
#     For dayahead market, uses tomorrow's date by default.
#     """
#     user_id = user_context.get("user_id", "anonymous")
#     username = user_context.get("username", "anonymous")
#     correlation_id = user_context.get("correlation_id", "unknown")
    
#     try:
#         # Log system operation start
#         audit_logger.log_system_operation(
#             service="processing",
#             operation="dayahead_scheduling_cal_table_fetch",
#             message="Starting Day Ahead scheduling calculation table fetch operation",
#             correlation_id=correlation_id,
#             schedule_date=schedule_date,
#             user_id=user_id
#         )
        
#         result = await dayaheadmarket_controller.dayahead_scheduling_cal_table(
#             schedule_date=schedule_date,
#             request=request,
#             current_user=user_context,
#             audit_logger=audit_logger
#         )
        
#         # Log successful user action
#         audit_logger.log_user_action(
#             service="processing",
#             user_id=user_id,
#             action="dayahead_scheduling_cal_table_view",
#             message="User successfully fetched Day Ahead scheduling calculation table",
#             username=username,
#             correlation_id=correlation_id,
#             schedule_date=schedule_date,
#             record_count=len(result.get("scheduling_data", [])) if isinstance(result, dict) else 0,
#             success=True
#         )
        
#         logger.info(f"Day Ahead scheduling calculation table fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
#         return DayAheadMarketResponse(data=result)
        
#     except Exception as e:
#         # Log error event
#         audit_logger.log_error_event(
#             service="processing",
#             error_name="DayAheadSchedulingCalTableFetchError",
#             error_message=f"Failed to fetch Day Ahead scheduling calculation table: {str(e)}",
#             user_id=user_id,
#             correlation_id=correlation_id,
#             schedule_date=schedule_date,
#             error_type=type(e).__name__
#         )
        
#         logger.exception("Error fetching Day Ahead scheduling calculation table")
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Failed to fetch Day Ahead scheduling calculation table."
#         )
#     status_code=status.HTTP_200_OK,
#     summary="Get Day Ahead Market Scheduling Calculation Table",
#     response_description="Day Ahead Market scheduling calculation table for given schedule date",
#     responses={
#         200: {"description": "OK", "model": DayAheadMarketResponse},
#         422: {"description": "Validation Error", "model": ErrorResponse},
#         500: {"description": "Server Error", "model": ErrorResponse},
#     }
# )
# async def get_dayahead_scheduling_cal_table(
#         request: Request,
#         schedule_date: Optional[str] = Query(
#             None, description="YYYY-MM-DD; defaults to tomorrow's date for dayahead market"
#         ),
#         user_context: Dict[str, Any] = Depends(get_user_context),
#         audit_logger: AuditLogger = Depends(get_audit_logger)
# ):
#     """
#     Returns dayahead market scheduling calculation table for a given schedule_date.
#     For dayahead market, uses tomorrow's date by default.
#     """
#     user_id = user_context.get("user_id", "anonymous")
#     username = user_context.get("username", "anonymous")
#     correlation_id = user_context.get("correlation_id", "unknown")
    
#     try:
#         # Log system operation start
#         audit_logger.log_system_operation(
#             service="processing",
#             operation="dayahead_scheduling_cal_table_fetch",
#             message="Starting Day Ahead scheduling calculation table fetch operation",
#             correlation_id=correlation_id,
#             schedule_date=schedule_date,
#             user_id=user_id
#         )
        
#         result = await dayaheadmarket_controller.dayahead_scheduling_cal_table(
#             schedule_date=schedule_date,
#             request=request,
#             current_user=user_context,
#             audit_logger=audit_logger
#         )
        
#         # Log successful user action
#         audit_logger.log_user_action(
#             service="processing",
#             user_id=user_id,
#             action="dayahead_scheduling_cal_table_view",
#             message="User successfully fetched Day Ahead scheduling calculation table",
#             username=username,
#             correlation_id=correlation_id,
#             schedule_date=schedule_date,
#             record_count=len(result.get("scheduling_data", [])) if isinstance(result, dict) else 0,
#             success=True
#         )
        
#         logger.info(f"Day Ahead scheduling calculation table fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
#         return DayAheadMarketResponse(data=result)
        
#     except Exception as e:
#         # Log error event
#         audit_logger.log_error_event(
#             service="processing",
#             error_name="DayAheadSchedulingCalTableFetchError",
#             error_message=f"Failed to fetch Day Ahead scheduling calculation table: {str(e)}",
#             user_id=user_id,
#             correlation_id=correlation_id,
#             schedule_date=schedule_date,
#             error_type=type(e).__name__
#         )
        
#         logger.exception("Error fetching Day Ahead scheduling calculation table")
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Failed to fetch Day Ahead scheduling calculation table."
#         )

@router.post(
    "/submit-for-pre-exchange",
    response_model=DayAheadMarketResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit dayahead pre-exchange data",
    response_description="Returns success status after processing pre-exchange data",
    responses={
        200: {"description": "OK", "model": DayAheadMarketResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    }
)
async def dayahead_submitForPreExchangeData(
        request: Request,
        data: List[ReferenceData],
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Accepts a list of bid data for pre-exchange processing in dayahead market.

    ### Example Request
    ```json
    [
      {
        "reference_number": "68ee9bcbd35c84056106b0e3",
        "bid_status": "approved",
        "Remarks": "This bid is correct",
        "action_by": "5f158b66cf650f572054ddc2",
        "block_no": 17
      }
    ]
    ```

    - `reference_number`: Unique bid ID  
    - `bid_status`: approved / pending / rejected  
    - `Remarks`: Additional notes about the bid  
    - `action_by`: User ID performing the action  
    - `block_no`: Time block number
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dayahead_submit_for_pre_exchange",
            message="Starting Day Ahead pre-exchange data submission operation",
            correlation_id=correlation_id,
            user_id=user_id,
            data_count=len(data) if data else 0
        )
        
        # Convert Pydantic models to dict for processing
        data_list = [item.dict() for item in data] if data else []
        
        result = await dayaheadmarket_controller.submitForPreExchangeData(
            data=data_list,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dayahead_submit_for_pre_exchange",
            message="User successfully submitted Day Ahead pre-exchange data",
            username=username,
            correlation_id=correlation_id,
            data_count=len(data) if data else 0,
            success=True
        )
        
        logger.info(f"Day Ahead pre-exchange data submission completed successfully for user {user_id}")
        
        return DayAheadMarketResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DayAheadSubmitForPreExchangeError",
            error_message=f"Failed to submit Day Ahead pre-exchange data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        
        logger.exception("Error submitting Day Ahead pre-exchange data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit Day Ahead pre-exchange data."
        )
