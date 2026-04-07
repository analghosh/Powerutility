"""
Supply Manual V2 Router
Endpoints for manual supply data operations with JWT authentication.
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends, Query
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from ...controller.supply_manual_controller import supply_manual_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from libs.middleware import get_correlation_id
from libs.enum.updated_flag import UpdatedFlag

router = APIRouter(
    prefix="/dc_sg_template",
    tags=["Supply Manual V2"]
)

logger = logging.getLogger(__name__)


class SupplyManualResponse(BaseModel):
    """Response schema for Supply Manual V2 APIs."""
    data: Dict[str, Any] = Field(..., description="Response data payload")
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


@router.get(
    "/",
    response_model=SupplyManualResponse,
    status_code=status.HTTP_200_OK,
    summary="Get DC/SG Template Data (V2)",
    description="""
    Fetches **manual supply data** from the dc_sg staging collection.
    
    **Defaults:**
    - `dc_sg`: "DC" if not provided
    - `process_type`: "today" if not provided  
    - `updated_flag`: "OLD" if not provided
    - `time_block`: All blocks if not provided
    """,
    response_description="Returns dc_sg records matching the filters.",
)
async def get_manual_supply(
    request: Request,
    plant_id: Optional[str] = Query(
        None,
        description="Plant ID to filter. If not provided, returns all plants."
    ),
    dc_sg: Optional[str] = Query(
        None,
        description="DC or SG type. Defaults to 'DC'."
    ),
    process_type: Optional[str] = Query(
        None,
        description="'today' or 'tomorrow'. Defaults to 'today'."
    ),
    updated_flag: Optional[str] = Query(
        None,
        description="Filter by updated_flag. Defaults to 'OLD'."
    ),
    time_block: Optional[int] = Query(
        None,
        ge=1,
        le=96,
        description="Time block (1-96). If not provided, returns all blocks."
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    # Apply defaults
    dc_sg_val = dc_sg or "DC"
    process_type_val = process_type or "today"
    updated_flag_val = updated_flag or UpdatedFlag.OLD.value
    
    try:
        audit_logger.log_system_operation(
            service="supply_manual_service",
            operation="get_manual_supply",
            message=f"User {user_id} fetched manual supply: plant_id={plant_id}, dc_sg={dc_sg_val}, process_type={process_type_val}, updated_flag={updated_flag_val}, time_block={time_block}",
            correlation_id=correlation_id,
            success=True,
        )
        
        result = await supply_manual_controller.get_manual_supply(
            plant_id=plant_id,
            dc_sg=dc_sg_val,
            process_type=process_type_val,
            updated_flag=updated_flag_val,
            time_block=time_block
        )
        
        audit_logger.log_user_action(
            service="supply_manual_service",
            user_id=user_id,
            action="get_manual_supply",
            message="Fetched manual supply data successfully.",
            correlation_id=correlation_id,
            success=True,
        )
        
        return SupplyManualResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error fetching manual supply data for user {user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch manual supply data: {str(e)}"
        )


@router.put(
    "/",
    response_model=SupplyManualResponse,
    status_code=status.HTTP_200_OK,
    summary="Update DC/SG Template Data (V2)",
    description="""
    Updates **manual supply data** in dc_sg to zero volume with REPLACED flag.
    Also syncs the changes to supply_serve using delete + insert.
    
    **Required Parameters:**
    - `plant_id`: Plant ID to update
    - `dc_sg`: DC or SG type
    - `process_type`: "today" or "tomorrow"
    
    **Optional Parameters:**
    - `time_block`: If provided, only updates the specific block. Otherwise, updates all blocks.
    """,
    response_description="Returns update results including dc_sg and supply_serve sync status.",
)
async def update_manual_supply(
    request: Request,
    plant_id: Optional[str] = Query(
        None,
        description="Plant ID to update (REQUIRED)."
    ),
    dc_sg: Optional[str] = Query(
        None,
        description="DC or SG type (REQUIRED)."
    ),
    process_type: Optional[str] = Query(
        None,
        description="'today' or 'tomorrow' (REQUIRED)."
    ),
    time_block: Optional[int] = Query(
        None,
        ge=1,
        le=96,
        description="Time block (1-96). If not provided, updates all blocks."
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        audit_logger.log_system_operation(
            service="supply_manual_service",
            operation="update_manual_supply",
            message=f"User {user_id} updating manual supply: plant_id={plant_id}, dc_sg={dc_sg}, process_type={process_type}, time_block={time_block}",
            correlation_id=correlation_id,
            success=True,
        )
        
        result = await supply_manual_controller.update_manual_supply(
            plant_id=plant_id,
            dc_sg=dc_sg,
            process_type=process_type,
            time_block=time_block
        )
        
        audit_logger.log_user_action(
            service="supply_manual_service",
            user_id=user_id,
            action="update_manual_supply",
            message=f"Updated manual supply data: {result.get('message', '')}",
            correlation_id=correlation_id,
            success=True,
        )
        
        return SupplyManualResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating manual supply data for user {user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update manual supply data: {str(e)}"
        )
