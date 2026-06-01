"""
Provider Management Routes
Handles provider CRUD operations and management endpoints
"""

from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ....services.provider_service import ProviderManagementService
from libs.models.provider_models import (
    CreateProviderRequest, UpdateProviderRequest, ProviderResponse, 
    ProviderListResponse, EnableDisableRequest, IngestionType, DataType,
    CreateProviderResponse, UpdateProviderResponse, EnableDisableProviderResponse,
    DeleteProviderResponse, IngestionTypeResponse, DataTypeResponse
)
from ....middleware.page_access import require_page_access

router = APIRouter(prefix="/providers", tags=["Provider Management"])
audit_logger = get_audit_logger()

# Initialize provider service
provider_service = ProviderManagementService()


@router.get("", response_model=ProviderListResponse)
async def get_providers(
    ingestion_type: Optional[IngestionType] = Query(None, description="Filter by ingestion type"),
    data_type: Optional[DataType] = Query(None, description="Filter by data type"),
    enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    _current_user: Dict[str, Any] = Depends(require_page_access("Provider Management"))
):
    """
    Get all providers with optional filters
    
    Args:
        ingestion_type: Filter by ingestion type (UPLOAD, API, FTP, SCRAPE)
        data_type: Filter by data type (DEMAND, SUPPLY, EXCHANGE, BID)
        enabled: Filter by enabled status
        skip: Number of records to skip for pagination
        limit: Maximum number of records to return
        
    Returns:
        ProviderListResponse: List of providers with total count
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    # Debug logging to help identify issues
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"GET /providers called - user_id: {user_id}, session_id: {session_id}")
    logger.info(f"Full _current_user object: {_current_user}")
    logger.info(f"_current_user keys: {list(_current_user.keys()) if _current_user else 'None'}")
    
    try:
        result = await provider_service.get_all_providers(
            ingestion_type=ingestion_type,
            data_type=data_type,
            enabled=enabled,
            skip=skip,
            limit=limit
        )
        
        logger.info(f"Provider service returned {result.total} total providers, {len(result.providers)} returned")
        
        # Log successful provider list retrieval
        try:
            audit_result = audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="provider_list_view",
                message="Provider list viewed",
                session_id=session_id,
                total_providers=result.total,
                returned_providers=len(result.providers),
                ingestion_type=ingestion_type.value if ingestion_type else None,
                data_type=data_type.value if data_type else None,
                enabled=enabled,
                skip=skip,
                limit=limit,
                endpoint="/providers"
            )
            logger.info(f"User activity audit logged successfully: {audit_result}")
        except Exception as audit_error:
            logger.error(f"Failed to log user activity: {audit_error}")
            # Don't fail the request if audit logging fails
        
        return result
        
    except Exception as e:
        # Log failed provider list retrieval
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="provider_list_view_failed",
            message=f"Provider list view failed: {str(e)}",
            session_id=session_id,
            success=False,
            endpoint="/providers",
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=500,
            detail="Failed to get providers"
        ) from e

@router.get("/{provider_id}", response_model=ProviderResponse)
async def get_provider_by_id(
    provider_id: str,
    _current_user: Dict[str, Any] = Depends(require_page_access("Provider Management"))
):
    """
    Get provider information by ID
    
    Args:
        provider_id: Provider ID
        
    Returns:
        ProviderResponse: Provider information
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        provider = await provider_service.get_provider_by_id(provider_id)
        if not provider:
            # Log provider not found
            audit_logger.log_user_action(
                service="ingestion",
                user_id=user_id,
                action="provider_view_not_found",
                message=f"Provider not found: {provider_id}",
                session_id=session_id,
                success=False,
                provider_id=provider_id,
                endpoint=f"/providers/{provider_id}"
            )
        
            raise HTTPException(
                status_code=404,
                detail=f"Provider with ID '{provider_id}' not found"
            )
        
        # Log successful provider view
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="provider_view",
            message=f"Provider viewed: {provider.name}",
            session_id=session_id,
            provider_id=provider_id,
            provider_name=provider.name,
            provider_code=provider.code,
            data_type=provider.data_type,
            ingestion_type=provider.ingestion_type,
            endpoint=f"/providers/{provider_id}"
        )
        
        return provider
        
    except HTTPException:
        raise
    except Exception as e:
        # Log failed provider view
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="provider_view_failed",
            message=f"Provider view failed: {str(e)}",
            session_id=session_id,
            success=False,
            provider_id=provider_id,
            exception_type=type(e).__name__,
            endpoint=f"/providers/{provider_id}"
        )
        
        raise HTTPException(
            status_code=500,
            detail="Failed to get provider"
        ) from e

