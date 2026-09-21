"""
EPMS Processing Service - Clean Structure
Maintains essential functionality with clean router organization
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBearer
from pydantic import BaseModel
import logging
from starlette.responses import JSONResponse

from .core.config import settings
# Simple version constant
APP_VERSION = "2.0.0"
API_V2_VERSION = "2.0.0"
from .v2.services.health_service import processing_health_service

# Configure logging with correlation ID support
from libs.middleware import configure_logging, LoggingMiddleware, CorrelationMiddleware, AuthMiddleware, SecurityHeadersMiddleware

# Import V2 authentication middleware
from .v2.middleware.auth import V2AuthMiddleware
from .v2.middleware.https_enforcement import HTTPSEnforcementMiddleware

# Configure logging based on settings
configure_logging(
    level=settings.LOG_LEVEL,
    format_type=getattr(settings, 'LOG_FORMAT', 'colored'),
    include_correlation=True,
    use_colors=getattr(settings, 'LOG_USE_COLORS', True)
)



# Initialize logger (uses global config from configure_logging above)
logger = logging.getLogger("processing_service")


class HealthResponse(BaseModel):
    """Health check response model"""
    status: str
    service: str
    version: str
    environment: str
    base_path: str
    timestamp: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    # Startup
    logger.info("Starting EPMS Processing Service...")
    from .v2.repository.master_plants_repository import master_plants_repository
    await master_plants_repository.create_indexes()
    try:
        from .v2.repository.ppa_details_repository import ppa_details_repository
        await ppa_details_repository.create_indexes()
    except Exception as e:
        logger.error(f"Failed to ensure ppa_details indexes: {e}")
    from .v2.repository.bid_trade_serve_repository import BidTradeServeRepository
    BidTradeServeRepository()

    # Ensure indexes on limit_audit_log (121M+ docs with no query indexes — primary OOM cause)
    try:
        from .v2.repository.limit_workflow_repository import limit_workflow_repository
        await limit_workflow_repository.ensure_audit_indexes(
            settings.MONGODB_LIMIT_AUDIT_COLLECTION
        )
    except Exception as e:
        logger.error(f"Failed to ensure limit_audit_log indexes: {e}")

    # Ensure targeted indexes on audit_logs (matched to API query patterns only)
    try:
        from .v2.services.audit_logger_service import audit_logger_service
        await audit_logger_service.ensure_indexes()
    except Exception as e:
        logger.error(f"Failed to ensure audit_logs indexes: {e}")

    # Ensure indexes on serve_dam_scheduling (207K docs, previously only _id index)
    try:
        from .v2.repository.dam_scheduling_repository import DamSchedulingRepository
        dam_scheduling_repo = DamSchedulingRepository()
        await dam_scheduling_repo.ensure_indexes()
    except Exception as e:
        logger.error(f"Failed to ensure dam_scheduling indexes: {e}")

    # Ensure TTL index on dc_sg.scheduled_at (auto-deletes records older than 1 day)
    try:
        from .v2.repository.supply_manual_repository import supply_manual_repository
        await supply_manual_repository.ensure_indexes()
    except Exception as e:
        logger.error(f"Failed to ensure dc_sg TTL index: {e}")

    # Ensure unique + query indexes on limit_values (previously created lazily, only on approve())
    try:
        from .v2.repository.limit_values_repository import limit_values_repository
        await limit_values_repository.ensure_collection_exists(
            limit_values_repository.collection_name
        )
    except Exception as e:
        logger.error(f"Failed to ensure limit_values indexes: {e}")

    # One-time migration: encrypt plaintext BearerTokens in exchange_config
    try:
        from .v2.migrations.encrypt_bearer_tokens import run_encrypt_bearer_tokens_migration
        await run_encrypt_bearer_tokens_migration()
    except Exception as e:
        logger.error(f"BearerToken encryption migration failed: {e}")

    # Ensure default bid-price parameters exist (dam/gdam/hpdam)
    try:
        from .v2.services.parameter_service import parameter_service
        created = await parameter_service.ensure_bid_price_defaults()
        if created:
            logger.info(f"Auto-created bid price parameters: {created}")
    except Exception as e:
        logger.error(f"Failed to ensure bid price parameter defaults: {e}")

    # The analytics dashboard's peak/off-peak bands are defined in Masters →
    # Parameters; seed the five keys empty so PTC can find and fill them.
    try:
        from .v2.services.analytics_service import ensure_block_band_parameters
        created = await ensure_block_band_parameters()
        if created:
            logger.info(f"Auto-created analytics block band parameters: {created}")
    except Exception as e:
        logger.error(f"Failed to ensure analytics block band parameters: {e}")

    # Analytics read-path indexes, plus the unique user_id on the preference rows.
    # Runs here rather than as a manual deploy step for the same reason every block
    # above does: a step that must be remembered is a step that gets missed, and the
    # failure mode is silent — `demand_serve` COLLSCANs on both sides and the screen
    # merely feels slow. Idempotent, `background=True`, and each index is caught
    # individually inside `apply`, so a restart costs nothing and cannot be blocked by
    # one bad index. Still runnable by hand ahead of a deploy:
    #   ./.venv/bin/python -m migrations.analytics_indexes_2026_09_17
    try:
        from migrations.analytics_indexes_2026_09_17 import apply as apply_analytics_indexes
        from .v2.repository.analytics_repository import AnalyticsRepository
        created = await apply_analytics_indexes(AnalyticsRepository().db)
        logger.info(f"Analytics indexes ensured: {len(created)}")
    except Exception as e:
        logger.error(f"Failed to ensure analytics indexes: {e}")

    yield
    # Shutdown
    logger.info("Shutting down EPMS Processing Service...")


# Import V2 routers
from .v2.api.router.dsm_overview import router as dsm_overview_router
from .v2.api.router.market_overview import router as market_overview_router
from .v2.api.router.graph_routes import router as graph_router
from .v2.api.router.demand_forecast_routes import router as demand_forecast_router
from .v2.api.router.dashboard_routes import router as dashboard_v2_router
from .v2.api.router.management_snapshot_routes import router as management_snapshot_v2_router
from .v2.api.router.rtm_scheduling_snapshot_routes import router as rtm_scheduling_snapshot_v2_router
from .v2.api.router.price_group_routes import router as price_group_router
from .v2.api.router.supply_routes import router as supply_router
from .v2.api.router.master_plants_routes import router as master_plant_router
from .v2.api.router.ppa_details_routes import router as ppa_details_router
from .v2.api.router.forecastAnalysis_routes import router as forecast_analysis
from .v2.api.router.limit_values_routes import router as limit_values_routers
from .v2.api.router.weather_routes import router as weather_router
# from .v2.api.router.rtm_bidding_routes import router as rtm_bidding_router
from .v2.api.router.new_rtm_bidding_routes import (
    router as new_rtm_bidding_router,
    public_router as new_rtm_bidding_public_router,
)
from .v2.api.router.new_dam_bidding_routes import (
    router as new_dam_bidding_router,
    public_router as new_dam_bidding_public_router,
)
from .v2.api.router.new_gdam_bidding_routes import (
    router as new_gdam_bidding_router,
    public_router as new_gdam_bidding_public_router,
)
from .v2.api.router.new_hpdam_bidding_routes import (
    router as new_hpdam_bidding_router,
    public_router as new_hpdam_bidding_public_router,
)
from .v2.api.router.renewable_routes import router as renewable_routes
from .v2.api.router.dam_sheduling_routes import router as dam_scheduling_router
from .v2.api.router.block_bid_routes import router as block_bid_route
from .v2.api.router.rtm_scheduling_routes import router as rtm_scheduling_router
from .v2.api.router.dayaheadmarket_routes import router as dayaheadmarket_router
from .v2.api.router.audit_logger_routes import router as audit_logger_router
from .v2.api.router.parameter_routes import router as parameter_router
from .v2.api.router.idf_forecast_switch_routes import router as idf_forecast_switch_router
from .v2.api.router.forecast_serve_routes import router as forecast_serve_router
from .v2.api.router.demand_serve_historical_routes import router as demand_serve_historical_router
from .v2.api.router.reports_forecast_routes import router as reports_forecast_router
from .v2.api.router.reports_plf_paf_routes import router as reports_plf_paf_router
from .v2.api.router.reports_shift_status_routes import router as reports_shift_status_router
from .v2.api.router.urs_routes import router as urs_router
from .v2.api.router.bid_market_routes import router as bid_market_router
from .v2.api.router.job_execution_routes import router as job_execution_router
from .v2.api.router.supply_manual_routes import router as supply_manual_router
from .v2.api.router.demand_manual_routes import router as demand_manual_router
from .v2.api.router.power_optimization_routes import router as power_optimization_router
from .v2.api.router.power_exchange_routes import router as power_exchange_router
from .v2.api.router.iex_api_detail_routes import router as iex_api_detail_router
from .v2.api.router.api_key_management_routes import router as api_key_management_router 
from .v2.api.router.exchange_trade_routes import router as exchange_trade_router
from .v2.api.router.exchange_dsm_routes import router as exchange_dsm_router
from .v2.api.router.exchange_report_routes import router as exchange_report_router
from .v2.api.router.exchange_config_routes import router as exchange_config_router
from .v2.api.router.realtimemarket_routes import router as realtimemarket_router
from .v2.api.router.cron_orchestrator_routes import router as cron_orchestrator_router
from .v2.api.router.power_exchange_parameter_routes import router as power_exchange_parameter_router
from .v2.api.router.analytics_routes import router as analytics_router


# Get environment from settings (which has proper defaults and validation)
environment = settings.ENVIRONMENT

# Main FastAPI application
app = FastAPI(
    title="EPMS Processing Service - V1",
    description=f"""
    ## EPMS Processing Service - V1 Endpoints
    
    Core processing functionality (No authentication required).
    
    **Environment**: {environment}
    """,
    version=APP_VERSION,
    docs_url="/docs" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None,
    redoc_url="/redoc" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None,
    openapi_url="/openapi.json" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None,
    lifespan=lifespan,
)


# V2 Sub-application with proper swagger configuration for server environments
v2_docs_url = "/docs" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None
v2_redoc_url = "/redoc" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None
v2_openapi_url = "/openapi.json" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None

v2_app = FastAPI(
    title="EPMS Processing Service - V2",
    description=f"""
    ## EPMS Processing Service - V2 Endpoints
    
    Enhanced processing with JWT authentication (🔒 Bearer token required).
    
    **Environment**: {environment}
    **Base Path**: {settings.BASE_PATH}
    
    ### Swagger Access:
    - **Docs**: `{settings.BASE_PATH}/api/v2/docs`
    - **ReDoc**: `{settings.BASE_PATH}/api/v2/redoc`
    - **OpenAPI**: `{settings.BASE_PATH}/api/v2/openapi.json`
    """,
    version=API_V2_VERSION,
    docs_url=v2_docs_url,
    redoc_url=v2_redoc_url,
    openapi_url=v2_openapi_url,
)

# Mount V2 as a sub-application
app.mount("/api/v2", v2_app)


# Add middleware (order matters: last added = first executed)

# Add logging middleware first
app.add_middleware(
    LoggingMiddleware,
    log_requests=getattr(settings, 'LOG_REQUESTS', True),
    log_responses=getattr(settings, 'LOG_RESPONSES', True),
    log_body=getattr(settings, 'LOG_BODY', False),
    exclude_paths=["/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico"]
)

# Add correlation middleware second
app.add_middleware(CorrelationMiddleware)
# Security headers added to every response
app.add_middleware(SecurityHeadersMiddleware)

# Define paths to exclude from authentication
exclude_paths = [
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v2/health",
    "/api/v2/docs",
    "/api/v2/redoc",
    "/api/v2/openapi.json",
    "/api/v2/rtm-bidding/public",
    "/rtm-bidding/public",
    "/api/v2/dam-bidding/public",
    "/dam-bidding/public",
    "/api/v2/gdam-bidding/public",
    "/gdam-bidding/public",
    "/api/v2/hpdam-bidding/public",
    "/hpdam-bidding/public",
    # Exclude power optimization - it uses HMAC authentication instead of JWT
    "/api/v2/power-optimization",  # Matches /api/v2/power-optimization/*
    "/api/v2/power-optimization/",  # Also match with trailing slash
    "/api/v2/power-optimization/power-optimization-input",  # Explicit match
    "/power-optimization",  # Relative path (for mounted app)
    "/power-optimization/",  # Relative path with trailing slash
    # Note: V2 API routes now require authentication - use valid JWT tokens
    # Exception: power-optimization uses HMAC authentication
]

# Add v1 paths to exclude list only if v1 API is enabled
if settings.ENABLE_V1_API:
    exclude_paths.append("/api/v1")

# If a base path is configured, add prefixed versions to the exclude list
if settings.BASE_PATH:
    exclude_paths.extend([f"{settings.BASE_PATH}{p}" for p in exclude_paths])
     # Also add power-optimization specific paths with BASE_PATH
    exclude_paths.extend([
        f"{settings.BASE_PATH}/api/v2/power-optimization",
        f"{settings.BASE_PATH}/api/v2/power-optimization/",
        f"{settings.BASE_PATH}/api/v2/power-optimization/power-optimization-input",
    ])
# Add Auth Middleware third
app.add_middleware(
    AuthMiddleware,
    auth_service_url=settings.AUTH_SERVICE_URL,
    exclude_paths=exclude_paths,
)

# Add HTTPS enforcement middleware for HMAC endpoints
app.add_middleware(HTTPSEnforcementMiddleware)

# Add CORS middleware last to ensure it runs first
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
    # `allow_headers` governs the REQUEST; a response header stays unreadable to
    # JavaScript unless it is exposed. Without this the analytics export saves under
    # the client's own guess at a name: the server sends
    # `Content-Disposition: attachment; filename="..."`, curl sees it, and the
    # browser cannot — so a user-chosen file name silently never reached the file.
    # Found by driving the real UI; the API had looked correct throughout.
    expose_headers=["Content-Disposition"],
)


# V1 API disabled - comment out to re-enable
# app.include_router(v1_router)
logger.info("🚫 V1 API endpoints disabled")

# Include all V2 route modules in the V2 sub-application
v2_app.include_router(dsm_overview_router)
v2_app.include_router(market_overview_router)
v2_app.include_router(graph_router)
v2_app.include_router(demand_forecast_router)
v2_app.include_router(dashboard_v2_router)
v2_app.include_router(management_snapshot_v2_router)
v2_app.include_router(rtm_scheduling_snapshot_v2_router)
v2_app.include_router(price_group_router)
v2_app.include_router(supply_router)
v2_app.include_router(master_plant_router)
v2_app.include_router(ppa_details_router)
v2_app.include_router(forecast_analysis)
v2_app.include_router(limit_values_routers)
v2_app.include_router(weather_router)
# v2_app.include_router(rtm_bidding_router)
v2_app.include_router(new_rtm_bidding_router)
v2_app.include_router(new_rtm_bidding_public_router)
v2_app.include_router(new_dam_bidding_router)
v2_app.include_router(new_dam_bidding_public_router)
v2_app.include_router(new_gdam_bidding_router)
v2_app.include_router(new_gdam_bidding_public_router)
v2_app.include_router(new_hpdam_bidding_router)
v2_app.include_router(new_hpdam_bidding_public_router)
v2_app.include_router(renewable_routes)
v2_app.include_router(dam_scheduling_router)
v2_app.include_router(block_bid_route)
v2_app.include_router(rtm_scheduling_router)
v2_app.include_router(dayaheadmarket_router)
v2_app.include_router(audit_logger_router)
v2_app.include_router(parameter_router)
v2_app.include_router(idf_forecast_switch_router)
v2_app.include_router(forecast_serve_router)
v2_app.include_router(demand_serve_historical_router)
v2_app.include_router(reports_forecast_router)
v2_app.include_router(reports_plf_paf_router)
v2_app.include_router(reports_shift_status_router)
v2_app.include_router(urs_router)
v2_app.include_router(bid_market_router)
v2_app.include_router(job_execution_router)
v2_app.include_router(supply_manual_router)
v2_app.include_router(demand_manual_router)
v2_app.include_router(power_optimization_router)
v2_app.include_router(power_exchange_router)
v2_app.include_router(iex_api_detail_router)
v2_app.include_router(api_key_management_router)
v2_app.include_router(exchange_trade_router)
v2_app.include_router(exchange_dsm_router)
v2_app.include_router(exchange_report_router)
v2_app.include_router(exchange_config_router)
v2_app.include_router(realtimemarket_router)
v2_app.include_router(cron_orchestrator_router)
v2_app.include_router(power_exchange_parameter_router)
v2_app.include_router(analytics_router)



# Add middleware (order matters - last added = first executed)
logging_config = {
    "log_requests": getattr(settings, 'LOG_REQUESTS', True),
    "log_responses": getattr(settings, 'LOG_RESPONSES', True),
    "log_body": getattr(settings, 'LOG_BODY', False),  # Only log body in debug mode
    "exclude_paths": ["/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico"]
}

# Add debugging middleware for URL issues
@app.middleware("http")
async def debug_main_app_requests(request: Request, call_next):
    """Debug middleware to log all requests to main app"""
    if "processing" in str(request.url):
        logger.info(f"🔍 MAIN APP REQUEST - Method: {request.method}, URL: {request.url}, Path: {request.url.path}, Query: {request.url.query}")
    response = await call_next(request)
    return response

# Simple and robust authentication middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Handle authentication for V2 routes using auth service validation"""
    path = request.url.path
    
    # Normalize path (remove double slashes, trailing slashes)
    path = path.replace("//", "/").rstrip("/")
    
    # Debug logging
    logger.info(f"🔍 MAIN AUTH MIDDLEWARE - Path: {path}")
    
    # V2 routes should use auth service validation, not local JWT validation
    # Define paths that need authentication
    base_path = settings.BASE_PATH
    v2_paths = ["/api/v2/"]
    if base_path:
        v2_paths.append(f"{base_path}/api/v2/")
    
    # Define paths to exclude from authentication (swagger docs, health, etc.)
    # NOTE: gdam/hpdam public paths intentionally NOT excluded here — like
    # dam-bidding/public, they must be validated by this middleware so
    # request.state.current_user is set for the routes' get_current_user
    # dependency (local JWT fallback fails due to cross-service secrets).
    exclude_paths = [
        "/api/v2/health",
        "/api/v2/docs",
        "/api/v2/redoc",
        "/api/v2/openapi.json",
        # Exclude power optimization - it uses HMAC authentication instead of JWT
        "/api/v2/power-optimization",  # Matches /api/v2/power-optimization/*
        "/api/v2/power-optimization/",  # Also match with trailing slash
        "/api/v2/power-optimization/power-optimization-input",  # Explicit match
    ]

    # Add base path prefixed versions for server environments
    if base_path:
        exclude_paths.extend([
            f"{base_path}/api/v2/health",
            f"{base_path}/api/v2/docs",
            f"{base_path}/api/v2/redoc",
            f"{base_path}/api/v2/openapi.json",
            # CRITICAL: Add BASE_PATH-prefixed power-optimization paths for server
            f"{base_path}/api/v2/power-optimization",  # Matches /processing/api/v2/power-optimization/*
            f"{base_path}/api/v2/power-optimization/",  # With trailing slash
            f"{base_path}/api/v2/power-optimization/power-optimization-input",  # Explicit match
        ])
    
    # Allow OPTIONS requests to pass through for CORS preflight
    if request.method == "OPTIONS":
        logger.info(f"🔓 Bypassing auth for OPTIONS request: {path}")
        return await call_next(request)

    # Debug: Log the paths being checked
    logger.info(f"🔍 V2 paths: {v2_paths}")
    logger.info(f"🔍 Exclude paths: {exclude_paths}")

    # Check if this is a V2 route that needs authentication
    needs_auth = any(v2_path in path for v2_path in v2_paths)
    # Improved exclusion check - check if path starts with any exclude path
    is_excluded = any(
        path.startswith(exclude_path.rstrip("/")) or 
        path == exclude_path.rstrip("/") or
        path.startswith(exclude_path + "/")
        for exclude_path in exclude_paths
    )
    
    logger.info(f"🔍 Path: {path}, Needs auth: {needs_auth}, Is excluded: {is_excluded}")
    
    # CRITICAL: If path is excluded, skip JWT authentication early (HMAC will handle it)
    if is_excluded:
        logger.info(f"🔓 SKIPPING JWT AUTH for excluded path: {path}")
        return await call_next(request)
    
    if needs_auth and not is_excluded:
        logger.info(f"🔐 AUTH REQUIRED for V2 route: {path}")
        
        # Get Authorization header
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        
        if not auth_header:
            logger.warning(f"❌ NO AUTH HEADER for: {path}")
            return JSONResponse(status_code=401, content={"detail": "Authorization header required"})
        
        if not auth_header.startswith("Bearer "):
            logger.warning(f"❌ INVALID AUTH FORMAT for: {path}")
            return JSONResponse(status_code=401, content={"detail": "Bearer token required"})
        
        # Extract token and validate with auth service
        try:
            token = auth_header.split(" ")[1]
            logger.info(f"✅ TOKEN EXTRACTED for: {path}")

            # Use auth service for validation instead of local JWT validation
            from libs.auth_client import AuthClient
            auth_client = AuthClient(settings.AUTH_SERVICE_URL)
            
            async with auth_client:
                validation_result = await auth_client.validate_token(token)
                
                if not validation_result.valid:
                    logger.error(f"❌ AUTH SERVICE VALIDATION FAILED for: {path}")
                    return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})
                
                # Create user context from auth service response
                request.state.current_user = {
                    "user_id": validation_result.user_id,
                    "username": validation_result.username,
                    "email": validation_result.email,
                    "roles": validation_result.roles,
                    "permissions": validation_result.permissions,
                    "token": token
                }
                logger.info(f"✅ USER CONTEXT SET for: {path} - User: {validation_result.username}")
            
        except Exception as e:
            logger.error(f"❌ AUTH PROCESSING FAILED for: {path} - Error: {e}")
            return JSONResponse(status_code=401, content={"detail": f"Authentication failed: {str(e)}"})
    
    else:
        logger.info(f"🔓 NO AUTH NEEDED for: {path}")
    
    # Continue with request
    response = await call_next(request)
    return response

