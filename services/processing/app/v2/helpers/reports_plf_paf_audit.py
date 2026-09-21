"""
Audit logging helpers for reports-plf-paf API access tracking.
"""

from typing import Any, Dict, Optional

from fastapi import Request

from libs.audit_queue.simple_logger import AuditLogger

REPORTS_PLF_PAF_AUDIT_ACTIONS = (
    "reports_plf_paf_access_attempt",
    "reports_plf_paf_view",
    "reports_plf_paf_download",
)

REPORTS_PLF_PAF_DATA_TYPE = "reports_plf_paf"


def extract_reports_plf_paf_request_meta(request: Request) -> Dict[str, Optional[str]]:
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


def build_reports_plf_paf_query_context(
    *,
    endpoint: str,
    schedule_date: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    from_block: Optional[int] = None,
    to_block: Optional[int] = None,
    response_format: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "data_type": REPORTS_PLF_PAF_DATA_TYPE,
        "endpoint": endpoint,
        "schedule_date": schedule_date,
        "from_date": from_date,
        "to_date": to_date,
        "from_block": from_block,
        "to_block": to_block,
        "response_format": response_format,
    }


def log_reports_plf_paf_access_attempt(
    audit_logger: AuditLogger,
    *,
    user_id: str,
    username: str,
    correlation_id: str,
    endpoint: str,
    request: Request,
    schedule_date: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    from_block: Optional[int] = None,
    to_block: Optional[int] = None,
    response_format: Optional[str] = None,
) -> str:
    """Record that a user initiated a reports-plf-paf request (before outcome)."""
    meta = extract_reports_plf_paf_request_meta(request)
    ip_address = meta.pop("ip_address", None)
    return audit_logger.log_user_action(
        service="processing",
        user_id=user_id,
        action="reports_plf_paf_access_attempt",
        message=f"User attempted to access PLF/PAF report ({endpoint})",
        username=username,
        ip_address=ip_address,
        correlation_id=correlation_id,
        success=True,
        **build_reports_plf_paf_query_context(
            endpoint=endpoint,
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            response_format=response_format,
        ),
        **meta,
    )


def log_reports_plf_paf_outcome(
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
    schedule_date: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    from_block: Optional[int] = None,
    to_block: Optional[int] = None,
    response_format: Optional[str] = None,
    failure_reason: Optional[str] = None,
    **extra: Any,
) -> str:
    """Record success or failure after the reports-plf-paf operation completes."""
    meta = extract_reports_plf_paf_request_meta(request)
    ip_address = meta.pop("ip_address", None)
    payload: Dict[str, Any] = {
        **build_reports_plf_paf_query_context(
            endpoint=endpoint,
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
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
