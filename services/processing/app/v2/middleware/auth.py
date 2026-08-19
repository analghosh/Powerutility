"""
V2 Authentication middleware wrapper
"""

import logging
from typing import Optional, List, Dict, Any
from fastapi import Request
from libs.middleware.auth_middleware import AuthMiddleware
from libs.auth_client import get_auth_client

logger = logging.getLogger(__name__)


class V2AuthMiddleware(AuthMiddleware):
    """
    V2-specific authentication middleware with real JWT validation
    """
    
    def __init__(self, app, exclude_paths: Optional[List[str]] = None):
        # V2 specific excluded paths (only for docs and health) (both relative and absolute paths)
        v2_exclude_paths = [
            "/health",  # V2 health endpoint
            "/docs",    # Swagger UI
            "/redoc",   # ReDoc UI
            "/openapi.json",  # OpenAPI schema
            "/favicon.ico",   # Browser favicon requests
            "/static",   # Static files for docs
            # Also include the mounted paths (when accessed via main app)
            "/api/v2/health",
            "/api/v2/docs",
            "/api/v2/redoc", 
            "/api/v2/openapi.json",
            "/api/v2/favicon.ico",
            "/api/v2/static",
            # Exclude power optimization path - it uses HMAC authentication instead of JWT
            "/power-optimization",
            "/api/v2/power-optimization"
        ]
        
        if exclude_paths:
            v2_exclude_paths.extend(exclude_paths)
        
        # Get auth service URL from config
        from ...core.config import settings
        
        # Initialize with real auth service validation
        # Create a dedicated auth client instance for V2
        from libs.auth_client import AuthClient
        auth_client = AuthClient(settings.AUTH_SERVICE_URL)
        
        super().__init__(
            settings=settings,
            app=app,
            auth_client=auth_client,
            exclude_paths=v2_exclude_paths,
            require_auth=True  # V2 always requires authentication
        )
        
        logger.info(f"V2AuthMiddleware initialized with auth service: {settings.AUTH_SERVICE_URL}")
        logger.info(f"V2AuthMiddleware excluded paths: {v2_exclude_paths}")
    
    def _should_skip_auth(self, path: str) -> bool:
        """
        Override to handle both mounted and direct paths for V2
        """
        logger.info(f"🔍 V2 AUTH SKIP - Path: '{path}' | Excludes: {self.exclude_paths}")
        
        # Check direct exclusions first
        for exclude_path in self.exclude_paths:
            if path.startswith(exclude_path):
                logger.info(f"🔍 V2 AUTH SKIP - MATCH! Skipping auth for '{path}' (matches '{exclude_path}')")
                return True
        
        # For mounted apps, also check if the path contains our relative paths
        relative_paths = ["/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico", "/power-optimization"]
        for rel_path in relative_paths:
            if path.startswith(rel_path) or rel_path in path:
                logger.info(f"🔍 V2 AUTH SKIP - MATCH! Skipping auth for '{path}' (contains '{rel_path}')")
                return True
        
        logger.info(f"🔍 V2 AUTH SKIP - NO MATCH! Requiring auth for '{path}'")
        return False