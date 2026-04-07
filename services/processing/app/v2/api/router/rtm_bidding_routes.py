"""
RTM (Real-Time Market) bidding routes using v2 API patterns.
Defines API endpoints for RTM bidding operations with authentication, authorization, and audit logging.
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Query, Request, Body, Depends, HTTPException, status
from pydantic import BaseModel
import logging

from ...controller.rtm_bidding_controller import rtm_bidding_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from libs.middleware import get_correlation_id

router = APIRouter(prefix="/rtm-bidding", tags=["RTM-Single Bidding V2"])
logger = logging.getLogger(__name__)

# Define request schemas
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
    block_no: str

@router.get("/single-bid-graph")
async def get_rtm_single_bid_graph(
    schedule_date: Optional[str] = Query(None, description="Schedule date in YYYY-MM-DD format"),
    from_block: Optional[int] = Query(None, description="Starting time block"),
    to_block: Optional[int] = Query(None, description="Ending time block"),
    request: Request = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get RTM single bid graph data with authentication and authorization.
    
    Args:
        schedule_date: The schedule date in 'YYYY-MM-DD' format
        from_block: Starting time block
        to_block: Ending time block
        request: FastAPI request object
        user_context: Current authenticated user context
        
    Returns:
        RTM single bid graph data
    """
    try:
        return await rtm_bidding_controller.rtm_single_bid_graph(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch RTM single bid graph data: {str(e)}"
        )

@router.get("/single-bid-table")
async def get_rtm_single_bid_table(
    schedule_date: Optional[str] = Query(None, description="Schedule date in YYYY-MM-DD format"),
    from_block: Optional[int] = Query(None, description="Starting time block"),
    to_block: Optional[int] = Query(None, description="Ending time block"),
    request: Request = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get RTM single bid table data with authentication and authorization.
    
    Args:
        schedule_date: The schedule date in 'YYYY-MM-DD' format
        from_block: Starting time block
        to_block: Ending time block
        request: FastAPI request object
        user_context: Current authenticated user context
        
    Returns:
        RTM single bid table data
    """
    try:
        return await rtm_bidding_controller.rtm_single_bid_table(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch RTM single bid table data: {str(e)}"
        )

@router.get("/approval-request-list")
async def get_rtm_approval_request_list(
    schedule_date: Optional[str] = Query(None, description="Schedule date in YYYY-MM-DD format"),
    from_block: Optional[int] = Query(None, description="Starting time block"),
    to_block: Optional[int] = Query(None, description="Ending time block"),
    request: Request = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get RTM approval request list with authentication and authorization.
    
    Args:
        schedule_date: The schedule date in 'YYYY-MM-DD' format
        from_block: Starting time block
        to_block: Ending time block
        request: FastAPI request object
        user_context: Current authenticated user context
        
    Returns:
        RTM approval request list data
    """
    try:
        return await rtm_bidding_controller.approval_request_list(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch RTM approval request list: {str(e)}"
        )

@router.post("/bid-creation")
async def create_rtm_bid(
    post_data: dict = Body(...),
    request: Request = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Create RTM bid data with authentication and authorization.
    
    Args:
        post_data: Bid creation data
        request: FastAPI request object
        user_context: Current authenticated user context
        
    Returns:
        Bid creation result
    """
    try:
        return await rtm_bidding_controller.bid_creation_post(
            post_data=post_data,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create RTM bid: {str(e)}"
        )

@router.post("/submit-for-approval")
async def submit_rtm_bid_for_approval(
    post_data: dict = Body(..., description="Submit bid approval data"),
    request: Request = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Submit RTM bid data for approval with authentication and authorization.
    
    Args:
        request_data: Approval request data
        request: FastAPI request object
        user_context: Current authenticated user context
        
    Returns:
        Submission result
    """
    try:
        return await rtm_bidding_controller.submit_for_approval_post_data(
            post_data,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit RTM bid for approval: {str(e)}"
        )

@router.post("/submit-pre-exchange-data")
async def submit_rtm_pre_exchange_data(
    data: List[ReferenceData] = Body(...),
    request: Request = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Submit RTM bid data for pre-exchange processing with authentication and authorization.
    
    Args:
        data: Pre-exchange data
        request: FastAPI request object
        user_context: Current authenticated user context
        
    Returns:
        Processing result
    """
    try:
        # Convert Pydantic models to dictionaries
        data_dict = [item.dict() for item in data]
        
        return await rtm_bidding_controller.submit_for_pre_exchange_data(
            data=data_dict,
            request=request,
            current_user=user_context,
            audit_logger=audit_logger
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit RTM pre-exchange data: {str(e)}"
        )

# This endpoint is not used in the project

# @router.get("/cal-scheduling-table")
# async def get_rtm_cal_scheduling_table(
#     schedule_date: Optional[str] = Query(None, description="Schedule date in YYYY-MM-DD format"),
#     request: Request = None,
#     user_context: Dict[str, Any] = Depends(get_user_context),
#     audit_logger: AuditLogger = Depends(get_audit_logger)
# ):
#     """
#     Calculate RTM scheduling table data with authentication and authorization.
    
#     Args:
#         schedule_date: The schedule date in 'YYYY-MM-DD' format
#         request: FastAPI request object
#         user_context: Current authenticated user context
        
#     Returns:
#         RTM scheduling calculation data
#     """
#     try:
#         return await rtm_bidding_controller.cal_scheduling_table_data(
#             schedule_date=schedule_date,
#             request=request,
#             current_user=user_context,
#             audit_logger=audit_logger
#         )
#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to calculate RTM scheduling table data: {str(e)}"
#         )

