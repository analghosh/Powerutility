from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any, List

from ...dependencies import get_user_context
from ....core.dependencies import get_audit_logger
from libs.audit_queue.simple_logger import AuditLogger
from ...controller.exchange_config_controller import exchange_config_controller
import logging

router = APIRouter(
    prefix="/exchange-config",
    tags=["Exchange Config"]
)

logger = logging.getLogger(__name__)


@router.get(
    "/",
    summary="Get active exchange configs",
    response_description="List of active exchange configurations"
)
async def get_active_exchange_configs(
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> List[Dict[str, Any]]:
    return await exchange_config_controller.get_active_list()


@router.put(
    "/{id}/switch",
    summary="Switch active environment (UAT ↔ PROD)",
    response_description="Switch result showing which environment was activated"
)
async def switch_exchange_environment(
    id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> Dict[str, Any]:
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="exchange_config_switch",
            message=f"Switching exchange environment for config id {id}",
            correlation_id=correlation_id,
            user_id=user_id,
            record_id=id
        )

        result = await exchange_config_controller.switch_environment(id)

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="exchange_config_switch",
            message=f"Switched exchange config {id} from {result['switched_from']} to {result['switched_to']}",
            username=username,
            correlation_id=correlation_id,
            record_id=id,
            success=True
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="ExchangeConfigSwitchError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        logger.exception(f"Error switching exchange environment for config id {id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to switch exchange environment"
        )
