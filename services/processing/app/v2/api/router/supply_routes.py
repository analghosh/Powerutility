
"""
Supply V2 Router
Endpoints for supply data with JWT authentication and correlation tracking
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends, Query
from typing import Dict, Any, Optional, List, Tuple
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from ...controller.supply_controller import supply_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from libs.middleware import get_correlation_id

router = APIRouter(
    prefix="/supply",
    tags=["Supply V2"]
)

logger = logging.getLogger(__name__)


class SupplyResponse(BaseModel):
    """
    Response schema for Supply V2 APIs.
    """
    data: Dict[str, Any] = Field(..., description="Supply data payload")
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


@router.get(
    "/graph_bundle",
    response_model=SupplyResponse,
    status_code=status.HTTP_200_OK,
    summary="Supply Graph Bundle Data (V2)",
    description="""
    Fetches **supply graph bundle data** for the Power Grid system.
    """,
    response_description="Returns structured supply data for visualization.",
)
async def get_supply_graph_bundle(
    request: Request,
    schedule_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date.",
        example="2025-01-15"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        audit_logger.log_system_operation(
            service="supply_service",
            operation="fetch_supply_graph_bundle",
            message=
                f"User {user_id} fetched supply graph bundle for schedule_date: {schedule_date}",
            correlation_id=correlation_id,
            success=True,
        )
        result = await supply_controller.get_supply_graph_bundle(schedule_date)
        audit_logger.log_user_action(
            service="supply_service",
            user_id=user_id,
            action="fetch_supply_graph_bundle",
            message="Fetched supply graph bundle data successfully.",
            correlation_id=correlation_id,
            success=True,
        )
        return SupplyResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.exception(f"Error fetching supply graph bundle data for user {user_id}, schedule_date: {schedule_date}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch supply graph bundle data."
        )

@router.get(
    "/block_bundle",
    response_model=SupplyResponse,
    status_code=status.HTTP_200_OK,
    summary="Supply Block Bundle Data (V2)",
    description="""
    Fetches **supply block bundle data** for the Power Grid system.
    """,
    response_description="Returns structured supply data for visualization.",
)
async def get_supply_block_bundle(
    request: Request,
    schedule_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date.",
        example="2025-01-15"
    ),
    time_block: Optional[int] = Query(
        None, 
        ge=1, 
        le=96, 
        description="1..96. Defaults to current IST block."
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        audit_logger.log_system_operation(
            service="supply_service",
            operation="fetch_supply_block_bundle",
            message=
                f"User {user_id} fetched supply block bundle for schedule_date: {schedule_date}, time_block: {time_block}",
            correlation_id=correlation_id,
            success=True,
        )
        result = await supply_controller.get_supply_block_bundle(schedule_date, time_block)
        audit_logger.log_user_action(
            service="supply_service",
            user_id=user_id,
            action="fetch_supply_block_bundle",
            message="Fetched supply block bundle data successfully.",
            correlation_id=correlation_id,
            success=True,
        )
        return SupplyResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.exception(f"Error fetching supply block bundle data for user {user_id}, schedule_date: {schedule_date}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch supply block bundle data: {e}"
        )

@router.get(
    "/tech",
    response_model=SupplyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get supply by technology (single block, V2)",
    description="Returns single-block supply grouped by technology with central/state split and totals.",
    response_description="Technology breakdown for the chosen date & block",
)
async def get_supply_by_technology(
    request: Request,
    schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD (IST). Defaults to today's date."),
    time_block: Optional[int] = Query(None, ge=1, le=96, description="1..96. Defaults to current IST block."),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        audit_logger.log_system_operation(
            service="supply_service",
            operation="fetch_supply_by_technology",
            message=
                f"User {user_id} fetched supply by technology for schedule_date: {schedule_date}, time_block: {time_block}",
            correlation_id=correlation_id,
            success=True,
        )
        result = await supply_controller.get_supply_by_technology_block(schedule_date, time_block)
        audit_logger.log_user_action(
            service="supply_service",
            user_id=user_id,
            action="fetch_supply_by_technology",
            message="Fetched supply by technology successfully.",
            correlation_id=correlation_id,
            success=True,
        )
        return SupplyResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
    except Exception as e:
        logger.exception(f"Error fetching supply by technology for user {user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch supply by technology."
        )

@router.get(
    "/price_group",
    response_model=SupplyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get supply by price group (single block, V2)",
    description="Returns single-block supply grouped by price group with central/state split and totals.",
    response_description="Price group breakdown for the chosen date & block",
)
async def get_supply_by_price_group(
    request: Request,
    schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD (IST). Defaults to today's date."),
    time_block: Optional[int] = Query(None, ge=1, le=96, description="1..96. Defaults to current IST block."),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        audit_logger.log_system_operation(
            service="supply_service",
            operation="fetch_supply_by_price_group",
            message=
                f"User {user_id} fetched supply by price group for schedule_date: {schedule_date}, time_block: {time_block}",
            correlation_id=correlation_id,
            success=True,
        )
        result = await supply_controller.get_supply_by_price_group_block(schedule_date, time_block)
        audit_logger.log_user_action(
            service="supply_service",
            user_id=user_id,
            action="fetch_supply_by_price_group",
            message="Fetched supply by price group successfully.",
            correlation_id=correlation_id,
            success=True,
        )
        return SupplyResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
    except Exception as e:
        logger.exception(f"Error fetching supply by price group for user {user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch supply by price group."
        )

@router.get(
    "/raw",
    response_model=SupplyResponse,
    status_code=status.HTTP_200_OK,
    summary="Raw Supply Data (V2)",
    description="""
    Fetches **raw supply data** for the Power Grid system.
    """,
    response_description="Returns raw supply data.",
)
async def get_supply_raw(
    request: Request,
    # ---- ranges (all optional) ----
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD (inclusive)"),
    date_to:   Optional[str] = Query(None, description="YYYY-MM-DD (inclusive)"),
    from_block: Optional[int] = Query(None, ge=1, le=96, description="Lower bound time_block (inclusive)"),
    to_block:   Optional[int] = Query(None, ge=1, le=96, description="Upper bound time_block (inclusive)"),

    # ---- common field filters (multi-value allowed) ----
    plant_id: Optional[List[str]] = Query(None),
    plant_name: Optional[List[str]] = Query(None),
    c_s: Optional[List[str]] = Query(None, description="central | state"),
    category: Optional[List[str]] = Query(None),
    technology: Optional[List[str]] = Query(None),
    price_group: Optional[List[str]] = Query(None),
    dc_sg: Optional[List[str]] = Query(None, description="DC | SG | SH"),

    # numeric ranges (optional)
    ramping_min: Optional[float] = Query(None),
    ramping_max: Optional[float] = Query(None),
    TM_min: Optional[float] = Query(None),
    TM_max: Optional[float] = Query(None),
    FC_min: Optional[float] = Query(None, description="FC_price min"),
    FC_max: Optional[float] = Query(None, description="FC_price max"),
    VC_min: Optional[float] = Query(None, description="VC_price min"),
    VC_max: Optional[float] = Query(None, description="VC_price max"),
    px_min: Optional[float] = Query(None, description="px_price min"),
    px_max: Optional[float] = Query(None, description="px_price max"),
    mult_min: Optional[float] = Query(None, description="multiplier min"),
    mult_max: Optional[float] = Query(None, description="multiplier max"),
    vol_min: Optional[float] = Query(None, description="volume min"),
    vol_max: Optional[float] = Query(None, description="volume max"),

    updated_from: Optional[str] = Query(None, description="ISO datetime, inclusive"),
    updated_to:   Optional[str] = Query(None, description="ISO datetime, inclusive"),

    # ---- projection ----
    include: Optional[str] = Query(None, description="Comma-separated list to include"),
    exclude: Optional[str] = Query(None, description="Comma-separated list to exclude"),

    # ---- sorting + pagination ----
    sort: Optional[str] = Query(None, description="Comma-separated 'field:dir'. e.g. 'schedule_date:asc,time_block:asc'"),
    skip: int = Query(0, ge=0),
    limit: Optional[int] = Query(
        None,                  # <-- None => service adds no $limit
        ge=1, le=1_000_000,
        description="Max rows to return. Omit for no limit (use with care)."
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        audit_logger.log_system_operation(
            service="supply_service",
            operation="fetch_supply_raw",
            message=
                f"User {user_id} fetched raw supply data with filters - "
                f"date_from: {date_from}, date_to: {date_to}, from_block: {from_block}, to_block: {to_block}, "
                f"plant_id: {plant_id}, plant_name: {plant_name}, c_s: {c_s}, category: {category}, technology: {technology}, "
                f"price_group: {price_group}, dc_sg: {dc_sg}, ramping_min: {ramping_min}, ramping_max: {ramping_max}, "
                f"TM_min: {TM_min}, TM_max: {TM_max}, FC_min: {FC_min}, FC_max: {FC_max}, VC_min: {VC_min}, VC_max: {VC_max}, "
                f"px_min: {px_min}, px_max: {px_max}, mult_min: {mult_min}, mult_max: {mult_max}, vol_min: {vol_min}, vol_max: {vol_max}, "
                f"updated_from: {updated_from}, updated_to: {updated_to}, include: {include}, exclude: {exclude}, "
                f"sort: {sort}, skip: {skip}, limit: {limit}",
            correlation_id=correlation_id,
            success=True,
        )
        result = await supply_controller.get_supply_raw(
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            plant_id=plant_id,
            plant_name=plant_name,
            c_s=c_s,
            category=category,
            technology=technology,
            price_group=price_group,
            dc_sg=dc_sg,
            ramping_min=ramping_min,
            ramping_max=ramping_max,
            TM_min=TM_min,
            TM_max=TM_max,
            FC_min=FC_min,
            FC_max=FC_max,
            VC_min=VC_min,
            VC_max=VC_max,
            px_min=px_min,
            px_max=px_max,
            mult_min=mult_min,
            mult_max=mult_max,
            vol_min=vol_min,
            vol_max=vol_max,
            updated_from=updated_from,
            updated_to=updated_to,
            include=include,
            exclude=exclude,
            sort=sort,
            skip=skip,
            limit=limit,
        )
        audit_logger.log_user_action(
            service="supply_service",
            user_id=user_id,
            action="fetch_supply_raw",
            message="Fetched raw supply data successfully.",
            correlation_id=correlation_id,
            success=True,
        )
        return SupplyResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
        
    except Exception as e:
        logger.exception(f"Error fetching raw supply data for user {user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch raw supply data: {e}"
        )
