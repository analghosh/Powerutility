"""
EPMS Auth Service - Main Application
"""

# Auto-install libs package if not available
try:
    import libs.core.cron_manager
except ImportError:
    import sys
    import subprocess
    from pathlib import Path
    print("📦 Installing dependencies...")
    project_root = Path(__file__).parent.parent.parent.parent
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-e", str(project_root)
    ])
    print("✅ Dependencies installed")

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request

import time
import logging
from datetime import datetime

from pydantic import BaseModel

from .core.config import settings
from .core.seeder import DatabaseSeeder
from .services import PasswordPolicyService

# Configure logging with correlation ID support
from libs.middleware import configure_logging, LoggingMiddleware, CorrelationMiddleware, CacheMiddleware

# Configure logging based on settings
configure_logging(
    level=getattr(settings, 'LOG_LEVEL', 'INFO'),
    format_type=getattr(settings, 'LOG_FORMAT', 'colored'),
    include_correlation=True
)

# Lifespan context manager for startup/shutdown events
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    # Create database indexes for performance (only once)
    from .core.database_indexes import create_database_indexes
    try:
        create_database_indexes()
    except Exception as e:
        logger.warning(f"Failed to create database indexes: {e}")
    
    # Run database seeders on startup - only if needed and limit operations
    try:
        seeder = DatabaseSeeder()
        # Quick check if admin user exists (lightweight check)
        admin_user = seeder.user_repository.get_user_by_username(settings.ADMIN_USERNAME)
        if not admin_user:
            logger.info("Admin user not found, running minimal seeding...")
            seeder.seed_roles()
            seeder.ensure_admin_user()
        else:
            logger.info("Admin user exists, skipping heavy seeding operations")
    except Exception as e:
        logger.error(f"Error during startup seeding: {e}")
        # Don't fail startup if seeding fails
    
    # Initialize centralized RBAC permissions catalog
    from libs.core.authz.permissions_catalog import ensure_permissions_catalog
    from libs.core.dependencies import get_db
    
    try:
        db = get_db()
        await ensure_permissions_catalog(db)
        logger.info("✅ Centralized RBAC permissions catalog initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize permissions catalog: {e}")
    
    yield
    
    # Cleanup on shutdown
    from .core.database import close_database_connections
    from .core.dependencies import cleanup_audit_resources
    try:
        # Close audit logging resources first
        cleanup_audit_resources()
        
        # Close database connections
        close_database_connections()
        
        logger.info("🔒 Application shutdown cleanup completed")
    except Exception as e:
        logger.error(f"❌ Error during shutdown cleanup: {e}")

