"""
Parameter V2 Router
CRUD endpoints for parameter management with JWT authentication
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends, Query, Body
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from ...controller.parameter_controller import parameter_controller
from ...dependencies import get_user_context
from ...services.idf_forecast_switch_service import ManualSwitchNotAllowedError
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(
    prefix="/parameters",
    tags=["Parameters V2"]
)

logger = logging.getLogger(__name__)


# Pydantic Models
class ParameterCreate(BaseModel):
    """Schema for creating a parameter"""
    para_key: str = Field(..., description="Parameter key (unique identifier)")
    para_value: Any = Field(..., description="Parameter value (string or structured object)")

    class Config:
        json_schema_extra = {
            "example": {
                "para_key": "max_upload_size",
                "para_value": "10485760"
            }
        }


class ParameterUpdate(BaseModel):
    """Schema for updating a parameter"""
    para_key: Optional[str] = Field(None, description="Parameter key")
    para_value: Optional[Any] = Field(None, description="Parameter value (string or structured object)")

    class Config:
        json_schema_extra = {
            "example": {
                "para_value": "20971520"
            }
        }


class ParameterMergeResult(BaseModel):
    merged: int
    skipped: int
    failed: int
    status: str


class ParameterResponse(BaseModel):
    """Schema for parameter response"""
    id: str = Field(..., alias="_id", description="Parameter ID")
    para_key: Optional[str] = Field(None, description="Parameter key")
    para_value: Optional[Any] = Field(None, description="Parameter value (string or structured object)")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    merge_result: Optional[ParameterMergeResult] = Field(
        None,
        description="Present when idf_forecast_switch was updated and demand_serve merge ran",
    )

    class Config:
        populate_by_name = True
        extra = "allow"


class ParameterListResponse(BaseModel):
    """Schema for parameter list response"""
    parameters: List[ParameterResponse] = Field(..., description="List of parameters")
    total: int = Field(..., description="Total number of parameters")
    skip: int = Field(..., description="Number of records skipped")
    limit: int = Field(..., description="Maximum number of records returned")
    timestamp: Optional[str] = Field(None, description="Response timestamp")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")



class BulkCreateRequest(BaseModel):
    """Schema for bulk parameter creation"""
    parameters: List[ParameterCreate] = Field(..., description="List of parameters to create")

    class Config:
        json_schema_extra = {
            "example": {
                "parameters": [
                    {"para_key": "max_upload_size", "para_value": "10485760"},
                    {"para_key": "session_timeout", "para_value": "3600"}
                ]
            }
        }


class BulkCreateResponse(BaseModel):
    """Schema for bulk create response"""
    inserted_ids: List[str] = Field(..., description="List of inserted parameter IDs")
    count: int = Field(..., description="Number of parameters created")
    timestamp: str = Field(..., description="Response timestamp")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


@router.post(
    "",
    response_model=ParameterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Parameter",
    description="Create a new parameter with para_key and para_value"
)
async def create_parameter(
    request: Request,
    parameter: ParameterCreate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Create a new parameter"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="parameter_create",
            message="Starting parameter creation",
            correlation_id=correlation_id,
            user_id=user_id,
            para_key=parameter.para_key
        )

        result = await parameter_controller.create_parameter(parameter.dict())

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="parameter_create",
            message="Parameter created successfully",
            username=username,
            correlation_id=correlation_id,
            para_key=parameter.para_key,
            success=True
        )

        return result

    except ValueError as ve:
        audit_logger.log_error_event(
            service="processing",
            error_name="ParameterValidationError",
            error_message=str(ve),
            user_id=user_id,
            correlation_id=correlation_id
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ParameterCreateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error creating parameter")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create parameter"
        )


@router.post(
    "/bulk",
    response_model=BulkCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk Create Parameters",
    description="Create multiple parameters at once"
)
async def create_parameters_bulk(
    request: Request,
    bulk_request: BulkCreateRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Create multiple parameters in bulk"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="parameter_bulk_create",
            message=f"Starting bulk parameter creation ({len(bulk_request.parameters)} items)",
            correlation_id=correlation_id,
            user_id=user_id,
            count=len(bulk_request.parameters)
        )

        parameters = [param.dict() for param in bulk_request.parameters]
        result = await parameter_controller.create_parameters_bulk(parameters)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="parameter_bulk_create",
            message=f"Bulk created {result['count']} parameters",
            username=username,
            correlation_id=correlation_id,
            count=result['count'],
            success=True
        )

        return {
            **result,
            "timestamp": datetime.now().isoformat(),
            "correlation_id": correlation_id
        }

    except ValueError as ve:
        audit_logger.log_error_event(
            service="processing",
            error_name="ParameterBulkValidationError",
            error_message=str(ve),
            user_id=user_id,
            correlation_id=correlation_id
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ParameterBulkCreateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error creating parameters in bulk")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create parameters in bulk"
        )


@router.get(
    "",
    response_model=ParameterListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get All Parameters",
    description="Get all parameters with pagination"
)
async def get_all_parameters(
    request: Request,
    skip: int = Query(0, description="Number of records to skip", ge=0),
    limit: int = Query(100, description="Maximum number of records to return", ge=1, le=1000),
    sort_by: str = Query("created_at", description="Field to sort by"),
    sort_order: int = Query(-1, description="Sort order (1 for ascending, -1 for descending)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get all parameters with pagination"""
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await parameter_controller.get_all_parameters(
            skip=skip,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order
        )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="parameter_list",
            message="Parameters fetched successfully",
            correlation_id=correlation_id,
            count=len(result['parameters']),
            success=True
        )

        return {
            **result,
            "timestamp": datetime.now().isoformat(),
            "correlation_id": correlation_id
        }

    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ParameterListError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error fetching parameters")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch parameters"
        )


