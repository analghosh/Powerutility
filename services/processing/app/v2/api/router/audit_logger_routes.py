"""
Audit Logger V2 Router
Enhanced audit log retrieval endpoints with JWT authentication and correlation tracking
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends, Query
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from ...controller.audit_logger_controller import audit_logger_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(
    prefix="/audit-logs",
    tags=["Audit Logs V2"]
)

logger = logging.getLogger(__name__)


class AuditLogEntry(BaseModel):
    """Schema for a single audit log entry"""
    id: Optional[str] = Field(None, alias="_id", description="MongoDB ObjectId as string")
    timestamp: Optional[datetime] = Field(None, description="Timestamp of the audit event")
    service: Optional[str] = Field(None, description="Service name (auth, ingestion, processing)")
    action_type: Optional[str] = Field(None, description="Type of action performed")
    user_id: Optional[str] = Field(None, description="User ID who performed the action")
    username: Optional[str] = Field(None, description="Username from auth_db")
    name: Optional[str] = Field(None, description="Full name from auth_db profile")
    email: Optional[str] = Field(None, description="Email from auth_db")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")
    data: Optional[Dict[str, Any]] = Field(None, description="Additional audit data")
    success: Optional[bool] = Field(None, description="Whether the action was successful")
    error_message: Optional[str] = Field(None, description="Error message if action failed")


class PaginationMeta(BaseModel):
    """Pagination metadata"""
    skip: int = Field(..., description="Number of records skipped")
    limit: Optional[int] = Field(None, description="Maximum number of records requested")
    returned: int = Field(..., description="Number of records returned in this response")
    total: Optional[int] = Field(None, description="Total number of matching records")


class AuditLogsPayload(BaseModel):
    """Audit logs data payload"""
    result: List[AuditLogEntry] = Field(..., description="List of audit log entries")
    meta: PaginationMeta = Field(..., description="Pagination metadata")


class AuditLogsResponse(BaseModel):
    """Response schema for Audit Logs API"""
    data: AuditLogsPayload = Field(..., description="Audit logs data payload with metadata")
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


@router.get(
    "",
    response_model=AuditLogsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Audit Logs",
    description="""
    Fetches **audit logs** from the audit database with filtering options.

    ### Query Parameters:
    - `type`: Filter type - "authentication", "roles", or "data"
    - `skip`: Number of records to skip (for pagination, default 0)
    - `limit`: Maximum number of records to return (for pagination, default 100)

    ### Filter Behavior:
    - **type=authentication**: Searches for `service: auth` AND `data.action_type: login`
    - **type=roles**: Returns **latest role assignment per user** (grouped by user_id, most recent first)
    - **type=data**: Searches for:
      - `service: ingestion` AND `data.action_type: upload` OR
      - `service: processing` AND `data.action_type: download`

    ### Notes:
    - User information (username, email) is enriched from auth_db.users collection
    - Enhanced with JWT authentication and correlation tracking
    - All timestamps are in UTC
    - If no type is specified, defaults to "authentication"
    """,
    response_description="Returns a list of audit log entries matching the criteria.",
    responses={
        200: {"description": "Audit logs retrieved successfully"},
        401: {"description": "Unauthorized - Invalid or missing JWT token"},
        404: {"description": "No audit logs found for the given filters"},
        422: {"description": "Validation error - Invalid query parameters"},
        500: {"description": "Internal Server Error - Failed to fetch audit logs"},
    },
)
async def get_audit_logs(
        request: Request,
        type: Optional[str] = Query(
            None,
            description="Filter type: 'authentication', 'roles', or 'data' (defaults to 'authentication')",
            regex="^(authentication|roles|data)$"
        ),
        skip: int = Query(0, description="Number of records to skip (for pagination)", ge=0),
        limit: int = Query(100, description="Maximum number of records to return", ge=1, le=1000),
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch audit logs with enhanced authentication and logging.
    
    Args:
        request: FastAPI request object
        type: Filter type - "authentication", "roles", or "data"
        skip: Number of records to skip
        limit: Maximum number of records to return
        user_context: Current authenticated user context
        audit_logger: Audit logger instance
        
    Returns:
        AuditLogsResponse: List of audit log entries with metadata
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="audit_logs_fetch",
            message="Starting audit logs fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            filter_type=type
        )

        # Prepare filters for the controller
        filters = {
            "type": type,
            "skip": skip,
            "limit": limit
        }

        # Fetch audit logs from controller
        result = await audit_logger_controller.get_audit_logs(filters)

        if not result or not result.get("logs"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No audit logs found for the specified filters."
            )

        logs = result.get("logs", [])
        total = result.get("total", len(logs))

        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="audit_logs_view",
            message="User successfully fetched audit logs",
            username=username,
            correlation_id=correlation_id,
            record_count=len(logs),
            success=True,
            **filters
        )

        logger.info(
            f"Audit logs fetched successfully for user {user_id}, "
            f"filters: {filters}, returned: {len(logs)}"
        )

        # Construct the response payload
        data_payload = AuditLogsPayload(
            result=logs,
            meta=PaginationMeta(
                skip=skip,
                limit=limit,
                returned=len(logs),
                total=total
            )
        )

        return AuditLogsResponse(
            data=data_payload,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="AuditLogsFetchError",
            error_message=f"Failed to fetch audit logs: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__,
            **filters
        )

        logger.exception(f"Error fetching audit logs for user {user_id}, filters: {filters}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch audit logs."
        )
