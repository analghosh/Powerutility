"""
Configuration settings for Auth Service
"""

import os
from typing import List, Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings"""
    
    # Service Configuration
    SERVICE_NAME: str = "auth-service"
    SERVICE_PORT: int = 8001
    SERVICE_HOST: str = "0.0.0.0"
    HOST: str = "0.0.0.0"  # Alias for SERVICE_HOST
    PORT: str = "8001"  # Alias for SERVICE_PORT
    DEBUG: bool = False  # Default to False for security; set to True explicitly for development
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "colored"
    
    # Environment Configuration
    ENVIRONMENT: Optional[str] = None  # Auto-detected if not set in .env
    
    # Database Configuration

    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_URL: str = "mongodb://localhost:27017"  # Alias for MONGODB_URI
    AUTH_DB_NAME: str = "dev_auth_db"
    AUDIT_DB_NAME: str = "dev_audit_db"
    MONGODB_AUTH_COLLECTION: str = "users"
    MONGODB_SESSIONS_COLLECTION: str = "sessions"
    MONGODB_QUEUE_COLLECTION: str = "audit_queue_jobs"
    MONGODB_AUDIT_COLLECTION: str = "audit_logs"
    MONGODB_ROLE_COLLECTION: str = "roles"
    MONGODB_PERMISSION_COLLECTION: str = "permissions"
    
    
    # Redis Configuration
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    REDIS_URL: str = "redis://localhost:6379/0"  # Alias for Redis connection
    
    # Redis Blacklist Configuration
    REDIS_BLACKLIST_ENABLED: bool = True
    REDIS_BLACKLIST_DB: int = 1
    REDIS_BLACKLIST_TTL: int = 7  # Days (same as refresh token expiry)
    
    # JWT Configuration - must be set via env var; no insecure fallback
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ACCESS_TOKEN_EXPIRE_MINUTES: str = "30"  # Alias for JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: float = 7
    
    # Security Configuration - must be set via env var; no insecure fallback
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    BCRYPT_ROUNDS: int = 12
    PASSWORD_MIN_LENGTH: int = 8
    PASSWORD_MAX_LENGTH: int = 128
    
    # Password Policy Configuration
    MIN_PASSWORD_LENGTH: int = 8
    REQUIRE_UPPERCASE: bool = True
    REQUIRE_LOWERCASE: bool = True
    REQUIRE_NUMBERS: bool = True
    REQUIRE_SPECIAL_CHARS: bool = True
    PASSWORD_EXPIRY_DAYS: int = 90
    PASSWORD_HISTORY_COUNT: int = 5
    FIRST_LOGIN_PASSWORD_EXPIRY_HOURS: int = 24
    
    # Account Lockout Configuration
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_DURATION_MINUTES: int = 20
    LOCKOUT_WINDOW_HOURS: int = 24
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 3600
    
    # MFA Configuration
    MFA_ENABLED: bool = True
    MFA_ISSUER: str = "EPMS"  # Can be overridden by APP_NAME env var
    MFA_ALGORITHM: str = "SHA1"
    MFA_DIGITS: int = 6
    MFA_PERIOD: int = 30
    MFA_WINDOW: int = 1  # Time window buffer for TOTP validation (±30 seconds)
    
    # App Configuration
    APP_NAME: str = "EPMS"  # Used as default MFA issuer name
    
    # Session Configuration
    SESSION_TIMEOUT_HOURS: int = 24
    MAX_SESSIONS_PER_USER: int = 5
    
    # Proxy Configuration
    # Comma-separated list of trusted reverse-proxy IPs (e.g. "127.0.0.1,10.0.0.1").
    # When set, X-Forwarded-For / X-Real-IP are only honoured for requests whose
    # direct socket peer matches one of these IPs.  Leave empty in local dev.
    TRUSTED_PROXIES: str = os.getenv("TRUSTED_PROXIES", "127.0.0.1")

    # CORS Configuration
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:8080",
        "http://localhost:8002",  # Scheduler service (dashboard)
        "http://localhost:8003",  # Processing service
        "http://localhost:8004",  # Ingestion service
        "http://127.0.0.1:8002",  # Alternative localhost
        "http://127.0.0.1:8003",
        "http://127.0.0.1:8004",
        "https://api-dev.infiscopelabs.com"  # Production domain
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    
    # Health Check
    HEALTH_CHECK_ENABLED: bool = True
    HEALTH_CHECK_INTERVAL_SECONDS: int = 30
    
    # Default Admin Configuration - must be set via env var; no insecure fallback
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "admin@epms.com")
    ADMIN_INITIAL_PASSWORD: str = os.getenv("ADMIN_INITIAL_PASSWORD", "")
    
    @model_validator(mode='after')
    def set_environment_config(self):
        """Set environment-aware configurations after validation"""
        if self.ENVIRONMENT is None:
            # Use proper environment detection with defaults
            docker_container = os.getenv("DOCKER_CONTAINER", "").lower() in ("true", "1", "yes")
            server_deployment = os.getenv("SERVER_DEPLOYMENT", "").lower() in ("true", "1", "yes")
            nginx_proxy = os.getenv("NGINX_PROXY") is not None
            
            if docker_container:
                self.ENVIRONMENT = "docker"
            elif server_deployment or nginx_proxy:
                self.ENVIRONMENT = "server"
            else:
                self.ENVIRONMENT = "local"
        
        return self
    
    def check_security_warnings(self):
        """Validate security configuration and fail startup if insecure.
        Call this at application startup (auth service only).
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # Check JWT_SECRET_KEY
        if not self.JWT_SECRET_KEY:
            raise ValueError(
                "SECURITY ERROR: JWT_SECRET_KEY must be set via environment variable. "
                "Set JWT_SECRET_KEY to a secure random string (>=32 chars)."
            )
        
        if len(self.JWT_SECRET_KEY) < 32:
            raise ValueError(
                f"SECURITY ERROR: JWT_SECRET_KEY must be at least 32 characters long. "
                f"Current length: {len(self.JWT_SECRET_KEY)}"
            )
        
        if "your-super-secret-jwt-key-change-in-production" in self.JWT_SECRET_KEY:
            raise ValueError(
                "SECURITY ERROR: JWT_SECRET_KEY contains insecure default value. "
                "Set a secure random string via JWT_SECRET_KEY environment variable."
            )
        
        # Check SECRET_KEY
        if not self.SECRET_KEY:
            raise ValueError(
                "SECURITY ERROR: SECRET_KEY must be set via environment variable. "
                "Set SECRET_KEY to a secure random string (>=32 chars)."
            )
        
        if len(self.SECRET_KEY) < 32:
            raise ValueError(
                f"SECURITY ERROR: SECRET_KEY must be at least 32 characters long. "
                f"Current length: {len(self.SECRET_KEY)}"
            )
        
        if "your-super-secret-key-change-in-production" in self.SECRET_KEY:
            raise ValueError(
                "SECURITY ERROR: SECRET_KEY contains insecure default value. "
                "Set a secure random string via SECRET_KEY environment variable."
            )
        
        # Check ADMIN_INITIAL_PASSWORD
        if not self.ADMIN_INITIAL_PASSWORD:
            raise ValueError(
                "SECURITY ERROR: ADMIN_INITIAL_PASSWORD must be set via environment variable. "
                "Set ADMIN_INITIAL_PASSWORD before first startup."
            )
        
        if len(self.ADMIN_INITIAL_PASSWORD) < 8:
            raise ValueError(
                f"SECURITY ERROR: ADMIN_INITIAL_PASSWORD must be at least 8 characters long. "
                f"Current length: {len(self.ADMIN_INITIAL_PASSWORD)}"
            )
        
        if "Infi@7983" in self.ADMIN_INITIAL_PASSWORD:
            raise ValueError(
                "SECURITY ERROR: ADMIN_INITIAL_PASSWORD contains insecure default value. "
                "Set a secure password via ADMIN_INITIAL_PASSWORD environment variable."
            )
        
        logger.info("Security configuration validation passed")
    
    @property
    def BASE_PATH(self) -> str:
        """Get base path for current environment (computed dynamically)"""
        return "/auth" if self.ENVIRONMENT in ["server", "docker"] else ""

    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Ignore extra fields from environment
        json_schema_extra = {
            "example": {
                "SERVICE_NAME": "auth-service",
                "SERVICE_PORT": 8001,
                "JWT_SECRET_KEY": "<set-via-env-var>",
                "MONGODB_URI": "mongodb://localhost:27017",
                "ADMIN_USERNAME": "admin",
                "ADMIN_EMAIL": "admin@epms.com",
                "ADMIN_INITIAL_PASSWORD": "<set-via-env-var>"
            }
        }


# Create settings instance
settings = Settings() 
