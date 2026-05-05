"""
Authentication configuration
"""

import os
from typing import Optional


class AuthConfig:
    """Authentication configuration settings"""
    
    def __init__(self, jwt_secret_key: str = None, jwt_algorithm: str = None):
        # JWT Settings - can be overridden by service-specific config
        self.JWT_SECRET_KEY = jwt_secret_key or os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
        self.JWT_ALGORITHM = jwt_algorithm or os.getenv("JWT_ALGORITHM", "HS256")
        self.JWT_EXPIRATION_DELTA = int(os.getenv("JWT_EXPIRATION_DELTA", "3600"))  # 1 hour
        
        # Auth Service Settings
        self.AUTH_SERVICE_HOST = os.getenv("AUTH_SERVICE_HOST", "localhost")
        self.AUTH_SERVICE_PORT = int(os.getenv("AUTH_SERVICE_PORT", "8001"))
        self.AUTH_SERVICE_TIMEOUT = float(os.getenv("AUTH_SERVICE_TIMEOUT", "30.0"))
        
        # Middleware Settings
        self.REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true").lower() == "true"
        self.CORRELATION_HEADER_NAME = os.getenv("CORRELATION_HEADER_NAME", "X-Correlation-ID")
        
        # Excluded paths (comma-separated)
        excluded_paths_str = os.getenv("AUTH_EXCLUDED_PATHS", "/health,/docs,/redoc,/openapi.json")
        self.EXCLUDED_PATHS = [path.strip() for path in excluded_paths_str.split(",") if path.strip()]
    
    @property
    def auth_service_base_url(self) -> str:
        """Get auth service base URL"""
        return f"http://{self.AUTH_SERVICE_HOST}:{self.AUTH_SERVICE_PORT}/api/v1"


# Global auth config instance - will be initialized by services
auth_config = None

def get_auth_config(jwt_secret_key: str = None, jwt_algorithm: str = None) -> AuthConfig:
    """Get or create auth config instance"""
    global auth_config
    if auth_config is None:
        auth_config = AuthConfig(jwt_secret_key, jwt_algorithm)
    return auth_config
