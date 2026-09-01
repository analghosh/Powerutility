"""
Audit logging helpers for daily-markets report access tracking.
"""

from typing import Any, Dict, Optional

from fastapi import Request

from libs.audit_queue.simple_logger import AuditLogger

DAILY_MARKETS_AUDIT_ACTIONS = (
    "reports_daily_markets_access_attempt",
    "reports_daily_markets_view",
    "reports_daily_markets_download",
)

DAILY_MARKETS_DATA_TYPE = "reports_daily_markets"


def extract_daily_markets_request_meta(request: Request) -> Dict[str, Optional[str]]:
    """Client IP and user agent for audit correlation."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else None
    return {
        "ip_address": ip,
        "user_agent": request.headers.get("User-Agent"),
        "request_path": str(request.url.path),
        "request_method": request.method,
    }


def build_daily_markets_query_context(
    *,
    endpoint: str,
    report_date: Optional[str] = None,
    market: Optional[str] = None,
    exchange: Optional[str] = None,
    response_format: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "data_type": DAILY_MARKETS_DATA_TYPE,
        "endpoint": endpoint,
        "report_date": report_date,
        "market": market,
        "exchange": exchange,
        "response_format": response_format,
    }


def log_daily_markets_access_attempt(
    audit_logger: AuditLogger,
    *,
    user_id: str,
    username: str,
    correlation_id: str,
    endpoint: str,
    request: Request,
    report_date: Optional[str] = None,
    market: Optional[str] = None,
    exchange: Optional[str] = None,
    response_format: Optional[str] = None,
) -> str:
    """Record that a user initiated a daily-markets request (before outcome)."""
    meta = extract_daily_markets_request_meta(request)
    ip_address = meta.pop("ip_address", None)
    return audit_logger.log_user_action(
        service="processing",
        user_id=user_id,
        action="reports_daily_markets_access_attempt",
        message=f"User attempted to access the daily markets report ({endpoint})",
        username=username,
        ip_address=ip_address,
        correlation_id=correlation_id,
        success=True,
        **build_daily_markets_query_context(
            endpoint=endpoint,
            report_date=report_date,
            market=market,
            exchange=exchange,
            response_format=response_format,
        ),
        **meta,
    )


def log_daily_markets_outcome(
    audit_logger: AuditLogger,
    *,
    action: str,
    user_id: str,
    username: str,
    correlation_id: str,
    endpoint: str,
    request: Request,
    success: bool,
    message: str,
    report_date: Optional[str] = None,
    market: Optional[str] = None,
    exchange: Optional[str] = None,
    response_format: Optional[str] = None,
    failure_reason: Optional[str] = None,
    **extra: Any,
) -> str:
    """Record success or failure after the daily-markets operation completes."""
    meta = extract_daily_markets_request_meta(request)
    ip_address = meta.pop("ip_address", None)
    payload: Dict[str, Any] = {
        **build_daily_markets_query_context(
            endpoint=endpoint,
            report_date=report_date,
            market=market,
            exchange=exchange,
            response_format=response_format,
        ),
        **meta,
        **extra,
    }
    if failure_reason:
        payload["failure_reason"] = failure_reason

    return audit_logger.log_user_action(
        service="processing",
        user_id=user_id,
        action=action,
        message=message,
        username=username,
        ip_address=ip_address,
        correlation_id=correlation_id,
        success=success,
        **payload,
    )