# NOTE: Middleware is already added at lines 207-216
# DO NOT add duplicate middleware here - it causes "No response returned" errors
# with mounted sub-applications due to conflicting call_next chains

# Health endpoints
@app.get("/health", tags=["Health"])
async def health_check():
    """Basic health check endpoint"""
    return await processing_health_service.get_simple_health()

@v2_app.get("/health", tags=["V2 Health"])
async def v2_health_check():
    """V2 Health check endpoint with swagger URL information"""
    base_path = settings.BASE_PATH
    
    swagger_urls = {
        "docs": f"{base_path}/api/v2/docs" if base_path else "/api/v2/docs",
        "redoc": f"{base_path}/api/v2/redoc" if base_path else "/api/v2/redoc",
        "openapi": f"{base_path}/api/v2/openapi.json" if base_path else "/api/v2/openapi.json"
    }
    
    return {
        "status": "healthy",
        "service": "processing-service-v2",
        "version": API_V2_VERSION,
        "environment": environment,
        "base_path": base_path,
        "swagger_urls": swagger_urls,
        "features": [
            "JWT Authentication",
            "Correlation ID Tracking", 
            "Enhanced Error Handling"
        ]
    }


def custom_openapi_v2():
    """Custom OpenAPI schema for V2 with security definitions and proper server URLs"""
    if v2_app.openapi_schema:
        return v2_app.openapi_schema
    
    openapi_schema = get_openapi(
        title=v2_app.title,
        version=v2_app.version,
        description=v2_app.description,
        routes=v2_app.routes,
    )
    
    # Add server URLs with base path for proper OpenAPI documentation
    base_path = settings.BASE_PATH
    
    openapi_schema["servers"] = [{"url": "/", "description": "API"}]
    
    # Add security scheme for JWT Bearer token
    openapi_schema["components"]["securitySchemes"] = {
        "HTTPBearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter your JWT token for V2 API endpoints (e.g., 'Bearer eyJhbGciOiJIUzI1NiI9...)"
        }
    }
    
    # Add security to all V2 endpoints except health
    for path, path_item in openapi_schema["paths"].items():
        if not path.endswith("/health"):
            for operation in path_item.values():
                if isinstance(operation, dict) and "operationId" in operation:
                    operation["security"] = [{"HTTPBearer": []}]
    
    v2_app.openapi_schema = openapi_schema
    return v2_app.openapi_schema

# Set custom OpenAPI function for V2 app
v2_app.openapi = custom_openapi_v2


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=settings.DEBUG
    )