# Create FastAPI app
app = FastAPI(
    title="EPMS Auth Service",
    description="""
    ## EPMS Authentication Service
    
    This service handles user authentication, JWT token management, and **menu-based authorization** services.
    
    ### Features:
    - **User Authentication**: Login/logout functionality with JWT tokens
    - **Multi-Factor Authentication (MFA)**: TOTP-based two-factor authentication
    - **Menu-Based RBAC**: Role-Based Access Control with menu permissions
    - **Token Validation**: JWT token validation for other services
    - **User Information**: Safe user data retrieval for service-to-service communication
    - **Password Policy**: Enforced password policies and account security
    
    ### 🎯 Menu-Based RBAC Workflow:
    
    **IMPORTANT**: Permissions flow from **Menu+Permissions → Role → User**
    
    #### 1. Create Roles:
    ```
    POST /api/v1/roles
    Body: { "name": "Manager", "description": "Management role" }
    → Role created with empty permissions initially
    ```
    
    #### 2. Assign Menu+Permissions to Roles (AUTO-SYNCS role permissions):
    ```
    POST /api/v1/menu-roles/roles/{role_id}/menus
    Body: { "menu_id": "dashboard_menu_id", "permissions": ["read", "write"] }
    → Role permissions automatically updated to include "dashboard:read", "dashboard:write"
    ```
    
    #### 3. Assign Roles to Users:
    ```
    PUT /api/v1/users/{user_id}/roles
    Body: { "roles": ["manager"] }
    → User automatically gets all permissions from assigned roles
    ```
    
    #### 4. Users Automatically Get Menu Access:
    ```
    GET /api/v1/users/{user_id}/accessible-menus → User's accessible menus with permissions
    GET /api/v1/menu/ → Current user's personalized menu tree
    GET /api/v1/roles/{role_id}/menus → See which menus are assigned to a role
    ```
    
    **Key Points:**
    - ✅ Role permissions are **automatically calculated** from menu assignments
    - ✅ User permissions = **sum of all their role permissions**
    - ❌ No direct permission assignment to users or roles
    
    ### Authentication Flows:
    
    #### Traditional Login (Non-MFA Users):
    ```
    POST /auth/login → LoginResponse (immediate tokens)
    ```
    
    #### MFA-Enhanced Login (MFA Users):
    ```
    POST /auth/login → TemporaryTokenResponse
    POST /auth/verify-login → LoginResponse (final tokens)
    ```
    
    ### MFA Setup Process:
    ```
    1. POST /mfa/enable → Get QR code and secret (access token only!)
    2. Scan QR code with authenticator app
    3. POST /mfa/verify → Complete MFA setup
    4. Future logins require TOTP codes
    ```
    
    ### 🔒 IMPROVED MFA Security:
    - **Enable MFA**: Access token only (no password needed)
    - **Disable MFA**: Access token + TOTP code (no password needed) 
    - **Verify MFA**: Access token + TOTP code
    - **Reduced password exposure** for better security
    
    ### Authentication:
    Most endpoints require authentication using JWT Bearer tokens.
    
    ### Service-to-Service Communication:
    The Auth Service provides endpoints for other services to validate tokens and retrieve user information.
    """,
    version="1.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/auth_api.json",
    lifespan=lifespan,
    tags=[
        {
            "name": "Authentication",
            "description": "🔐 Core authentication operations including login, logout, and token management. Supports both traditional and MFA-enhanced flows."
        },
        {
            "name": "Multi-Factor Authentication",
            "description": "🛡️ TOTP-based two-factor authentication setup, verification, and management. No password required for setup - uses access tokens."
        },
        {
            "name": "User Management",
            "description": "👤 User CRUD operations, role assignments, and user access management"
        },
        {
            "name": "Role Management",
            "description": "👑 Role creation and management. Role permissions are automatically synced from menu assignments."
        },
        {
            "name": "Menu-Role Management",
            "description": "🎯 **CORE WORKFLOW!** Assign menus with permissions to roles. This automatically updates role permissions and flows to users."
        },
        {
            "name": "Menu Management",
            "description": "📋 Menu tree management and user-accessible menu retrieval"
        },

        {
            "name": "Health",
            "description": "❤️ Health check and service status endpoints"
        }
    ],
    root_path=settings.BASE_PATH
)

# Configure security schemes for Swagger UI
app.openapi_schema = None  # Reset to regenerate with security

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    from fastapi.openapi.utils import get_openapi
    
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # Add server URLs with base path for proper OpenAPI documentation
    base_path = settings.BASE_PATH
    environment = settings.ENVIRONMENT
    
    servers = []
    
    # For server environments, add the full server URL with base path
    if base_path:
        servers.append({
            "url": f"{base_path}/api/v1", 
            "description": f"Server environment ({environment}) - Use this for server deployment"
        })
    
    # Always add local development URL
    servers.append({
        "url": "/api/v1", 
        "description": "Local development - Use this for local testing"
    })
    
    openapi_schema["servers"] = servers
    
    # Add security schemes
    openapi_schema["components"]["securitySchemes"] = {
        "HTTPBearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter your JWT token (without 'Bearer' prefix)"
        }
    }
    
    # Add security to all protected endpoints
    protected_paths = [
        "/api/v1/auth/me",
        "/api/v1/auth/logout", 
        "/api/v1/auth/change-password",
        "/api/v1/mfa/enable",
        "/api/v1/mfa/verify",
        "/api/v1/mfa/disable",
        "/api/v1/mfa/recover",
        "/api/v1/mfa/status",
        "/api/v1/mfa/admin/emergency-disable/{target_user_id}",
        "/api/v1/mfa/admin/reset-rate-limit/{target_user_id}",
        "/api/v1/users/",
        "/api/v1/users/{user_id}",
        "/api/v1/users/{user_id}/roles",
        "/api/v1/users/{user_id}/accessible-menus",
        "/api/v1/roles/",
        "/api/v1/roles/{role_id}",
        "/api/v1/menu/",
        "/api/v1/menu/list",
        "/api/v1/menu-roles/roles/{role_id}/menus",
        "/api/v1/menu-roles/roles/{role_id}/menus/bulk",
        "/api/v1/menu-roles/roles/{role_id}/menus/{menu_id}",
        "/api/v1/menu-roles/menus/{menu_id}/roles",
        "/api/v1/menu-roles/summary",
        "/api/v1/auth/user/{user_id}",
        "/api/v1/auth/validate-token",
        "/api/v1/auth/roles",
        "/api/v1/auth/permissions",
        "/api/v1/auth/validate-permissions",
        "/api/v1/token/validate",
        "/api/v1/token/user-info/{user_id}"
    ]
    
    # Add security requirement to protected endpoints
    for path, path_item in openapi_schema["paths"].items():
        if any(path.startswith(protected_path.split('{')[0]) for protected_path in protected_paths):
            for operation in path_item.values():
                if isinstance(operation, dict) and "operationId" in operation:
                    operation["security"] = [{"HTTPBearer": []}]
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add logging and correlation middleware (order matters - last added = first executed)
logging_config = {
    "log_requests": True,
    "log_responses": True,
    "log_body": getattr(settings, 'DEBUG', False),  # Only log body in debug mode
    "exclude_paths": ["/health", "/docs", "/redoc", "/auth_api.json", "/favicon.ico", "/static"]
}

