"""Audit payload helpers for idf_forecast_switch changes."""

from typing import Any, Dict, Optional

from fastapi import Request


def switch_state_audit_fields(state: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    """Flatten switch dict for audit additional_data with a prefix (previous_ / new_)."""
    return {
        f"{prefix}_use_rtm_api": state.get("use_rtm_api"),
        f"{prefix}_active_source": state.get("active_source"),
        f"{prefix}_para_value": state.get("para_value"),
        f"{prefix}_para_key": state.get("para_key"),
    }


def switch_change_audit_fields(
    previous: Dict[str, Any], new: Dict[str, Any]
) -> Dict[str, Any]:
    """Before/after fields for audit logs."""
    return {
        **switch_state_audit_fields(previous, "previous"),
        **switch_state_audit_fields(new, "new"),
    }


def request_audit_context(request: Request) -> Dict[str, Any]:
    """Client metadata for traceability."""
    client_ip: Optional[str] = None
    if request.client:
        client_ip = request.client.host
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    return {
        "client_ip": client_ip,
        "user_agent": request.headers.get("user-agent"),
        "http_method": request.method,
        "http_path": str(request.url.path),
    }
