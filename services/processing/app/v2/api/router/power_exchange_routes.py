from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
from datetime import datetime
import logging

from libs.models.power_exchange_entity import MarketEnum, ExchangeEnum
from libs.audit_queue.simple_logger import AuditLogger
from ...dependencies import get_user_context
from ....core.dependencies import get_audit_logger
from ...controller.power_exchange_controller import power_exchange_controller

router = APIRouter(
    prefix="/power-exchange",
    tags=["Power Exchange"]
)

logger = logging.getLogger(__name__)


class PowerExchangeCreate(BaseModel):
    key: str = Field(..., min_length=1)
    market: MarketEnum = Field(...)
    exchange: ExchangeEnum = Field(...)
    value: Any = Field(...)


class PowerExchangeUpdate(BaseModel):
    key: Optional[str] = Field(None, min_length=1)
    market: Optional[MarketEnum] = Field(None)
    exchange: Optional[ExchangeEnum] = Field(None)
    value: Optional[Any] = Field(None)


class V2Response(BaseModel):
    data: Any
    timestamp: str
    correlation_id: Optional[str]


@router.post(
    "/",
    response_model=V2Response,
    status_code=status.HTTP_201_CREATED,
    summary="Create a power exchange record"
)
async def create_power_exchange(
    payload: PowerExchangeCreate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> V2Response:
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id")
    try:
        result = await power_exchange_controller.create(
            payload.dict(exclude_none=True)
        )
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_create",
            message=f"Created power exchange record for key '{payload.key}'",
            username=user_context.get("username", "anonymous"),
            correlation_id=correlation_id,
            record_id=str(result.get("_id")),
            success=True
        )
        return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PowerExchangeCreateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error creating power exchange record")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create power exchange record"
        )


@router.get(
    "/",
    response_model=V2Response,
    summary="List power exchange records"
)
async def list_power_exchange(
    key: Optional[str] = Query(None),
    market: Optional[MarketEnum] = Query(None),
    exchange: Optional[ExchangeEnum] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> V2Response:
    correlation_id = user_context.get("correlation_id")
    try:
        result = await power_exchange_controller.get_list(
            key=key,
            market=market.value if market else None,
            exchange=exchange.value if exchange else None,
            page=page,
            page_size=page_size,
        )
        return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error listing power exchange records")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list power exchange records"
        )


@router.get(
    "/{id}",
    response_model=V2Response,
    summary="Get a power exchange record by ID"
)
async def get_power_exchange(
    id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> V2Response:
    correlation_id = user_context.get("correlation_id")
    try:
        result = await power_exchange_controller.get_by_id(id)
        return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error fetching power exchange record {id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch power exchange record"
        )


@router.patch(
    "/{id}",
    response_model=V2Response,
    summary="Update a power exchange record"
)
async def update_power_exchange(
    id: str,
    payload: PowerExchangeUpdate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> V2Response:
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id")
    updates = payload.dict(exclude_none=True)
    try:
        result = await power_exchange_controller.update(id, updates)
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_update",
            message=f"Updated power exchange record {id}",
            username=user_context.get("username", "anonymous"),
            correlation_id=correlation_id,
            record_id=id,
            success=True
        )
        return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PowerExchangeUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error updating power exchange record {id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update power exchange record"
        )


@router.delete(
    "/{id}",
    response_model=V2Response,
    summary="Delete a power exchange record"
)
async def delete_power_exchange(
    id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> V2Response:
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id")
    try:
        result = await power_exchange_controller.delete(id)
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_delete",
            message=f"Deleted power exchange record {id}",
            username=user_context.get("username", "anonymous"),
            correlation_id=correlation_id,
            record_id=id,
            success=True
        )
        return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PowerExchangeDeleteError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error deleting power exchange record {id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete power exchange record"
        )
