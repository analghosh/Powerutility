"""
Power Optimization API Routes
Provides a unified interface for power optimization inputs.
"""
from typing import Dict, Any, Optional, List
from enum import Enum
from fastapi import APIRouter, Request, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field

from ...middleware.hmac_auth import verify_hmac_auth
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ...controller.power_optimization_controller import PowerOptimizationController


router = APIRouter(
    prefix="/power-optimization",
    tags=["Power Optimization"]
)

class FilterType(str, Enum):
    """Filter types for power optimization API"""
    FORECAST = "forecast"
    OPTIMIZATION = "optimization"

class PowerOptimizationResponse(BaseModel):
    """Response schema for power optimization API"""
    meta: Dict[str, Any]
    dates: List[Dict[str, Any]]
    plants: List[Dict[str, Any]]
    marginal_costs: List[Dict[str, Any]]

@router.get(
    "/power-optimization-input",
    response_model=PowerOptimizationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Power Optimization Inputs",
    response_description="Unified demand, generation, market, and plant metadata for optimization"
)
async def get_power_optimization_input(
    request: Request,
    from_date: str = Query(
        ..., 
        description="Start date in YYYY-MM-DD format",
        example="2026-01-06"
    ),
    to_date: str = Query(
        ..., 
        description="End date in YYYY-MM-DD format",
        example="2026-01-07"
    ),
    filter: FilterType = Query(
        FilterType.OPTIMIZATION,
        description="Filter type: 'forecast' fetches only demand and IEX data; 'optimization' fetches all data",
        example="optimization"
    ),
    forecast_market: Optional[str] = Query(None, description="Primary market selection: DAM or RTM", example="DAM"),
    exchange: Optional[List[str]] = Query(None, description="Exchanges (multi): IEX, PXIL, HPX"),
    market_type: Optional[List[str]] = Query(None, description="Market types (multi): DAM, RTM, GDAM, HPDAM"),
    demand_type: str = Query("IDF", description="Demand type: IDF or DAF", example="IDF"),
    api_key_context: Dict[str, Any] = Depends(verify_hmac_auth),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch a unified dataset for power optimization including:
    - Demand Forecast (IDF)
    - Generation Forecast (Schedule vs DC)
    - IEX Market Data (DAM, RTM, GDAM, HPDAM)
    - Plant Metadata (Ramping, Availability type, etc.)
    - Marginal Cost Data
    
    **Authentication**: This endpoint requires HMAC signature authentication.
    See documentation for details on generating signatures.
    """
    api_key = api_key_context.get("api_key", "unknown")
    client_name = api_key_context.get("client_name", "unknown")
    correlation_id = api_key_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation
        audit_logger.log_system_operation(
            service="processing",
            operation="power_optimization_input_fetch",
            message=f"Fetching power optimization input for range: {from_date} to {to_date} with filter={filter.value} (API Key: {api_key[:8]}...)",
            correlation_id=correlation_id,
            user_id=f"api_key:{api_key}"
        )
        
        controller = PowerOptimizationController()
        # Pass API key context and filter
        result = await controller.get_power_optimization_input(
            from_date, to_date, api_key, client_name, filter.value,
            exchange=exchange,
            market_type=market_type,
            forecast_market=forecast_market,
            demand_type=demand_type
        )
        
        # Log system action (not user action since this is system-to-system)
        audit_logger.log_system_operation(
            service="processing",
            operation="power_optimization_input_success",
            message=f"Successfully fetched power optimization input data for {client_name} with filter={filter.value}",
            correlation_id=correlation_id,
            user_id=f"api_key:{api_key}"
        )
        
        return result
        
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PowerOptimizationInputError",
            error_message=str(e),
            user_id=f"api_key:{api_key}",
            correlation_id=correlation_id
        )
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal error fetching power optimization input: {str(e)}"
        )
