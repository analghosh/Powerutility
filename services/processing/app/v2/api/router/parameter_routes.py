"""
Parameter V2 Router
CRUD endpoints for parameter management with JWT authentication
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends, Query, Body
from typing import Dict, Any, Optional, List, Union
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from ...controller.parameter_controller import parameter_controller
from ...services.parameter_service import (
    REPORT_CHARGE_RATE_KEYS,
    parameter_service,
)
from ...dependencies import get_user_context
from ...services.idf_forecast_switch_service import ManualSwitchNotAllowedError
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(
    prefix="/parameters",
    tags=["Parameters V2"]
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bid configuration (parameter master) models
# ---------------------------------------------------------------------------
class MarketBidFlags(BaseModel):
    block_bid_buy: bool = Field(True, description="Enable block bid buy for this market")
    block_bid_sell: bool = Field(True, description="Enable block bid sell for this market")
    single_bid_buy: bool = Field(True, description="Enable single bid buy for this market")
    single_bid_sell: bool = Field(True, description="Enable single bid sell for this market")


class GDAMDAMBidFlags(MarketBidFlags):
    ocf: bool = Field(True, description="Enable OCF for this market")
    premium: bool = Field(True, description="Enable premium for this market")
    discount: bool = Field(True, description="Enable discount for this market")


class BidConfiguration(BaseModel):
    RTM: MarketBidFlags = Field(default_factory=MarketBidFlags)
    GDAM: GDAMDAMBidFlags = Field(default_factory=GDAMDAMBidFlags)
    DAM: GDAMDAMBidFlags = Field(default_factory=GDAMDAMBidFlags)
    HPDAM: MarketBidFlags = Field(default_factory=MarketBidFlags)


class BidConfigurationResponse(BaseModel):
    data: BidConfiguration = Field(..., description="Active bid configuration")
    timestamp: Optional[str] = Field(None, description="Response timestamp")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


class BidConfigurationSaveResponse(BaseModel):
    para_key: str = Field(..., description="Parameter key")
    para_value: BidConfiguration = Field(..., description="Saved normalized config")
    message: str = Field(..., description="Status message")


# ---------------------------------------------------------------------------
# Generic parameter models
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Daily/Weekly markets report charge rates
# ---------------------------------------------------------------------------
class ReportChargeRateField(BaseModel):
    """One rate, with enough context for the Masters screen to render it."""

    para_key: str
    value: float
    description: str
    editable: bool
    source: str = Field(..., description="parameters | default")


class ReportChargeRatesResponse(BaseModel):
    rates: List[ReportChargeRateField]
    fee_rate_basis: str


class ReportChargeRatesSaveRequest(BaseModel):
    reports_fee_rate_per_mwh: Optional[float] = None
    reports_sldc_flat_charge: Optional[float] = None
    reports_fcr_rate_per_mu: Optional[float] = None
    # reports_gst_percent is intentionally absent: it is not editable until PTC
    # confirms whether the per-MWh charge already includes GST. Sending it is
    # rejected by the service rather than silently ignored.
    reports_gst_percent: Optional[float] = None


@router.get(
    "/report-charge-rates",
    response_model=ReportChargeRatesResponse,
    status_code=status.HTTP_200_OK,
    summary="Get daily/weekly report charge rates",
    description=(
        "The four rates the markets report money rows are derived from. "
        "EPMS holds no fee or charge dataset, so these are configuration, not "
        "measured data."
    ),
)
async def get_report_charge_rates(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    try:
        resolved = await parameter_service.get_report_charge_rates()
        value_of = {
            "reports_fee_rate_per_mwh": resolved["fee_rate_per_mwh"],
            "reports_gst_percent": resolved["gst_percent"],
            "reports_sldc_flat_charge": resolved["sldc_flat_charge"],
            "reports_fcr_rate_per_mu": resolved["fcr_rate_per_mu"],
        }
        return ReportChargeRatesResponse(
            rates=[
                ReportChargeRateField(
                    para_key=key,
                    value=value_of[key],
                    description=spec["description"],
                    editable=spec["editable"],
                    source=resolved["sources"].get(key, "default"),
                )
                for key, spec in REPORT_CHARGE_RATE_KEYS.items()
            ],
            fee_rate_basis=resolved["fee_rate_basis"],
        )
    except Exception as e:
        logger.exception("Failed to read report charge rates")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read report charge rates.",
        )


@router.put(
    "/report-charge-rates",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Update daily/weekly report charge rates",
    description=(
        "Updates the editable charge rates. Values are validated and range "
        "checked here rather than through the generic parameter CRUD, because "
        "these multiply every figure on the markets report."
    ),
)
async def save_report_charge_rates(
    request: Request,
    payload: ReportChargeRatesSaveRequest = Body(...),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}

    try:
        result = await parameter_service.save_report_charge_rates(updates)
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            username=username,
            action="report_charge_rates_updated",
            message="User updated the markets report charge rates",
            correlation_id=correlation_id,
            success=True,
            data_type="report_charge_rates",
            updated=result["updated"],
            previous=result["previous"],
        )
        return {"success": True, **result}
    except ValueError as e:
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            username=username,
            action="report_charge_rates_updated",
            message="Charge rate update rejected",
            correlation_id=correlation_id,
            success=False,
            data_type="report_charge_rates",
            failure_reason=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )
    except Exception:
        logger.exception("Failed to save report charge rates")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save report charge rates.",
        )


# ---------------------------------------------------------------------------
# Bid configuration (parameter master) endpoints
# ---------------------------------------------------------------------------
@router.get(
    "/bid-config",
    response_model=BidConfigurationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get active bid configuration",
    description="Returns the currently active bid toggle configuration for all markets"
)
async def get_bid_configuration(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get active bid configuration."""
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await parameter_controller.get_bid_configuration()
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_configuration_view",
            message="Fetched active bid configuration",
            correlation_id=correlation_id,
            success=True
        )
        return {
            "data": result,
            "timestamp": datetime.now().isoformat(),
            "correlation_id": correlation_id
        }
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="BidConfigurationGetError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error fetching bid configuration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch bid configuration"
        )


