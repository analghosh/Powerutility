# apps/processing/api/price_group_routes.py
from __future__ import annotations

from typing import Any, Dict, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from ..repository.price_group_repository import PriceGroupRepository
from ..services.price_group_service import PriceGroupService
from ..controller.price_group_controller import PriceGroupController
from libs.models.price_group_entity import PriceGroupCreate, PriceGroupUpdate
from libs.audit_queue.simple_logger import AuditLogger
from libs.middleware import get_correlation_id
from ....core.dependencies import get_audit_logger
from ...dependencies import get_user_context

router = APIRouter(prefix="/price-groups", tags=["Price Group V2"])
logger = logging.getLogger(__name__)


# -------- Dependencies --------
# Cache repository instance to avoid recreating connections
_repo_instance = None

def get_repo() -> PriceGroupRepository:
    global _repo_instance
    if _repo_instance is None:
        _repo_instance = PriceGroupRepository()
    return _repo_instance

def get_service(repo: PriceGroupRepository = Depends(get_repo), audit_logger: AuditLogger = Depends(get_audit_logger)) -> PriceGroupService:
    return PriceGroupService(repo, audit_logger)

def get_controller(service: PriceGroupService = Depends(get_service)) -> PriceGroupController:
    return PriceGroupController(service)


# -------- API Schemas --------
class PriceGroupIn(PriceGroupCreate):
    price_group: str = Field(min_length=1, max_length=50)

class PriceGroupOut(BaseModel):
    price_group_id: str
    price_group: str
    price_from: float
    price_to: float
    inserted_date: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class PriceGroupListOut(BaseModel):
    items: List[PriceGroupOut]
    total: int
    page: int
    page_size: int


# -------- Helper Functions --------
def build_req_meta(request: Request) -> Dict[str, Any]:
    ip = (request.headers.get("X-Forwarded-For") or request.client.host or "").split(",")[0].strip()
    ua = request.headers.get("User-Agent")
    return {"ip_address": ip, "user_agent": ua}


# -------- Routes with Logging & Authentication --------
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=str,
    summary="Create a price group",
)
async def create_price_group(
    payload: PriceGroupIn,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    controller: PriceGroupController = Depends(get_controller),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id", "unknown")
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    req_meta = build_req_meta(request)

    try:
        new_id = await controller.create(payload.model_dump(), user_context, req_meta)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            username=username,
            action="price_group_create",
            message=f"Created price group {payload.price_group}",
            correlation_id=correlation_id,
            record_id=new_id,
            success=True
        )

        logger.info(f"Price group {payload.price_group} created by user {user_id}")
        return new_id

    except ValueError as e:
        # Handle duplicate price group error
        audit_logger.log_error_event(
            service="processing",
            error_name="PriceGroupDuplicateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="ValueError"
        )
        logger.warning(f"Duplicate price group creation attempt by user {user_id}: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PriceGroupCreateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        logger.exception(f"Error creating price group by user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to create price group")


@router.get(
    "/{price_group_id}",
    response_model=Optional[PriceGroupOut],
    summary="Get a price group by id",
)
async def get_price_group(
    price_group_id: str,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    controller: PriceGroupController = Depends(get_controller),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id", "unknown")
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    req_meta = build_req_meta(request)

    try:
        doc = await controller.get(price_group_id, user_context, req_meta)
        if not doc:
            raise HTTPException(status_code=404, detail="Price group not found")

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            username=username,
            action="price_group_view",
            message=f"Viewed price group {price_group_id}",
            correlation_id=correlation_id,
            record_id=price_group_id,
            success=True
        )

        logger.info(f"Price group {price_group_id} fetched by user {user_id}")
        return doc

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PriceGroupFetchError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        logger.exception(f"Error fetching price group {price_group_id} by user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to fetch price group")


@router.get(
    "/",
    response_model=PriceGroupListOut,
    summary="List price groups",
)
@router.get(
    "",
    response_model=PriceGroupListOut,
    summary="List price groups",
)
async def list_price_groups(
    request: Request,
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    price_group: Optional[str] = Query(None),
    price_from_min: Optional[float] = Query(None),
    price_to_max: Optional[float] = Query(None),
    user_context: Dict[str, Any] = Depends(get_user_context),
    controller: PriceGroupController = Depends(get_controller),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id", "unknown")
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    req_meta = build_req_meta(request)

    try:
        filters: Dict[str, Any] = {}
        if price_group is not None:
            filters["price_group"] = price_group
        if price_from_min is not None or price_to_max is not None:
            rng: Dict[str, Any] = {}
            if price_from_min is not None:
                rng["$gte"] = price_from_min
            if price_to_max is not None:
                rng["$lte"] = price_to_max
            if "$gte" in rng:
                filters["price_from"] = {"$gte": rng["$gte"]}
            if "$lte" in rng:
                filters["price_to"] = {"$lte": rng["$lte"]}

        # Add timing to identify bottlenecks
        import time
        controller_start = time.time()
        logger.info(f"🔍 Starting controller.list call")
        
        result = await controller.list(filters, page, page_size, user_context, req_meta)
        
        controller_time = time.time() - controller_start
        logger.info(f"🔍 Controller.list completed in {controller_time:.3f}s")

        # Single audit log after successful operation
        audit_start = time.time()
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            username=username,
            action="price_group_list",
            message="Listed price groups",
            correlation_id=correlation_id,
            record_count=len(result.get("items", [])),
            filters=filters,
            success=True
        )
        audit_time = time.time() - audit_start
        logger.info(f"🔍 Audit logging completed in {audit_time:.3f}s")


        
        logger.info(f"Price groups listed by user {user_id}, page {page}")
        return result

    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PriceGroupListError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        logger.exception(f"Error listing price groups by user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to list price groups")


@router.patch(
    "/{price_group_id}",
    response_model=Optional[PriceGroupOut],
    summary="Update a price group",
)
async def update_price_group(
    price_group_id: str,
    updates: PriceGroupUpdate,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    controller: PriceGroupController = Depends(get_controller),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id", "unknown")
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    req_meta = build_req_meta(request)

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="price_group_update",
            message=f"Updating price group {price_group_id}",
            correlation_id=correlation_id,
            user_id=user_id
        )
        updated = await controller.update(price_group_id, updates.model_dump(exclude_none=True), user_context, req_meta)
        if not updated:
            raise HTTPException(status_code=404, detail="Price group not found or not updated")

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            username=username,
            action="price_group_update",
            message=f"Updated price group {price_group_id}",
            correlation_id=correlation_id,
            record_id=price_group_id,
            success=True
        )

        logger.info(f"Price group {price_group_id} updated by user {user_id}")
        return updated

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PriceGroupUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        logger.exception(f"Error updating price group {price_group_id} by user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to update price group")


@router.delete(
    "/{price_group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a price group",
)
async def delete_price_group(
    price_group_id: str,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    controller: PriceGroupController = Depends(get_controller),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id", "unknown")
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    req_meta = build_req_meta(request)

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="price_group_delete",
            message=f"Deleting price group {price_group_id}",
            correlation_id=correlation_id,
            user_id=user_id
        )
        ok = await controller.delete(price_group_id, user_context, req_meta)
        if not ok:
            raise HTTPException(status_code=404, detail="Price group not found")

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            username=username,
            action="price_group_delete",
            message=f"Deleted price group {price_group_id}",
            correlation_id=correlation_id,
            record_id=price_group_id,
            success=True
        )

        logger.info(f"Price group {price_group_id} deleted by user {user_id}")
        return None

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PriceGroupDeleteError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        logger.exception(f"Error deleting price group {price_group_id} by user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to delete price group")
