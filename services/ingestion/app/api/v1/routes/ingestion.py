"""
Ingestion API routes
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks
from typing import Dict, Any
from pydantic import BaseModel
from datetime import datetime
from libs.audit_queue.simple_logger import AuditLogger
from ....core.config import settings
from ....core.dependencies import get_audit_logger
from ....middleware.page_access import require_page_access

router = APIRouter(prefix="/ingest", tags=["Data Ingestion"])
audit_logger = get_audit_logger()
logger = logging.getLogger("ingestion_routes")


def _scheduler_base_url() -> str:
    """Return scheme+host+port of the scheduler service (strips any path suffix)."""
    from urllib.parse import urlparse
    parsed = urlparse(settings.SCHEDULER_SERVICE_URL)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _trigger_scheduler_demand_pipeline() -> Dict[str, Any]:
    """
    Call the scheduler's demand-trigger endpoint to run demand_temp_sync then
    demand_processor. Failures here do not fail the ingestion (which already
    published to RabbitMQ); the outcome is reported back in the response.
    """
    url = f"{_scheduler_base_url()}/api/v1/scheduler/demand-trigger/trigger"
    timeout = getattr(settings, 'SCHEDULER_DEMAND_TRIGGER_TIMEOUT', 300)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url)
            resp.raise_for_status()
            return {"success": True, "detail": resp.json()}
    except Exception as e:
        logger.error("Failed to trigger scheduler demand pipeline at %s: %s", url, str(e))
        return {"success": False, "error": str(e)}


async def _run_manual_supply_demand_pipeline() -> None:
    """
    Background worker: run the supply/demand/rtm-optimization ingestion, then
    chain the scheduler demand pipeline. Runs detached from the HTTP request so
    the caller gets an immediate "triggered" response instead of timing out.
    """
    from ....processors.api_raw_data_processor import APIRawDataProcessor
    try:
        processor = APIRawDataProcessor(audit_logger)
        result = await processor.process_supply_demand_api_data()
        logger.info("Manual supply/demand ingestion finished with status=%s", result.get('status'))

        if result.get('status') in ('success', 'partial_success'):
            sched = await _trigger_scheduler_demand_pipeline()
            logger.info("Scheduler demand pipeline triggered (success=%s)", sched.get('success'))
        else:
            logger.warning("Skipping scheduler trigger; ingestion status=%s", result.get('status'))
    except Exception as e:
        logger.error("Manual supply/demand pipeline failed in background: %s", str(e), exc_info=True)


class DataIngestionRequest(BaseModel):
    """Data ingestion request model"""
    data: Dict[str, Any]
    source: str = "manual"
    priority: str = "normal"


class IngestionResponse(BaseModel):
    """Data ingestion response model"""
    message: str
    ingestion_id: str
    timestamp: str


class ValidationRequest(BaseModel):
    """Data validation request model"""
    data: Dict[str, Any]
    validation_rules: list[str] = []


class ValidationResponse(BaseModel):
    """Data validation response model"""
    valid: bool
    validation_errors: list[str]


class StatusResponse(BaseModel):
    """Ingestion status response model"""
    status: str
    last_ingestion: str
    total_records: int


@router.post("/data", response_model=IngestionResponse)
async def ingest_data(
    _request: DataIngestionRequest,
    _current_user: Dict[str, Any] = Depends(require_page_access("Data Ingestion"))
):
    """
    Ingest data endpoint
    
    Returns:
        Ingestion confirmation with ID
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        # TODO: Implement actual data ingestion logic
        ingestion_id = f"ingest_{datetime.now().timestamp()}"
        
        # Log successful data ingestion activity
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="data_ingestion",
            message=f"Data ingestion successful: {_request.source}",
            session_id=session_id,
            ingestion_id=ingestion_id,
            source=_request.source,
            priority=_request.priority,
            data_size=len(str(_request.data)),
            endpoint="/ingest/data"
        )
        
        return IngestionResponse(
            message="Data ingested successfully",
            ingestion_id=ingestion_id,
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        # Log failed data ingestion activity
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="data_ingestion_failed",
            message=f"Data ingestion failed: {str(e)}",
            session_id=session_id,
            success=False,
            source=_request.source,
            priority=_request.priority,
            endpoint="/ingest/data",
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Data ingestion failed: {str(e)}"
        ) from e