@router.put(
    "/bid-config",
    response_model=BidConfigurationSaveResponse,
    status_code=status.HTTP_200_OK,
    summary="Save bid configuration",
    description="Save the bid toggle configuration. It becomes active immediately."
)
async def save_bid_configuration(
    request: Request,
    body: BidConfiguration,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Save the active bid configuration."""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        result = await parameter_controller.save_bid_configuration(
            body.dict(),
            user_context
        )
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="bid_configuration_save",
            message="Saved active bid configuration",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        return result
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="BidConfigurationSaveError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error saving bid configuration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save bid configuration"
        )


# ---------------------------------------------------------------------------
# Active market / active exchange for single-bid-table limit values
# ---------------------------------------------------------------------------
class ActiveMarketUpdateRequest(BaseModel):
    markets: Union[str, List[str]] = Field(
        ...,
        description="Active market(s): DAM, GDAM or HPDAM. Accepts a single value or a list."
    )


class ActiveExchangeUpdateRequest(BaseModel):
    exchange: str = Field(..., description="Active exchange: IEX, PXIL or HPX")


class ActiveMarketsResponse(BaseModel):
    active_markets: List[str] = Field(..., description="Currently active markets")
    timestamp: Optional[str] = Field(None, description="Response timestamp")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


class ActiveExchangeResponse(BaseModel):
    active_exchange: str = Field(..., description="Currently active exchange")
    timestamp: Optional[str] = Field(None, description="Response timestamp")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


class ActiveMarketsSaveResponse(BaseModel):
    para_key: str = Field(..., description="Parameter key")
    para_value: List[str] = Field(..., description="Saved active markets")
    message: str = Field(..., description="Status message")


class ActiveExchangeSaveResponse(BaseModel):
    para_key: str = Field(..., description="Parameter key")
    para_value: str = Field(..., description="Saved active exchange")
    message: str = Field(..., description="Status message")


@router.get(
    "/active-markets",
    response_model=ActiveMarketsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get active markets",
    description="Returns the list of markets (DAM/GDAM/HPDAM) currently used by single-bid-table."
)
async def get_active_markets(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        markets = await parameter_controller.get_active_markets()
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="active_markets_view",
            message=f"Fetched active markets: {markets}",
            correlation_id=correlation_id,
            success=True
        )
        return {
            "active_markets": markets,
            "timestamp": datetime.now().isoformat(),
            "correlation_id": correlation_id
        }
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ActiveMarketsGetError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error fetching active markets")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch active markets"
        )


@router.put(
    "/active-markets",
    response_model=ActiveMarketsSaveResponse,
    status_code=status.HTTP_200_OK,
    summary="Set active markets",
    description="Set one or more markets (DAM/GDAM/HPDAM) used by single-bid-table."
)
async def set_active_markets(
    request: Request,
    body: ActiveMarketUpdateRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await parameter_controller.set_active_markets(body.markets)
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="active_markets_save",
            message=f"Saved active markets: {result.get('para_value')}",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ActiveMarketsSaveError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error saving active markets")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save active markets"
        )


@router.get(
    "/active-exchange",
    response_model=ActiveExchangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Get active exchange",
    description="Returns the exchange (IEX/PXIL/HPX) currently used by single-bid-table."
)
async def get_active_exchange(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        exchange = await parameter_controller.get_active_exchange()
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="active_exchange_view",
            message=f"Fetched active exchange: {exchange}",
            correlation_id=correlation_id,
            success=True
        )
        return {
            "active_exchange": exchange,
            "timestamp": datetime.now().isoformat(),
            "correlation_id": correlation_id
        }
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ActiveExchangeGetError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error fetching active exchange")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch active exchange"
        )


@router.put(
    "/active-exchange",
    response_model=ActiveExchangeSaveResponse,
    status_code=status.HTTP_200_OK,
    summary="Set active exchange",
    description="Set the exchange (IEX/PXIL/HPX) used by single-bid-table."
)
async def set_active_exchange(
    request: Request,
    body: ActiveExchangeUpdateRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await parameter_controller.set_active_exchange(body.exchange)
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="active_exchange_save",
            message=f"Saved active exchange: {result.get('para_value')}",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ActiveExchangeSaveError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception("Error saving active exchange")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save active exchange"
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



