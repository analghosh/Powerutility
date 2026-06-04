"""
EPMS Ingestion Service
Handles data ingestion and validation
"""
from datetime import datetime

from pydantic import BaseModel

from .core.config import settings
from .services.cron_manager import CronManager
from .core.dependencies import get_audit_logger, cleanup_audit_resources

# Configure logging with correlation ID support
from libs.middleware import configure_logging, LoggingMiddleware, CorrelationMiddleware, CacheMiddleware, SecurityHeadersMiddleware

# Configure logging based on settings
configure_logging(
    level=settings.LOG_LEVEL,
    format_type=getattr(settings, 'LOG_FORMAT', 'colored'),
    include_correlation=True
)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBearer
import logging

from .api.v1.routes import ingestion, file_upload, providers
from .core.logger import get_ingestion_logger

# Initialize logger (uses global config from configure_logging above)
logger = get_ingestion_logger("ingestion_service")


class HealthResponse(BaseModel):
    """Health check response model"""
    status: str
    service: str
    version: str
    environment: str
    base_path: str
    timestamp: str


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    # Startup
    settings.check_security_warnings()
    logger.info("Starting EPMS Ingestion Service...")
    logger.info("   Service: %s", settings.SERVICE_NAME if hasattr(settings, 'SERVICE_NAME') else 'ingestion-service')
    logger.info("   Host: %s:%s", settings.SERVICE_HOST, settings.SERVICE_PORT)
    logger.info("   Debug: %s", settings.DEBUG)
    logger.info("   Log Level: %s", settings.LOG_LEVEL)
    
    # Create audit logger and cron manager
    audit_logger = get_audit_logger()
    cron_manager = CronManager(audit_logger)
    await cron_manager.initialize_jobs()
    app.state.cron_manager = cron_manager

    # Ensure provider indexes
    try:
        from libs.repository.provider_repository import ProviderRepository
        _provider_repo = ProviderRepository(settings.MONGODB_URI, settings.MONGODB_DATABASE)
        await _provider_repo.ensure_indexes()
    except Exception as idx_error:
        logger.warning("Failed to ensure provider indexes: %s", idx_error)

    # Ensure forecast_serve indexes (revision-aware unique key for append-only ingest)
    try:
        from .repository.forecast_serve_repository import forecast_serve_repository

        await forecast_serve_repository.create_indexes()
    except Exception as idx_error:
        logger.warning("Failed to ensure forecast_serve indexes: %s", idx_error)

    # Seed default providers (e.g., forecast_api_provider)
    try:
        from .seeders.provider_seeder import seed_providers
        seed_providers()
        logger.info("Provider seeding completed")
    except Exception as seed_error:
        logger.warning("Failed to seed providers: %s", seed_error)

    # Start the cron scheduler
    try:
        cron_manager.start()
        logger.info("Cron scheduler started successfully")
        
        # Execute background file processing immediately on startup
        try:
            # Get the first available job to execute on startup
            registered_jobs = cron_manager.get_registered_jobs()
            if registered_jobs:
                job_id = list(registered_jobs.keys())[0]  # Get first job
                logger.info("Executing initial job '%s' immediately on startup...", job_id)
                result = await cron_manager.execute_job_immediately(job_id)
                if result:
                    logger.info("Initial job '%s' executed successfully with result: %s", job_id, result)
                else:
                    logger.warning("Initial job '%s' execution returned no result", job_id)
            else:
                logger.warning("No jobs available to execute on startup")
        except (RuntimeError, OSError, ValueError) as trigger_error:
            logger.warning("Failed to execute initial job on startup: %s", trigger_error)
            
    except (RuntimeError, OSError, ValueError) as e:
        logger.error("Failed to start cron scheduler: %s", e)
    
    yield
    
    # Shutdown
    logger.info("Shutting down EPMS Ingestion Service...")
    
    # Stop the cron scheduler
    try:
        cron_manager.stop()
        logger.info("Cron scheduler stopped successfully")
    except (RuntimeError, OSError, AttributeError) as e:
        logger.error("Failed to stop cron scheduler: %s", str(e))

    cleanup_audit_resources()