@router.post("/validate", response_model=ValidationResponse)
async def validate_data(
    _request: ValidationRequest,
    _current_user: Dict[str, Any] = Depends(require_page_access("Data Ingestion"))
):
    """
    Validate data endpoint
    
    Returns:
        Validation result
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        # TODO: Implement actual validation logic
        validation_errors = []
        
        # Basic validation example
        if "timestamp" not in _request.data:
            validation_errors.append("Missing timestamp field")
        
        if "value" not in _request.data:
            validation_errors.append("Missing value field")
        
        is_valid = len(validation_errors) == 0
        
        # Log data validation activity
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="data_validation",
            message=f"Data validation completed: {len(validation_errors)} errors found",
            session_id=session_id,
            success=is_valid,
            validation_rules_count=len(_request.validation_rules),
            validation_errors_count=len(validation_errors),
            is_valid=is_valid,
            data_fields=list(_request.data.keys()),
            endpoint="/ingest/validate"
        )
        
        return ValidationResponse(
            valid=is_valid,
            validation_errors=validation_errors
        )
    except Exception as e:
        # Log failed data validation activity
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="data_validation_failed",
            message=f"Data validation failed: {str(e)}",
            session_id=session_id,
            success=False,
            endpoint="/ingest/validate",
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Data validation failed: {str(e)}"
        ) from e


@router.post("/trigger-supply-demand", status_code=status.HTTP_202_ACCEPTED)
async def trigger_supply_demand_ingestion(
    background_tasks: BackgroundTasks,
    _current_user: Dict[str, Any] = Depends(require_page_access("Data Ingestion"))
):
    """
    Trigger the full manual demand/supply pipeline and return immediately.

    The endpoint only *kicks off* the work and responds right away (202) so the
    frontend never waits/timeouts. In the background it:
      1. Runs the api_raw_data_processor pipeline for SUPPLY, DEMAND and the RTM
         optimization provider, storing raw data with metadata.manual = true.
      2. Publishes job triggers to RabbitMQ for the downstream consumers.
      3. Chains a call to the scheduler's demand-trigger endpoint, which runs
         demand_temp_sync then demand_processor.

    Returns 409 Conflict only if the api cron is already running (cron handles
    the update — no need to duplicate).
    """
    from ....processors import api_raw_data_processor as _api_proc_module

    # Fast pre-check (instant flag read) so the caller still gets a 409 when the
    # cron is mid-run, without waiting for any processing.
    if _api_proc_module._api_cron_running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="API cron job is currently running. Please try again after it completes."
        )

    # Fire-and-forget: do the heavy ingestion + scheduler chain in the background.
    background_tasks.add_task(_run_manual_supply_demand_pipeline)

    return {
        "status": "triggered",
        "message": "Manual supply/demand pipeline triggered. Ingestion and scheduler "
                   "processing are running in the background."
    }


@router.get("/status", response_model=StatusResponse)
async def get_ingestion_status(
    _current_user: Dict[str, Any] = Depends(require_page_access("Data Ingestion"))
):
    """
    Get ingestion status endpoint
    
    Returns:
        Current ingestion status
    """
    user_id = _current_user.get("user_id", "unknown")
    session_id = _current_user.get("session_id", "unknown")
    
    try:
        # TODO: Implement actual status logic
        status_result = StatusResponse(
            status="active",
            last_ingestion=datetime.now().isoformat(),
            total_records=0
        )
        
        # Log status check activity
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="status_check",
            message="Ingestion status checked",
            session_id=session_id,
            ingestion_status=status_result.status,
            total_records=status_result.total_records,
            endpoint="/ingest/status"
        )
        
        return status_result
    except Exception as e:
        # Log failed status check activity
        audit_logger.log_user_action(
            service="ingestion",
            user_id=user_id,
            action="status_check_failed",
            message=f"Status check failed: {str(e)}",
            session_id=session_id,
            success=False,
            endpoint="/ingest/status",
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get ingestion status"
        ) from e 