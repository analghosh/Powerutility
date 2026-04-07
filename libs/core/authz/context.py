# libs/core/authz/context.py
from __future__ import annotations
from fastapi import Request

class RequestContext:
    def __init__(self, correlation_id: str):
        self.tenant_id = "default"  # Single tenant system
        self.correlation_id = correlation_id

async def request_context(request: Request) -> RequestContext:
    return RequestContext(
        correlation_id = (request.headers.get("X-Correlation-Id") or "auto").strip()
    )