# Create FastAPI app
app = FastAPI(
    title="EPMS Ingestion Service",
    description="Data ingestion service for EPMS",
    version="1.0.0",
    docs_url="/docs" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None,
    redoc_url="/redoc" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None,
    openapi_url="/openapi.json" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None,
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "File Upload",
            "description": "CSV file upload operations for data ingestion",
        },
        {
            "name": "Data Ingestion",
            "description": "Data ingestion operations",
        },
        {
            "name": "Provider Management",
            "description": "Provider CRUD operations and management",
        },
    ],
)


# Configure security scheme for Bearer token authentication
security = HTTPBearer(auto_error=False)


def custom_openapi():
    """Custom OpenAPI schema with Bearer token authentication"""
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title="EPMS Ingestion Service",
        version="1.0.0",
        description="Data ingestion service for EPMS",
        routes=app.routes,
    )
    
    # Add server URLs with base path for proper OpenAPI documentation
    base_path = settings.BASE_PATH
    environment = settings.ENVIRONMENT
    openapi_schema["servers"] = [{"url": "/", "description": "API"}]

    # Add security scheme
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter your Bearer token in the format: Bearer <your-token>"
        }
    }
    
    # Add security requirement to all protected endpoints
    for path_item in openapi_schema["paths"].values():
        for operation in path_item.values():
            if isinstance(operation, dict) and "tags" in operation:
                # Only add security to non-health endpoints
                if "health" not in operation.get("summary", "").lower():
                    operation["security"] = [{"BearerAuth": []}]
    
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
    "log_body": settings.DEBUG,  # Only log body in debug mode
    "exclude_paths": ["/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico", "/static"]
}

# Add base path prefixed versions to exclude list if base path exists
if settings.BASE_PATH:
    base_exclude = [f"{settings.BASE_PATH}{p}" for p in logging_config["exclude_paths"]]
    logging_config["exclude_paths"].extend(base_exclude)

# Add logging middleware first (executes last)
app.add_middleware(LoggingMiddleware, **logging_config)
# Add correlation middleware second (executes second to last)
app.add_middleware(CorrelationMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# Add cache middleware third (executes third to last)
app.add_middleware(
    CacheMiddleware,
    redis_host=getattr(settings, 'REDIS_HOST', 'localhost'),
    redis_port=getattr(settings, 'REDIS_PORT', 6379),
    redis_db=getattr(settings, 'REDIS_DB', 2),  # Use different DB for ingestion service
    redis_password=getattr(settings, 'REDIS_PASSWORD', None),
    default_ttl=3600
)

# Include routers
app.include_router(ingestion.router, prefix="/api/v1")
app.include_router(file_upload.router, prefix="/api/v1")
app.include_router(providers.router, prefix="/api/v1")


# Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        service="ingestion-service",
        version="1.0.0",
        environment=settings.ENVIRONMENT or "local",
        base_path=settings.BASE_PATH,
        timestamp=datetime.now().isoformat()
    )


@app.get("/cron/status")
async def cron_status():
    """Get cron scheduler status"""
    cron_manager = app.state.cron_manager
    return {
        "service": "ingestion-service",
        "cron_scheduler_running": cron_manager.is_running(),
        "jobs": cron_manager.get_all_jobs()
    }


@app.post("/cron/trigger/{job_id}")
async def trigger_cron_job(job_id: str):
    """Trigger a specific cron job immediately"""
    from fastapi import HTTPException
    cron_manager = app.state.cron_manager
    registered = cron_manager.get_registered_jobs()
    if job_id not in registered:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    success = cron_manager.trigger_job_now(job_id)
    return {
        "service": "ingestion-service",
        "job_id": job_id,
        "triggered": success
    }


@app.post("/cron/trigger-all")
async def trigger_all_cron_jobs():
    """Trigger all registered cron jobs immediately"""
    cron_manager = app.state.cron_manager
    registered = cron_manager.get_registered_jobs()
    results = {}
    for job_id in registered:
        results[job_id] = cron_manager.trigger_job_now(job_id)
    return {
        "service": "ingestion-service",
        "triggered": results
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=settings.DEBUG
    ) 