@router.get(
    "/{parameter_id}",
    response_model=ParameterResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Parameter by ID",
    description="Get a specific parameter by its ID"
)
async def get_parameter_by_id(
    request: Request,
    parameter_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get parameter by ID"""
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await parameter_controller.get_parameter_by_id(parameter_id)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parameter with ID {parameter_id} not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="parameter_get",
            message="Parameter fetched successfully",
            correlation_id=correlation_id,
            parameter_id=parameter_id,
            success=True
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ParameterGetError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error fetching parameter")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch parameter"
        )


@router.get(
    "/key/{para_key}",
    response_model=ParameterResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Parameter by Key",
    description="Get a specific parameter by its key"
)
async def get_parameter_by_key(
    request: Request,
    para_key: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get parameter by key"""
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await parameter_controller.get_parameter_by_key(para_key)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parameter with key '{para_key}' not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="parameter_get_by_key",
            message="Parameter fetched by key successfully",
            correlation_id=correlation_id,
            para_key=para_key,
            success=True
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ParameterGetByKeyError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error fetching parameter by key")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch parameter"
        )


@router.put(
    "/{parameter_id}",
    response_model=ParameterResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Parameter",
    description="Update an existing parameter by ID"
)
async def update_parameter(
    request: Request,
    parameter_id: str,
    parameter: ParameterUpdate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Update parameter by ID"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        # Only include fields that are set
        update_data = parameter.dict(exclude_unset=True)
        
        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )

        result = await parameter_controller.update_parameter(parameter_id, update_data)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parameter with ID {parameter_id} not found"
            )

        merge_result = result.get("merge_result") or {}
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="parameter_update",
            message="Parameter updated successfully",
            username=username,
            correlation_id=correlation_id,
            parameter_id=parameter_id,
            para_key=result.get("para_key"),
            success=True,
            merge_merged=merge_result.get("merged"),
            merge_skipped=merge_result.get("skipped"),
            merge_failed=merge_result.get("failed"),
            merge_status=merge_result.get("status"),
        )

        return result

    except ManualSwitchNotAllowedError as e:
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="parameter_update_rejected",
            message=str(e.message),
            username=username,
            correlation_id=correlation_id,
            parameter_id=parameter_id,
            success=False,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=e.message,
        ) from e
    except HTTPException:
        raise
    except ValueError as ve:
        audit_logger.log_error_event(
            service="processing",
            error_name="ParameterUpdateValidationError",
            error_message=str(ve),
            user_id=user_id,
            correlation_id=correlation_id
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ParameterUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error updating parameter")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update parameter"
        )


@router.delete(
    "/{parameter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Parameter",
    description="Delete a parameter by ID"
)
async def delete_parameter(
    request: Request,
    parameter_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Delete parameter by ID"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await parameter_controller.delete_parameter(parameter_id)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parameter with ID {parameter_id} not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="parameter_delete",
            message="Parameter deleted successfully",
            username=username,
            correlation_id=correlation_id,
            parameter_id=parameter_id,
            success=True
        )

        return None

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ParameterDeleteError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error deleting parameter")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete parameter"
        )
