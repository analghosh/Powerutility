import logging

from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, status, Depends, Query

from ...controller.power_exchange_parameter_controller import power_exchange_parameter_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(
    prefix="/power-exchange-parameters",
    tags=["Power Exchange Parameters V2"]
)

logger = logging.getLogger(__name__)


class PowerExchangeParameterCreate(BaseModel):
    exchange: str = Field(..., description="iex / hpx / pxil")
    market: str = Field(..., description="rtm / dam / gdam / hpdam")
    bid_type: Optional[str] = Field(None, description="single_bid / block_bid / both")
    allocation_percent: Optional[float] = Field(None, ge=0, le=100, strict=True)
    ocf_standalone: Optional[str] = Field(None, description="ocf / standalone")
    premium_discount: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "exchange": "iex",
                "market": "rtm",
                "bid_type": "single_bid",
                "allocation_percent": 100,
                "ocf_standalone": "standalone",
                "premium_discount": "premium"
            }
        }
    )


class PowerExchangeParameterUpdate(BaseModel):
    exchange: Optional[str] = None
    market: Optional[str] = Field(None, description="rtm / dam / gdam / hpdam")
    bid_type: Optional[str] = Field(None, description="single_bid / block_bid / both")
    allocation_percent: Optional[float] = Field(None, ge=0, le=100, strict=True)
    ocf_standalone: Optional[str] = Field(None, description="ocf / standalone")
    premium_discount: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bid_type": "block_bid",
                "allocation_percent": 75,
                "ocf_standalone": "standalone"
            }
        }
    )


class PowerExchangeParameterResponse(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    exchange: str
    market: str
    bid_type: Optional[str] = None
    allocation_percent: Optional[float] = None
    ocf_standalone: Optional[str] = None
    premium_discount: Optional[str] = None
    is_deleted: Optional[bool] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class PowerExchangeParameterListResponse(BaseModel):
    parameters: List[PowerExchangeParameterResponse]
    total: int
    skip: int
    limit: int
    timestamp: Optional[str] = None
    correlation_id: Optional[str] = None


class BulkCreatePowerExchangeRequest(BaseModel):
    parameters: List[PowerExchangeParameterCreate]


class BulkCreatePowerExchangeResponse(BaseModel):
    inserted_ids: List[str]
    count: int
    timestamp: str
    correlation_id: Optional[str] = None


@router.post(
    "",
    response_model=PowerExchangeParameterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Power Exchange Parameter Config",
    description="Create a new power exchange parameter config"
)
async def create_parameter(
    request: Request,
    parameter: PowerExchangeParameterCreate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="power_exchange_parameter_create",
            message="Starting power exchange parameter creation",
            correlation_id=correlation_id,
            user_id=user_id,
            exchange=parameter.exchange
        )

        result = await power_exchange_parameter_controller.create_parameter(parameter.model_dump())

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_parameter_create",
            message="Power exchange parameter created successfully",
            username=username,
            correlation_id=correlation_id,
            exchange=parameter.exchange,
            success=True
        )

        return result

    except ValueError as ve:
        audit_logger.log_error_event(
            service="processing",
            error_name="PowerExchangeParameterValidationError",
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
            error_name="PowerExchangeParameterCreateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error creating power exchange parameter")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create power exchange parameter"
        )


@router.post(
    "/bulk",
    response_model=BulkCreatePowerExchangeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk Create Power Exchange Parameter Configs",
    description="Create multiple power exchange parameters at once"
)
async def create_parameters_bulk(
    request: Request,
    bulk_request: BulkCreatePowerExchangeRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="power_exchange_parameter_bulk_create",
            message=f"Starting bulk power exchange parameter creation ({len(bulk_request.parameters)} items)",
            correlation_id=correlation_id,
            user_id=user_id,
            count=len(bulk_request.parameters)
        )

        parameters = [param.model_dump() for param in bulk_request.parameters]
        result = await power_exchange_parameter_controller.create_parameters_bulk(parameters)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_parameter_bulk_create",
            message=f"Bulk created {result['count']} power exchange parameters",
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
            error_name="PowerExchangeParameterBulkValidationError",
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
            error_name="PowerExchangeParameterBulkCreateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error creating power exchange parameters in bulk")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create power exchange parameters in bulk"
        )


