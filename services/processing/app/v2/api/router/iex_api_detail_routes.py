from fastapi import APIRouter, Depends, HTTPException, Body, Request, status
from typing import Dict, Any, List, Optional
from ...dependencies import get_user_context
from ....core.dependencies import get_audit_logger
from libs.audit_queue.simple_logger import AuditLogger
from ...controller.iex_api_detail_controller import (
    iex_api_detail_controller, 
    IexApiDetailCreate, 
    IexApiDetailUpdate, 
    StatusUpdate
)
import logging

router = APIRouter(
    prefix="/iex-api-details",
    tags=["IEX API Details"]
)

logger = logging.getLogger(__name__)

@router.get(
    "/",
    summary="Get all IEX API Details",
    response_description="List of IEX API details"
)
async def get_all_iex_api_details(
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    return await iex_api_detail_controller.get_all()

@router.post(
    "/",
    summary="Create IEX API Detail",
    response_description="Creation result",
    status_code=status.HTTP_201_CREATED
)
async def create_iex_api_detail(
    data: IexApiDetailCreate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="iex_api_detail_create",
            message=f"Starting IEX API detail creation for asset {data.asset_id}",
            correlation_id=correlation_id,
            user_id=user_id,
            asset_id=data.asset_id
        )

        result = await iex_api_detail_controller.create(data, user_context)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="iex_api_detail_create",
            message=f"Created IEX API Detail for asset {data.asset_id}",
            username=username,
            correlation_id=correlation_id,
            asset_id=data.asset_id,
            success=True
        )
        return result

    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="IexApiDetailCreateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error creating IEX API detail")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create IEX API detail"
        )

@router.put(
    "/{id}",
    summary="Update IEX API Detail",
    response_description="Update result"
)
async def update_iex_api_detail(
    id: str,
    data: IexApiDetailUpdate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="iex_api_detail_update",
            message=f"Starting IEX API detail update for id {id}",
            correlation_id=correlation_id,
            user_id=user_id,
            record_id=id
        )

        result = await iex_api_detail_controller.update(id, data, user_context)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="iex_api_detail_update",
            message=f"Updated IEX API Detail {id}",
            username=username,
            correlation_id=correlation_id,
            record_id=id,
            success=True
        )
        return result
        
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="IexApiDetailUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error updating IEX API detail id {id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update IEX API detail {id}"
        )

@router.patch(
    "/{id}/status",
    summary="Update Status",
    response_description="Status update result"
)
async def update_status(
    id: str,
    status_data: StatusUpdate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="iex_api_detail_status_update",
            message=f"Starting status update for id {id} to {status_data.status}",
            correlation_id=correlation_id,
            user_id=user_id,
            record_id=id,
            new_status=status_data.status
        )

        result = await iex_api_detail_controller.update_status(id, status_data, user_context)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="iex_api_detail_status_update",
            message=f"Updated status for IEX API Detail {id} to {status_data.status}",
            username=username,
            correlation_id=correlation_id,
            record_id=id,
            new_status=status_data.status,
            success=True
        )
        return result

    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="IexApiDetailStatusUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error updating status for IEX API detail id {id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update status for IEX API detail {id}"
        )

@router.delete(
    "/{id}",
    summary="Delete IEX API Detail",
    response_description="Deletion result"
)
async def delete_iex_api_detail(
    id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="iex_api_detail_delete",
            message=f"Starting deletion of IEX API detail {id}",
            correlation_id=correlation_id,
            user_id=user_id,
            record_id=id
        )

        result = await iex_api_detail_controller.delete(id, user_context)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="delete_iex_api_detail",
            message=f"Deleted IEX API Detail {id}",
            username=username,
            correlation_id=correlation_id,
            record_id=id,
            success=True
        )
        return result

    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="IexApiDetailDeleteError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error deleting IEX API detail id {id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete IEX API detail {id}"
        )
