"""
Bid Market V2 Router
CRUD endpoints for bid market management with JWT authentication
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends, Query
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field, validator
from datetime import datetime
import logging

from ...controller.bid_market_controller import bid_market_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(
    prefix="/bid-markets",
    tags=["Bid Markets V2"]
)

logger = logging.getLogger(__name__)


# Pydantic Models
class BidMarketCreate(BaseModel):
    """Schema for creating a bid market"""
    type: str = Field(..., description="Market type (e.g., DAM, RTM)")
    name: str = Field(..., description="Market name (unique identifier)")
    status: str = Field(..., description="Market status (active or inactive)")

    @validator('status')
    def validate_status(cls, v):
        if v not in ['active', 'inactive']:
            raise ValueError('status must be either "active" or "inactive"')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "type": "DAM",
                "name": "Day Ahead Market - IEX",
                "status": "active"
            }
        }


class BidMarketUpdate(BaseModel):
    """Schema for updating a bid market"""
    type: Optional[str] = Field(None, description="Market type")
    name: Optional[str] = Field(None, description="Market name")
    status: Optional[str] = Field(None, description="Market status (active or inactive)")

    @validator('status')
    def validate_status(cls, v):
        if v is not None and v not in ['active', 'inactive']:
            raise ValueError('status must be either "active" or "inactive"')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "status": "inactive"
            }
        }


class BidMarketResponse(BaseModel):
    """Schema for bid market response"""
    id: str = Field(..., alias="_id", description="Bid market ID")
    type: str = Field(..., description="Market type")
    name: str = Field(..., description="Market name")
    status: str = Field(..., description="Market status")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        populate_by_name = True


class BidMarketListResponse(BaseModel):
    """Schema for bid market list response"""
    bid_markets: List[BidMarketResponse] = Field(..., description="List of bid markets")
    total: int = Field(..., description="Total number of bid markets")
    skip: int = Field(..., description="Number of records skipped")
    limit: int = Field(..., description="Maximum number of records returned")
    filters: Dict[str, Optional[str]] = Field(..., description="Applied filters")
    timestamp: str = Field(..., description="Response timestamp")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


class BulkCreateRequest(BaseModel):
    """Schema for bulk bid market creation"""
    bid_markets: List[BidMarketCreate] = Field(..., description="List of bid markets to create")

    class Config:
        json_schema_extra = {
            "example": {
                "bid_markets": [
                    {"type": "DAM", "name": "Day Ahead Market - IEX", "status": "active"},
                    {"type": "RTM", "name": "Real Time Market - IEX", "status": "active"},
                    {"type": "DAM", "name": "Day Ahead Market - PXIL", "status": "inactive"}
                ]
            }
        }


class BulkCreateResponse(BaseModel):
    """Schema for bulk create response"""
    inserted_ids: List[str] = Field(..., description="List of inserted bid market IDs")
    count: int = Field(..., description="Number of bid markets created")
    timestamp: str = Field(..., description="Response timestamp")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


@router.post(
    "",
    response_model=BidMarketResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Bid Market",
    description="Create a new bid market with type, name, and status"
)
async def create_bid_market(
    request: Request,
    bid_market: BidMarketCreate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Create a new bid market"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="bid_market_create",
            message="Starting bid market creation",
            correlation_id=correlation_id,
            user_id=user_id,
            market_name=bid_market.name,
            market_type=bid_market.type
        )

        result = await bid_market_controller.create_bid_market(bid_market.dict())

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_market_create",
            message="Bid market created successfully",
            username=username,
            correlation_id=correlation_id,
            market_name=bid_market.name,
            market_type=bid_market.type,
            success=True
        )

        return result

    except ValueError as ve:
        audit_logger.log_error_event(
            service="processing",
            error_name="BidMarketValidationError",
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
            error_name="BidMarketCreateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error creating bid market")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create bid market"
        )


@router.post(
    "/bulk",
    response_model=BulkCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk Create Bid Markets",
    description="Create multiple bid markets at once"
)
async def create_bid_markets_bulk(
    request: Request,
    bulk_request: BulkCreateRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Create multiple bid markets in bulk"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="bid_market_bulk_create",
            message=f"Starting bulk bid market creation ({len(bulk_request.bid_markets)} items)",
            correlation_id=correlation_id,
            user_id=user_id,
            count=len(bulk_request.bid_markets)
        )

        bid_markets = [bid_market.dict() for bid_market in bulk_request.bid_markets]
        result = await bid_market_controller.create_bid_markets_bulk(bid_markets)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_market_bulk_create",
            message=f"Bulk created {result['count']} bid markets",
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
            error_name="BidMarketBulkValidationError",
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
            error_name="BidMarketBulkCreateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error creating bid markets in bulk")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create bid markets in bulk"
        )


@router.get(
    "",
    response_model=BidMarketListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get All Bid Markets",
    description="Get all bid markets with pagination and filtering"
)
async def get_all_bid_markets(
    request: Request,
    skip: int = Query(0, description="Number of records to skip", ge=0),
    limit: int = Query(100, description="Maximum number of records to return", ge=1, le=1000),
    sort_by: str = Query("created_at", description="Field to sort by"),
    sort_order: int = Query(-1, description="Sort order (1 for ascending, -1 for descending)"),
    status: Optional[str] = Query(None, description="Filter by status (active/inactive)"),
    type: Optional[str] = Query(None, description="Filter by type"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get all bid markets with pagination and filtering"""
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await bid_market_controller.get_all_bid_markets(
            skip=skip,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
            status_filter=status,
            type_filter=type
        )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_market_list",
            message="Bid markets fetched successfully",
            correlation_id=correlation_id,
            count=len(result['bid_markets']),
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
            error_name="BidMarketListError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error fetching bid markets")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch bid markets"
        )


