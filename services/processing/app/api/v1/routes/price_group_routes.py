# apps/processing/api/price_group_routes.py
from __future__ import annotations

from typing import Any, Dict, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from datetime import datetime

from ....repository.price_group_repository import PriceGroupRepository

from ....services.price_group_service import (
    PriceGroupService,
    PriceGroupPayloadMapper,
)
from ....controller.price_group_controller import PriceGroupController
from libs.models.price_group_entity import PriceGroupCreate, PriceGroupUpdate

router = APIRouter(prefix="/price-groups", tags=["Price Group"])


# -------- Dependencies & helpers --------

def get_repo() -> PriceGroupRepository:
    # If you use DI container, swap this with your provider
    return PriceGroupRepository()

def get_service(repo: PriceGroupRepository = Depends(get_repo)) -> PriceGroupService:
    return PriceGroupService(repo)

def get_controller(
    service: PriceGroupService = Depends(get_service),
) -> PriceGroupController:
    return PriceGroupController(service)

def build_actor(request: Request) -> Dict[str, Any]:
    """Extract actor info from HTTP headers (or fallback to anonymous)."""
    user_id = request.headers.get("X-User-Id") or "anonymous"
    username = request.headers.get("X-Username") or "anonymous"
    return {"user_id": user_id, "username": username}

def build_req_meta(request: Request) -> Dict[str, Any]:
    # Capture IP & user agent for audit trail
    # If you're behind a proxy/load balancer, prefer X-Forwarded-For
    ip = (request.headers.get("X-Forwarded-For") or request.client.host or "").split(",")[0].strip()
    ua = request.headers.get("User-Agent")
    return {"ip_address": ip, "user_agent": ua}


# -------- API Schemas (thin wrappers to control I/O) --------

class PriceGroupIn(PriceGroupCreate):
    """
    Incoming payload (external/legacy field names already match your mapper).
    """
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


# -------- Routes --------

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=str,
    summary="Create a price group",
)
async def create_price_group(
    payload: PriceGroupIn,
    request: Request,
    controller: PriceGroupController = Depends(get_controller),
):
    actor = build_actor(request)
    req_meta = build_req_meta(request)
    new_id = controller.create(payload.model_dump(), actor, req_meta)
    return new_id


@router.get(
    "/{price_group_id}",
    response_model=Optional[PriceGroupOut],
    summary="Get a price group by id",
)
async def get_price_group(
    price_group_id: str,
    request: Request,
    controller: PriceGroupController = Depends(get_controller),
):
    actor = build_actor(request)
    req_meta = build_req_meta(request)
    doc = controller.get(price_group_id, actor, req_meta)
    if not doc:
        # 200 with null is also fine if you prefer; raising 404 is typical REST.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Price group not found")
    return doc


@router.get(
    "",
    response_model=PriceGroupListOut,
    summary="List price groups (with optional filters & pagination)",
)
async def list_price_groups(
    request: Request,
    controller: PriceGroupController = Depends(get_controller),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    price_group: Optional[str] = Query(None, description="Exact match on price_group"),
    price_from_min: Optional[float] = Query(None, description="Filter: price_from >= value"),
    price_to_max: Optional[float] = Query(None, description="Filter: price_to <= value"),
):
    actor = build_actor(request)
    req_meta = build_req_meta(request)

    # Build Mongo-style filters; service will map fields if needed
    filters: Dict[str, Any] = {}
    if price_group is not None:
        filters["price_group"] = price_group
    if price_from_min is not None or price_to_max is not None:
        rng: Dict[str, Any] = {}
        if price_from_min is not None:
            rng["$gte"] = price_from_min
        if price_to_max is not None:
            rng["$lte"] = price_to_max
        # You can choose which bound to apply to which field. Common pattern:
        # filter by overlapping range; here we keep it simple:
        # price_from >= min and price_to <= max (when provided)
        if "$gte" in rng:
            filters["price_from"] = {"$gte": rng["$gte"]}
        if "$lte" in rng:
            filters["price_to"] = {"$lte": rng["$lte"]}

    result = controller.list(filters, page, page_size, actor, req_meta)
    return result  # already in external/legacy field names


@router.patch(
    "/{price_group_id}",
    response_model=Optional[PriceGroupOut],
    summary="Update a price group (partial)",
)
async def update_price_group(
    price_group_id: str,
    updates: PriceGroupUpdate,
    request: Request,
    controller: PriceGroupController = Depends(get_controller),
):
    actor = build_actor(request)
    req_meta = build_req_meta(request)
    updated = controller.update(price_group_id, updates.model_dump(exclude_none=True), actor, req_meta)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Price group not found or not updated")
    return updated


@router.delete(
    "/{price_group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a price group",
)
async def delete_price_group(
    price_group_id: str,
    request: Request,
    controller: PriceGroupController = Depends(get_controller),
):
    actor = build_actor(request)
    req_meta = build_req_meta(request)
    ok = controller.delete(price_group_id, actor, req_meta)
    if not ok:
        # If you prefer idempotent deletes, you could return 204 even if not found.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Price group not found")
    return None
