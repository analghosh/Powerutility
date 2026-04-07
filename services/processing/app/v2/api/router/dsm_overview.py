from enum import Enum
from fastapi import APIRouter, Request, HTTPException, status, Depends
from typing import Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import logging
from typing import Optional

from ...controller.dsm_controller import dsm_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from libs.middleware import get_correlation_id

router = APIRouter(
    prefix="/dsm",
    tags=["DSM Overview V2"]
)

logger = logging.getLogger(__name__)


class DsmoverviewResponse(BaseModel):
    """
    Response schema for Dsmoverview APIs.
    """
    # message: str = Field(..., description="Success message")
    # timestamp: str = Field(..., description="ISO formatted timestamp")
    data: Dict[str, Any] = Field(..., description="Payload data")


@router.get(
    "/overview",
    response_model=DsmoverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DSM Overview Data",
    response_description="DSM data with count and records"
)
async def get_data(
    request: Request,
    schedule_date: Optional[str] = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch DSM ingestion records from the database.

    Args:
        schedule_date (Optional[str]): The schedule date to filter DSM data. 
                                        Format: YYYY-MM-DD depending on your DB.
        user_context: Current authenticated user context (injected by dependency)

    Returns:
        DsmoverviewResponse: DSM data with metadata.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dsm_data_fetch",
            message="Starting DSM data fetch operation",
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            user_id=user_id
        )
        
        result = await dsm_controller.dsm(schedule_date)
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dsm_data_view",
            message="User successfully fetched DSM data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=len(result.get("records", [])) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"DSM data fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
        return DsmoverviewResponse(
            # message="DSM Data Fetched Successfully.",
            # timestamp=datetime.now().isoformat(),
            data=result
        )
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DSMDataFetchError",
            error_message=f"Failed to fetch DSM data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching DSM data for user {user_id}, schedule_date: {schedule_date}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DSM data."
        )
    

@router.get(
    "/historical",
    response_model=DsmoverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DSM Historical Data",
    response_description="DSM historical data with count and records"
)
async def get_dsmHistorical(
    request: Request,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    time_block: Optional[str] = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch DSM historical ingestion records from the database.

    Args:
        from_date (Optional[str]): Start date (YYYY-MM-DD).
        to_date (Optional[str]): End date (YYYY-MM-DD).
        user_context: Current authenticated user context (injected by dependency)

    Returns:
        DsmoverviewResponse: DSM historical data with metadata.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dsm_historical_data_fetch",
            message="Starting DSM historical data fetch operation",
            correlation_id=correlation_id,
            from_date=from_date,
            to_date=to_date,
            user_id=user_id
        )
        
        result = await dsm_controller.dsmHistorical(from_date, to_date)
        record_count = len(result) if result else 0
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dsm_historical_data_view",
            message="User successfully fetched DSM historical data",
            username=username,
            correlation_id=correlation_id,
            from_date=from_date,
            to_date=to_date,
            record_count=record_count,
            success=True
        )
        
        logger.info(f"DSM historical data fetched successfully for user {user_id}, from_date: {from_date}, to_date: {to_date}, records: {record_count}")
        
        return DsmoverviewResponse(
            # message="DSM Data Fetched Successfully.",
            # timestamp=datetime.now().isoformat(),
            data={"data": result}
        )
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DSMHistoricalDataFetchError",
            error_message=f"Failed to fetch DSM historical data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            from_date=from_date,
            to_date=to_date,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching DSM historical data for user {user_id}, from_date: {from_date}, to_date: {to_date}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DSM historical data."
        )

@router.get(
    "/historical-table",
    response_model=DsmoverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DSM Historical Table Data",
    response_description="DSM historical table data with count and records"
)
async def get_dsmHistoricalTable(
    request: Request,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    time_block: Optional[str] = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch DSM historical table ingestion records from the database.

    Args:
        from_date (Optional[str]): Start date (YYYY-MM-DD).
        to_date (Optional[str]): End date (YYYY-MM-DD).
        time_block (Optional[str]): Time block filter.
        user_context: Current authenticated user context (injected by dependency)

    Returns:
        DsmoverviewResponse: DSM historical table data with metadata.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dsm_historical_table_data_fetch",
            message="Starting DSM historical table data fetch operation",
            correlation_id=correlation_id,
            from_date=from_date,
            to_date=to_date,
            time_block=time_block,
            user_id=user_id
        )
        
        result = await dsm_controller.dsmHistoricalTable(from_date, to_date, time_block)
        record_count = len(result) if result else 0
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dsm_historical_table_data_view",
            message="User successfully fetched DSM historical table data",
            username=username,
            correlation_id=correlation_id,
            from_date=from_date,
            to_date=to_date,
            time_block=time_block,
            record_count=record_count,
            success=True
        )
        
        logger.info(f"DSM historical table data fetched successfully for user {user_id}, from_date: {from_date}, to_date: {to_date}, time_block: {time_block}, records: {record_count}")
        
        return DsmoverviewResponse(
            # message="DSM Data Fetched Successfully.",
            # timestamp=datetime.now().isoformat(),
            data={"data": result}
        )
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DSMHistoricalTableDataFetchError",
            error_message=f"Failed to fetch DSM historical table data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            from_date=from_date,
            to_date=to_date,
            time_block=time_block,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching DSM historical table data for user {user_id}, from_date: {from_date}, to_date: {to_date}, time_block: {time_block}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DSM historical table data."
        )
    

