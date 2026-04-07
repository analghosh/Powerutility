"""
HTTPS Enforcement Middleware
Enforces HTTPS for HMAC-authenticated routes in production
"""

import logging
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from ...core.config import settings

logger = logging.getLogger(__name__)


class HTTPSEnforcementMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce HTTPS for HMAC-authenticated routes"""
    
    def __init__(self, app, require_https: bool = None):
        super().__init__(app)
        self.require_https = require_https if require_https is not None else settings.HMAC_REQUIRE_HTTPS
        logger.info(f"HTTPS enforcement middleware initialized: require_https={self.require_https}")
    
    async def dispatch(self, request: Request, call_next):
        """
        Check if request is using HTTPS (or forwarded as HTTPS)
        
        Args:
            request: FastAPI Request
            call_next: Next middleware/handler
            
        Returns:
            Response
            
        Raises:
            HTTPException: If HTTPS is required but not used
        """
        # Skip HTTPS check if not required
        if not self.require_https:
            return await call_next(request)
        
        # Skip HTTPS check for health checks and docs
        if request.url.path in ["/health", "/docs", "/redoc", "/openapi.json"]:
            return await call_next(request)
        
        # Check if request has HMAC auth headers (only enforce for HMAC routes)
        has_hmac_headers = all([
            request.headers.get("X-API-Key"),
            request.headers.get("X-Signature")
        ])
        
        if not has_hmac_headers:
            # Not an HMAC-authenticated request, skip HTTPS check
            return await call_next(request)
        
        # Check if request is using HTTPS
        is_https = False
        
        # Check X-Forwarded-Proto header (set by reverse proxy)
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "").lower()
        if forwarded_proto == "https":
            is_https = True
        
        # Check if URL scheme is https
        if request.url.scheme == "https":
            is_https = True
        
        # Check if we're in development/local environment
        if settings.ENVIRONMENT in ["local", "development"]:
            # Allow HTTP in local development
            logger.debug(f"Allowing HTTP request in {settings.ENVIRONMENT} environment")
            return await call_next(request)
        
        # Require HTTPS in production
        if not is_https:
            logger.warning(
                f"HTTPS required but request used HTTP: {request.url.path} "
                f"(X-Forwarded-Proto: {forwarded_proto})"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="HTTPS is required for this endpoint"
            )
        
        return await call_next(request)