@router.post("", response_model=CreateProviderResponse)
async def create_provider(
    request: CreateProviderRequest,
    _current_user: Dict[str, Any] = Depends(require_page_access("Provider Management"))
):
    """
    Create a new provider for data ingestion
    
    This endpoint creates a new provider configuration that defines how data from external sources
    will be ingested and mapped to serve collections. Each provider specifies:
    
    - **Schema Definition**: Field structure and validation rules for incoming data
    - **Field Mappings**: How provider fields map to serve collection columns
    - **Data Type**: Which serve collection the data will be stored in (demand, supply, exchange, bid)
    - **Ingestion Method**: How data will be received (upload, API, FTP, scrape)
    
    ## Validation Rules:
    - Provider code + ingestion_type + data_type combination must be unique
    - Schema fields must have valid field types and validation rules
    - Field mappings must be 1:1 (no duplicate source or target fields)
    - Target fields must exist in the serve collection for the specified data type
    - Time_block fields are validated against the time_block master collection
    
    ## Serve Collection Columns by Data Type:
    
    **DEMAND**: scheduled_at, time_block, type, forecast, actual, schedule, drawal, frequency, 
    rostering, mape, mpe, remarks, source_actual, source_forecast, source_rostering, zone
    
    **SUPPLY**: scheduled_at, time_block, plant_name, dc_sg, volume, fc_price, px_price, vc_price, 
    category, technology, price_group, c_s, tm, multiplier, ramping
    
    **EXCHANGE**: market_type, px_name, market, time_block, buy_bid, sell_bid, mcv, mcp, acp, acv, scheduled_at
    
    **BID**: scheduled_at, time_block, bid_type, volume, price, status
    
    **PLANTS**: plant_name, c_s, category, technology, price_group, ramping, tm, fc_price, 
    px_price, vc_price, multiplier, volume, ppa, plant_id
    
    Args:
        request: Create provider request with schema, mappings, and configuration
        
    Returns:
        CreateProviderResponse: Success message with created provider ID and code
        
    Raises:
        400: Bad Request - Invalid provider configuration, duplicate code combination, or validation errors
        401: Unauthorized - Invalid or missing authentication token
        403: Forbidden - Insufficient permissions for provider management
        500: Internal Server Error - Server error during provider creation
        
    Example Request:
    ```json
    {
        "code": "SCADA_DEMAND",
        "ingestion_type": "UPLOAD",
        "fields": [
            {
                "field_type": "datetime",
                "validation": [
                    {"rule": "required", "value": true, "message": "Scheduled time is required"}
                ],
            },
            {
                "field_type": "integer",
                "validation": [
                    {"rule": "min_value", "value": 1, "message": "Time block must be at least 1"},
                    {"rule": "max_value", "value": 48, "message": "Time block cannot exceed 48"}
                ],
            },
            {
                "field_type": "float",
                "validation": [
                    {"rule": "min_value", "value": 0, "message": "Forecast must be non-negative"}
                ],
            }
        ],
        "mapping": [
            {
                "serve_field": "scheduled_at",
            },
            {
                "serve_field": "time_block",
            },
            {
                "serve_field": "forecast", 
            }
        ],
    }
    ```
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        provider_id = await provider_service.create_provider(
            request=request,
            created_by=_current_user.get("user_id")
        )
        
        # Log successful provider creation
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="provider_create",
            message=f"Provider created: {request.name}",
            session_id=session_id,
            provider_id=provider_id,
            provider_name=request.name,
            provider_code=request.code,
            data_type=request.data_type.value,
            ingestion_type=request.ingestion_type.value,
            enabled=request.enabled,
            schema_fields_count=len(request.fields) if request.fields else 0,
            endpoint="/providers"
        )
        
        return CreateProviderResponse(
            message="Provider created successfully",
            provider_id=provider_id,
            provider_code=request.code
        )
        
    except ValueError as e:
        # Log failed provider creation (validation error)
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="provider_create_failed",
            message=f"Provider creation failed: {str(e)}",
            session_id=session_id,
            success=False,
            provider_name=request.name,
            provider_code=request.code,
            exception_type=type(e).__name__,
            endpoint="/providers"
        )
        
        raise HTTPException(
            status_code=400,
            detail=str(e)
        ) from e
    except Exception as e:
        # Log failed provider creation (unexpected error)
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="provider_create_error",
            message=f"Provider creation error: {str(e)}",
            session_id=session_id,
            success=False,
            provider_name=request.name,
            provider_code=request.code,
            exception_type=type(e).__name__,
            endpoint="/providers"
        )
        
        raise HTTPException(
            status_code=500,
            detail="Failed to create provider"
        ) from e

@router.put("/{provider_id}", response_model=UpdateProviderResponse)
async def update_provider(
    provider_id: str,
    request: UpdateProviderRequest,
    _current_user: Dict[str, Any] = Depends(require_page_access("Provider Management"))
):
    """
    Update provider information (partial update supported)
    
    Args:
        provider_id: Provider ID
        request: Update provider request
        
    Returns:
        Dict[str, str]: Success message
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        success = await provider_service.update_provider(provider_id, request)
        
        if not success:
            # Log provider not found for update
            # Generic logging removed for efficiency
            
            raise HTTPException(
                status_code=404,
                detail=f"Provider with ID '{provider_id}' not found"
            )
        
        # Log successful provider update
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="provider_update",
            message=f"Provider updated: {provider_id}",
            session_id=session_id,
            provider_id=provider_id,
            endpoint=f"/providers/{provider_id}"
        )
        
        return UpdateProviderResponse(
            message="Provider updated successfully",
            provider_id=provider_id
        )
        
    except ValueError as e:
        # Log failed provider update (validation error)
        # Generic logging removed for efficiency
        
        raise HTTPException(
            status_code=400,
            detail=str(e)
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        # Log failed provider update (unexpected error)
        # Generic logging removed for efficiency
        
        raise HTTPException(
            status_code=500,
            detail="Failed to update provider"
        ) from e

@router.patch("/{provider_id}/enable-disable", response_model=EnableDisableProviderResponse)
async def enable_disable_provider(
    provider_id: str,
    request: EnableDisableRequest,
    _current_user: Dict[str, Any] = Depends(require_page_access("Provider Management"))
):
    """
    Enable or disable provider
    
    Args:
        provider_id: Provider ID
        request: Enable/disable request
        
    Returns:
        Dict[str, str]: Success message
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        success = await provider_service.enable_disable_provider(provider_id, request.enabled)
        
        if not success:
            # Log provider not found for enable/disable
            # Generic logging removed for efficiency
            
            raise HTTPException(
                status_code=404,
                detail=f"Provider with ID '{provider_id}' not found"
            )
        
        action = "enabled" if request.enabled else "disabled"
        
        # Log successful provider enable/disable
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="provider_enable_disable",
            message=f"Provider enable/disable: {provider_id}",
            session_id=session_id,
            provider_id=provider_id,
            endpoint=f"/providers/{provider_id}/enable-disable"
        )
        
        return EnableDisableProviderResponse(
            message=f"Provider {action} successfully",
            provider_id=provider_id,
            enabled=request.enabled
        )
        
    except ValueError as e:
        # Log failed provider enable/disable (validation error)
        # Generic logging removed for efficiency
        
        raise HTTPException(
            status_code=400,
            detail=str(e)
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        # Log failed provider enable/disable (unexpected error)
        # Generic logging removed for efficiency
        
        raise HTTPException(
            status_code=500,
            detail="Failed to update provider status"
        ) from e

@router.delete("/{provider_id}", response_model=DeleteProviderResponse)
async def delete_provider(
    provider_id: str,
    _current_user: Dict[str, Any] = Depends(require_page_access("Provider Management"))
):
    """
    Delete provider
    
    Args:
        provider_id: Provider ID
        
    Returns:
        Dict[str, str]: Success message
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        success = await provider_service.delete_provider(provider_id)
        
        if not success:
            # Log provider not found for deletion
            # Generic logging removed for efficiency
            
            raise HTTPException(
                status_code=404,
                detail=f"Provider with ID '{provider_id}' not found"
            )
        
        # Log successful provider deletion
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="provider_delete",
            message=f"Provider deleted: {provider_id}",
            session_id=session_id,
            provider_id=provider_id,
            endpoint=f"/providers/{provider_id}"
        )
        
        return DeleteProviderResponse(
            message="Provider deleted successfully",
            provider_id=provider_id
        )
        
    except ValueError as e:
        # Log failed provider deletion (validation error)
        # Generic logging removed for efficiency
        
        raise HTTPException(
            status_code=400,
            detail=str(e)
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        # Log failed provider deletion (unexpected error)
        # Generic logging removed for efficiency
        
        raise HTTPException(
            status_code=500,
            detail="Failed to delete provider"
        ) from e

