"""
API Key Management Routes
Admin-only endpoints for managing client API keys for HMAC authentication
Requires JWT authentication with admin role
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Dict, Any, List, Optional
import logging

from ...dependencies import get_user_context
from ....core.dependencies import get_audit_logger
from libs.audit_queue.simple_logger import AuditLogger
from ...controller.api_key_management_controller import (
    api_key_management_controller,
    CreateAPIKeyRequest,
    APIKeyResponse,
    APIKeyInfo,
    APIKeyStatusResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/api-keys",
    tags=["API Key Management"]
)


def check_admin_role(user_context: Dict[str, Any]) -> None:
    """
    Check if user has admin role
    
    Args:
        user_context: User context from JWT authentication
        
    Raises:
        HTTPException: If user is not admin
    """
    roles = user_context.get("roles", [])
    if "admin" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required for API key management"
        )


# ===========================
# Endpoints
# ===========================

@router.post(
    "/",
    response_model=APIKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create API Key",
    description="Create a new API key for a client. The secret is only shown once in the response - save it immediately."
)
async def create_api_key(
    request: CreateAPIKeyRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Create a new API key for a client.
    
    Requires admin role.
    Generates both API key and secret automatically.
    Secret is only returned once - client must save it securely.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    # Check admin role
    check_admin_role(user_context)
    
    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="api_key_create_start",
            message=f"Starting API key creation for client: {request.name}",
            correlation_id=correlation_id,
            user_id=user_id
        )
        
        result = await api_key_management_controller.create_api_key(request, username)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="api_key_create",
            message=f"Created API key for client: {request.name}",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="APIKeyCreateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error creating API key")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create API key: {str(e)}"
        )


@router.get(
    "/",
    response_model=List[APIKeyInfo],
    summary="List API Keys",
    description="List all API keys. Secrets are never returned for security."
)
async def list_api_keys(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    List all API keys.
    
    Requires admin role.
    Secrets are never returned in list operations.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    # Check admin role
    check_admin_role(user_context)
    
    try:
        result = await api_key_management_controller.list_api_keys(is_active=is_active)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="api_key_list",
            message=f"Listed {len(result)} API keys",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="APIKeyListError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error listing API keys")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list API keys: {str(e)}"
        )


@router.get(
    "/{api_key}",
    response_model=APIKeyInfo,
    summary="Get API Key Info",
    description="Get information about a specific API key. Secret is never returned."
)
async def get_api_key(
    api_key: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Get API key information.
    
    Requires admin role.
    Secret is never returned.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    # Check admin role
    check_admin_role(user_context)
    
    try:
        result = await api_key_management_controller.get_api_key(api_key)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="api_key_get",
            message=f"Retrieved API key info: {api_key[:8]}...",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="APIKeyGetError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error getting API key")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get API key: {str(e)}"
        )


@router.patch(
    "/{api_key}/activate",
    response_model=APIKeyStatusResponse,
    summary="Activate API Key",
    description="Activate a deactivated API key"
)
async def activate_api_key(
    api_key: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Activate an API key.
    
    Requires admin role.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    # Check admin role
    check_admin_role(user_context)
    
    try:
        result = await api_key_management_controller.activate_api_key(api_key)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="api_key_activate",
            message=f"Activated API key: {api_key[:8]}...",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="APIKeyActivateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error activating API key")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to activate API key: {str(e)}"
        )


@router.patch(
    "/{api_key}/deactivate",
    response_model=APIKeyStatusResponse,
    summary="Deactivate API Key",
    description="Deactivate an API key (revoke access)"
)
async def deactivate_api_key(
    api_key: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Deactivate an API key.
    
    Requires admin role.
    This revokes access but preserves the key for audit purposes.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    # Check admin role
    check_admin_role(user_context)
    
    try:
        result = await api_key_management_controller.deactivate_api_key(api_key)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="api_key_deactivate",
            message=f"Deactivated API key: {api_key[:8]}...",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="APIKeyDeactivateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error deactivating API key")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to deactivate API key: {str(e)}"
        )