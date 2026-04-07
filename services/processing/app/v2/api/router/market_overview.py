from enum import Enum
from fastapi import APIRouter, Request, HTTPException, status, Depends, Query
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from ...controller.market_controller import market_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from libs.middleware import get_correlation_id
from ....core.dependencies import get_audit_logger

router = APIRouter(
    prefix="/market",
    tags=["Market Overview V2"]
)

logger = logging.getLogger(__name__)


class MarketOverviewResponse(BaseModel):
    """
    Response schema for Market Overview APIs.
    """
    data: Any = Field(..., description="Market data payload (format varies by endpoint)")


@router.get(
    "/exchange-graph",
    response_model=MarketOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Market Exchange Graph Data",
    response_description="Market exchange data for graph visualization"
)
async def get_market_exchange_graph(
    request: Request,
    scheduled_at: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch market exchange data for graph visualization.

    Args:
        scheduled_at (Optional[str]): The scheduled date to filter market data. 
                                        Format: YYYY-MM-DD
        user_context: Current authenticated user context (injected by dependency)

    Returns:
        MarketOverviewResponse: Market exchange data for graphs.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="market_exchange_graph_fetch",
            message="Starting market exchange graph data fetch operation",
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            user_id=user_id
        )
        
        result = await market_controller.get_exchange_graph(scheduled_at)
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="market_exchange_graph_view",
            message="User successfully fetched market exchange graph data",
            username=username,
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            record_count=len(result.get("data", [])) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"Market exchange graph data fetched successfully for user {user_id}, scheduled_at: {scheduled_at}")
        
        return MarketOverviewResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="MarketExchangeGraphFetchError",
            error_message=f"Failed to fetch market exchange graph data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching market exchange graph data for user {user_id}, scheduled_at: {scheduled_at}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch market exchange graph data."
        )


@router.get(
    "/overview-graph",
    response_model=MarketOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Market Overview Graph Data",
    response_description="Market overview data for graph visualization"
)
async def get_market_overview_graph(
    request: Request,
    scheduled_at: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch market overview data for graph visualization.

    Args:
        scheduled_at (Optional[str]): The scheduled date to filter market data. 
                                        Format: YYYY-MM-DD
        user_context: Current authenticated user context (injected by dependency)

    Returns:
        MarketOverviewResponse: Market overview data for graphs.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="market_overview_graph_fetch",
            message="Starting market overview graph data fetch operation",
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            user_id=user_id
        )
        
        result = await market_controller.get_overview_graph(scheduled_at)
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="market_overview_graph_view",
            message="User successfully fetched market overview graph data",
            username=username,
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            record_count=len(result) if isinstance(result, list) else 0,
            success=True
        )
        
        logger.info(f"Market overview graph data fetched successfully for user {user_id}, scheduled_at: {scheduled_at}")
        
        return MarketOverviewResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="MarketOverviewGraphFetchError",
            error_message=f"Failed to fetch market overview graph data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching market overview graph data for user {user_id}, scheduled_at: {scheduled_at}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch market overview graph data."
        )


@router.get(
    "/exchange-table",
    response_model=MarketOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Market Exchange Table Data",
    response_description="Market exchange data in table format"
)
async def get_market_exchange_table(
    request: Request,
    scheduled_at: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"),
    time_block: Optional[int] = Query(None, description="Time block (1–96); optional"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch market exchange data in table format.

    Args:
        scheduled_at (Optional[str]): The scheduled date to filter market data. 
                                        Format: YYYY-MM-DD
        time_block (Optional[int]): Time block filter (1-96)
        user_context: Current authenticated user context (injected by dependency)

    Returns:
        MarketOverviewResponse: Market exchange data in table format.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="market_exchange_table_fetch",
            message="Starting market exchange table data fetch operation",
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            time_block=time_block,
            user_id=user_id
        )
        
        result = await market_controller.get_exchange_table(scheduled_at, time_block)
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="market_exchange_table_view",
            message="User successfully fetched market exchange table data",
            username=username,
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            time_block=time_block,
            record_count=len(result) if isinstance(result, list) else 0,
            success=True
        )
        
        logger.info(f"Market exchange table data fetched successfully for user {user_id}, scheduled_at: {scheduled_at}, time_block: {time_block}")
        
        return MarketOverviewResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="MarketExchangeTableFetchError",
            error_message=f"Failed to fetch market exchange table data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            time_block=time_block,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching market exchange table data for user {user_id}, scheduled_at: {scheduled_at}, time_block: {time_block}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch market exchange table data."
        )


@router.get(
    "/bidding-summary-table",
    response_model=MarketOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Market Bidding Summary Table Data",
    response_description="Market bidding summary data in table format"
)
async def get_market_bidding_summary_table(
    request: Request,
    scheduled_at: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"),
    time_block: Optional[int] = Query(None, description="Time block (1–96); optional"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch market bidding summary data in table format.

    Args:
        scheduled_at (Optional[str]): The scheduled date to filter market data. 
                                        Format: YYYY-MM-DD
        time_block (Optional[int]): Time block filter (1-96)
        user_context: Current authenticated user context (injected by dependency)

    Returns:
        MarketOverviewResponse: Market bidding summary data in table format.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="market_bidding_summary_fetch",
            message="Starting market bidding summary data fetch operation",
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            time_block=time_block,
            user_id=user_id
        )
        
        result = await market_controller.get_bidding_summary_table(scheduled_at, time_block)
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="market_bidding_summary_view",
            message="User successfully fetched market bidding summary data",
            username=username,
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            time_block=time_block,
            record_count=len(result) if isinstance(result, list) else 0,
            success=True
        )
        
        logger.info(f"Market bidding summary data fetched successfully for user {user_id}, scheduled_at: {scheduled_at}, time_block: {time_block}")
        
        return MarketOverviewResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="MarketBiddingSummaryFetchError",
            error_message=f"Failed to fetch market bidding summary data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            scheduled_at=scheduled_at,
            time_block=time_block,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching market bidding summary data for user {user_id}, scheduled_at: {scheduled_at}, time_block: {time_block}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch market bidding summary data."
        )