@router.get(
    "/{bid_market_id}",
    response_model=BidMarketResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Bid Market by ID",
    description="Get a specific bid market by its ID"
)
async def get_bid_market_by_id(
    request: Request,
    bid_market_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get bid market by ID"""
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await bid_market_controller.get_bid_market_by_id(bid_market_id)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bid market with ID {bid_market_id} not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_market_get",
            message="Bid market fetched successfully",
            correlation_id=correlation_id,
            bid_market_id=bid_market_id,
            success=True
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="BidMarketGetError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error fetching bid market")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch bid market"
        )


@router.get(
    "/name/{name}",
    response_model=BidMarketResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Bid Market by Name",
    description="Get a specific bid market by its name"
)
async def get_bid_market_by_name(
    request: Request,
    name: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get bid market by name"""
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await bid_market_controller.get_bid_market_by_name(name)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bid market with name '{name}' not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_market_get_by_name",
            message="Bid market fetched by name successfully",
            correlation_id=correlation_id,
            market_name=name,
            success=True
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="BidMarketGetByNameError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error fetching bid market by name")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch bid market"
        )


@router.get(
    "/status/{status}",
    response_model=List[BidMarketResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Bid Markets by Status",
    description="Get all bid markets with a specific status (active/inactive)"
)
async def get_bid_markets_by_status(
    request: Request,
    status: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get bid markets by status"""
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        if status not in ['active', 'inactive']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Status must be either 'active' or 'inactive'"
            )

        result = await bid_market_controller.get_bid_markets_by_status(status)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_market_get_by_status",
            message=f"Bid markets fetched by status successfully",
            correlation_id=correlation_id,
            status=status,
            count=len(result),
            success=True
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="BidMarketGetByStatusError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error fetching bid markets by status")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch bid markets"
        )


@router.get(
    "/type/{market_type}",
    response_model=List[BidMarketResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Bid Markets by Type",
    description="Get all bid markets with a specific type"
)
async def get_bid_markets_by_type(
    request: Request,
    market_type: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get bid markets by type"""
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await bid_market_controller.get_bid_markets_by_type(market_type)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_market_get_by_type",
            message=f"Bid markets fetched by type successfully",
            correlation_id=correlation_id,
            market_type=market_type,
            count=len(result),
            success=True
        )

        return result

    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="BidMarketGetByTypeError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error fetching bid markets by type")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch bid markets"
        )


@router.put(
    "/{bid_market_id}",
    response_model=BidMarketResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Bid Market",
    description="Update an existing bid market by ID"
)
async def update_bid_market(
    request: Request,
    bid_market_id: str,
    bid_market: BidMarketUpdate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Update bid market by ID"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        # Only include fields that are set
        update_data = bid_market.dict(exclude_unset=True)
        
        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )

        result = await bid_market_controller.update_bid_market(bid_market_id, update_data)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bid market with ID {bid_market_id} not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_market_update",
            message="Bid market updated successfully",
            username=username,
            correlation_id=correlation_id,
            bid_market_id=bid_market_id,
            success=True
        )

        return result

    except HTTPException:
        raise
    except ValueError as ve:
        audit_logger.log_error_event(
            service="processing",
            error_name="BidMarketUpdateValidationError",
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
            error_name="BidMarketUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error updating bid market")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update bid market"
        )


@router.delete(
    "/{bid_market_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Bid Market",
    description="Delete a bid market by ID"
)
async def delete_bid_market(
    request: Request,
    bid_market_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Delete bid market by ID"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await bid_market_controller.delete_bid_market(bid_market_id)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bid market with ID {bid_market_id} not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_market_delete",
            message="Bid market deleted successfully",
            username=username,
            correlation_id=correlation_id,
            bid_market_id=bid_market_id,
            success=True
        )

        return None

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="BidMarketDeleteError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error deleting bid market")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete bid market"
        )