# Add base path prefixed versions to exclude list if base path exists
if settings.BASE_PATH:
    base_exclude = [f"{settings.BASE_PATH}{p}" for p in logging_config["exclude_paths"]]
    logging_config["exclude_paths"].extend(base_exclude)

# Add logging middleware first (executes last)
app.add_middleware(LoggingMiddleware, **logging_config)
# Add correlation middleware second (executes second to last)
app.add_middleware(CorrelationMiddleware)
# Add cache middleware third (executes third to last)
# in main.py
app.add_middleware(
    CacheMiddleware,
    redis_host=getattr(settings, 'REDIS_HOST', 'localhost'),
    redis_port=getattr(settings, 'REDIS_PORT', 6379),
    redis_db=getattr(settings, 'REDIS_DB', 1),
    redis_password=getattr(settings, 'REDIS_PASSWORD', None),
    default_ttl=3600
)
# Initialize logger (uses global config from configure_logging above)
logger = logging.getLogger("auth_service")

# Pydantic models
class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    base_path: str
    timestamp: str

# Dependency Injection - Password Policy Service
def get_password_policy_service() -> PasswordPolicyService:
    """Dependency injection for password policy service"""
    return PasswordPolicyService()

# Import centralized RBAC guard
from libs.core.authz import global_authz_guard
from fastapi import Depends

# Include routers - authentication + required endpoints
from .api.v1.routes import auth, mfa, user_management, menu, role_management, menu_role_management, token_validation, service_endpoints

# Include authentication (essential) and required routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(mfa.router, prefix="/api/v1")

# Protected routers using centralized RBAC guard
app.include_router(user_management.router, prefix="/api/v1", dependencies=[Depends(global_authz_guard)])
app.include_router(role_management.router, prefix="/api/v1", dependencies=[Depends(global_authz_guard)])
app.include_router(menu.router, prefix="/api/v1", dependencies=[Depends(global_authz_guard)])
app.include_router(menu_role_management.router, prefix="/api/v1", dependencies=[Depends(global_authz_guard)])
app.include_router(token_validation.router, prefix="/api/v1")

# Service-to-service communication endpoints (require authentication but no specific permissions)
app.include_router(service_endpoints.router, prefix="/api/v1")



# Endpoints
@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """
    Health check endpoint
    
    Returns the current status of the auth service including:
    - Service health status
    - Version information
    - Current timestamp
    - New features: Menu-based RBAC system
    """
    return HealthResponse(
        status="healthy",
        service="auth-service-with-menu-rbac",
        version="1.1.0",
        environment=settings.ENVIRONMENT or "local",
        base_path=settings.BASE_PATH,
        timestamp=datetime.now().isoformat()
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=settings.DEBUG
    ) 