@router.get(
    "",
    response_model=PowerExchangeParameterListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get All Power Exchange Parameter Configs",
    description="Get all power exchange configurations with pagination"
)
async def get_all_parameters(
    request: Request,
    skip: int = Query(0, description="Records to skip", ge=0),
    limit: int = Query(100, description="Max records to return", ge=1, le=1000),
    sort_by: str = Query("created_at", description="Field to sort by"),
    sort_order: int = Query(-1, description="Sort order (1 or -1)"),
    exchange: Optional[str] = Query(None, description="Filter by exchange name (iex, hpx, pxil)"),
    market: Optional[str] = Query(None, description="Filter by market name (rtm, dam)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await power_exchange_parameter_controller.get_all_parameters(
            skip=skip,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
            exchange=exchange,
            market=market
        )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_parameter_list",
            message="Power exchange parameters fetched successfully",
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
            error_name="PowerExchangeParameterListError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error fetching power exchange parameters")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch power exchange parameters"
        )


@router.get(
    "/{parameter_id}",
    response_model=PowerExchangeParameterResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Power Exchange Parameter Config by ID",
    description="Get a specific power exchange configuration by ID"
)
async def get_parameter_by_id(
    request: Request,
    parameter_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await power_exchange_parameter_controller.get_parameter_by_id(parameter_id)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Power exchange parameter config with ID {parameter_id} not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_parameter_get",
            message="Power exchange parameter fetched successfully",
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
            error_name="PowerExchangeParameterGetError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error fetching power exchange parameter")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch power exchange parameter"
        )


@router.get(
    "/exchange/{exchange}",
    response_model=List[PowerExchangeParameterResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Power Exchange Parameter Configs by Exchange Name",
    description="Get all power exchange configurations for a specific exchange"
)
async def get_parameter_by_exchange(
    request: Request,
    exchange: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await power_exchange_parameter_controller.get_parameter_by_exchange(exchange)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_parameter_get_by_exchange",
            message="Power exchange parameters fetched by exchange successfully",
            correlation_id=correlation_id,
            exchange=exchange,
            success=True
        )

        return result

    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PowerExchangeParameterGetByExchangeError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error fetching power exchange parameter by exchange")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch power exchange parameter"
        )


@router.get(
    "/exchange/{exchange}/market/{market}",
    response_model=PowerExchangeParameterResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Power Exchange Parameter Config by Exchange and Market Name",
    description="Get specific power exchange configuration by exchange name and market"
)
async def get_parameter_by_exchange_and_market(
    request: Request,
    exchange: str,
    market: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await power_exchange_parameter_controller.get_parameter_by_exchange_and_market(exchange, market)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Power exchange config with exchange '{exchange}' and market '{market}' not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_parameter_get_by_exchange_and_market",
            message="Power exchange parameter fetched by exchange and market successfully",
            correlation_id=correlation_id,
            exchange=exchange,
            success=True
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="PowerExchangeParameterGetByExchangeAndMarketError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error fetching power exchange parameter by exchange and market")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch power exchange parameter"
        )


@router.put(
    "/{parameter_id}",
    response_model=PowerExchangeParameterResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Power Exchange Parameter Config",
    description="Update an existing power exchange parameter config by ID"
)
async def update_parameter(
    request: Request,
    parameter_id: str,
    parameter: PowerExchangeParameterUpdate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        update_data = parameter.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )

        result = await power_exchange_parameter_controller.update_parameter(parameter_id, update_data)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Power exchange parameter config with ID {parameter_id} not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_parameter_update",
            message="Power exchange parameter updated successfully",
            username=username,
            correlation_id=correlation_id,
            parameter_id=parameter_id,
            exchange=result.get("exchange"),
            success=True
        )

        return result

    except HTTPException:
        raise
    except ValueError as ve:
        audit_logger.log_error_event(
            service="processing",
            error_name="PowerExchangeParameterUpdateValidationError",
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
            error_name="PowerExchangeParameterUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error updating power exchange parameter")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update power exchange parameter"
        )


@router.delete(
    "/{parameter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Power Exchange Parameter Config",
    description="Delete a power exchange configuration by ID"
)
async def delete_parameter(
    request: Request,
    parameter_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await power_exchange_parameter_controller.delete_parameter(parameter_id, deleted_by=username)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Power exchange parameter config with ID {parameter_id} not found"
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="power_exchange_parameter_delete",
            message="Power exchange parameter deleted successfully",
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
            error_name="PowerExchangeParameterDeleteError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error deleting power exchange parameter")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete power exchange parameter"
        )
