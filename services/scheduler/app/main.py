"""
EPMS Scheduler Service
Handles cron jobs and scheduling
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import logging

from .core.config import settings
from .api.v1.routes import scheduler

from .routers import queue_health, job_monitoring, rtm_scheduling_router, bid_trade_live_sync_router, demand_trigger_router
from .services.cron_manager import cron_manager
from .seeder.timeblock_seeder import TimeblockSeeder
from .shared.middleware.error_handler import ErrorHandlerMiddleware

# Configure logging with correlation ID support
from libs.middleware import configure_logging, LoggingMiddleware, CorrelationMiddleware, CacheMiddleware, SecurityHeadersMiddleware
from libs.middleware.auth_middleware import AuthMiddleware
from libs.authz.route_guard import ACTION_WRITE, guard

# Configure logging based on settings
configure_logging(
    level=settings.LOG_LEVEL,
    format_type=getattr(settings, 'LOG_FORMAT', 'colored'),
    include_correlation=True
)

# Initialize logger (uses global config from configure_logging above)
logger = logging.getLogger("scheduler_service")


class HealthResponse(BaseModel):
    """Health check response model"""
    status: str
    service: str
    version: str
    timestamp: str


def run_timeblock_seeder():
    """Run the timeblock seeder and handle the results."""
    try:
        logger.info("Running timeblock seeder...")
        timeblock_seeder = TimeblockSeeder()
        seeder_result = timeblock_seeder.seed()
        
        if seeder_result['status'] == 'success':
            logger.info("Timeblock seeder completed successfully: %s", seeder_result['message'])
        elif seeder_result['status'] == 'skipped':
            logger.info("Timeblock seeder skipped: %s", seeder_result['message'])
        else:
            logger.warning("Timeblock seeder had issues: %s", seeder_result.get('message', 'Unknown error'))
            
    except Exception as e:
        logger.error("Failed to run timeblock seeder: %s", str(e))



def validate_database_configuration():
    """Validate database configuration"""
    try:
        # Check that job collections are properly configured
        job_collections = [
            settings.JOB_DATA_COLLECTION,
            settings.JOB_DETAIL_COLLECTION,
            settings.JOB_DETAIL_HISTORICAL_COLLECTION
        ]
        
        for collection in job_collections:
            if not collection:
                raise ValueError(f"Job collection name cannot be empty: {collection}")
        
        # Validate that required database settings are present
        if not settings.AUDIT_DB_URI:
            raise ValueError("AUDIT_DB_URI cannot be empty")
        
        if not settings.AUDIT_DB_NAME:
            raise ValueError("AUDIT_DB_NAME cannot be empty")
        
        print(f"✅ Database configuration validated - using database: {settings.AUDIT_DB_NAME}")
        
    except Exception as e:
        print(f"❌ Database configuration error: {e}")
        raise


async def start_cron_manager_async():
    """Start the cron manager asynchronously as a background task"""
    try:
        # Start the cron manager directly in the current event loop
        # since AsyncIOScheduler needs to be started in the same thread as the event loop
        cron_manager.start()
        logger.info("Cron scheduler started successfully in background")
    except Exception as e:
        logger.error(f"Failed to start cron scheduler in background: {str(e)}")


async def run_startup_deduplication():
    """Run historical deduplication (blocking startup)"""
    try:
        from .utils.deduplicator import run_historical_deduplication
        logger.info("🛡️ Starting historical deduplication (Blocking startup)...")
        # await run_historical_deduplication()
        logger.info("✅ Historical deduplication completed")
    except Exception as e:
        logger.error(f"❌ Historical deduplication failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    # Startup
    logger.info("Starting EPMS Scheduler Service...")
    logger.info(f"   Service: {settings.SERVICE_NAME}")
    logger.info(f"   Host: {settings.SERVICE_HOST}:{settings.SERVICE_PORT}")
    logger.info(f"   Debug: {settings.DEBUG}")
    logger.info(f"   Log Level: {settings.LOG_LEVEL}")
    
    # Step 0: Run historical deduplication - ABSOLUTELY BLOCKING
    # No other tasks (seeding, cron, consumers) should proceed until this is finished
    await run_startup_deduplication()
    
    # Validate configuration on startup
    validate_database_configuration()

    # Ensure forecast_serve indexes (migrate off legacy unique key; CSV + merge reads)
    try:
        from .repository.forecast_serve_repository import forecast_serve_repository

        await forecast_serve_repository.create_indexes()
        await forecast_serve_repository.ensure_historical_collection()
    except Exception as idx_error:
        logger.warning("Failed to ensure forecast_serve indexes: %s", idx_error)

    # Run timeblock seeder
    run_timeblock_seeder()
    
    # Step 2: Start the cron scheduler as a background task
    try:
        import asyncio
        # Start cron manager in background task
        asyncio.create_task(start_cron_manager_async())
        logger.info("Cron scheduler background task created successfully")
        
    except Exception as e:
        logger.error(f"Failed to start cron scheduler: {str(e)}")

    # Step 3: Start RabbitMQ Consumers
    try:
        from services.scheduler.app.consumers.consumer_manager import ConsumerManager
        # Initialize consumer manager with the cron_manager instance and running loop
        # We attach it to app.state to keep it alive and accessible
        loop = asyncio.get_running_loop()
        app.state.consumer_manager = ConsumerManager(cron_manager, loop)
        app.state.consumer_manager.start()
        logger.info("RabbitMQ Consumer Manager started")
    except Exception as e:
        logger.error(f"Failed to start RabbitMQ Consumer Manager: {str(e)}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down EPMS Scheduler Service...")
    
    # Stop Consumers
    if hasattr(app.state, 'consumer_manager'):
        try:
            app.state.consumer_manager.stop()
            logger.info("RabbitMQ Consumer Manager stopped")
        except Exception as e:
            logger.error(f"Failed to stop Consumer Manager: {str(e)}")

    # Stop the cron scheduler
    try:
        cron_manager.stop()
        logger.info("Cron scheduler stopped successfully")
    except Exception as e:
        logger.error(f"Failed to stop cron scheduler: {str(e)}")


# Create FastAPI app
app = FastAPI(
    title=settings.SERVICE_NAME,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None,
    redoc_url="/redoc" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None,
    openapi_url="/openapi.json" if (settings.DEBUG or settings.ENABLE_SWAGGER) else None,
)

# Add error handling middleware
app.add_middleware(ErrorHandlerMiddleware)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add middleware (order matters - last added = first executed)
logging_config = {
    "log_requests": True,
    "log_responses": True,
    "log_body": settings.DEBUG,  # Only log body in debug mode
    "exclude_paths": ["/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico", "/static"]
}

# Add logging middleware first (executes last)
app.add_middleware(LoggingMiddleware, **logging_config)
# Add correlation middleware second (executes second to last)
app.add_middleware(CorrelationMiddleware)
# Security headers added to every response
app.add_middleware(SecurityHeadersMiddleware)

# Authentication. This was commented out with the note "AuthMiddleware removed to allow
# internal trigger without authentication", which left every job-control endpoint on this
# service open to anyone who could reach the port -- including a job-disable route and the
# full 80-job inventory.
#
# The internal callers are real: five call sites in the processing service
# (cron_orchestrator_service, rtm_scheduling_controller, the DAM and RTM bidding routes)
# invoke this service with no credentials. They now present INTERNAL_SERVICE_TOKEN via the
# X-Internal-Token header, which AuthMiddleware accepts in place of a login.
#
# The previous exclude list is deliberately not carried over. "/", "/config", "/login" and
# "/dashboard" were exempt, and "/api/v1/scheduler/rtm-scheduling" exempted the RTM
# trigger by prefix -- the exact endpoint the internal caller needed, left open to
# everyone as a side effect. Only the paths that genuinely cannot carry a credential
# remain.
app.add_middleware(
    AuthMiddleware,
    auth_service_url=settings.AUTH_SERVICE_URL,
    internal_token=settings.INTERNAL_SERVICE_TOKEN,
    exclude_paths=[
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/static",
    ],
)

# Add cache middleware third (executes third to last)
from libs.cache.config import get_ttl_config

app.add_middleware(
    CacheMiddleware,
    redis_host=settings.REDIS_HOST,
    redis_port=settings.REDIS_PORT,
    redis_db=settings.REDIS_DB,
    redis_password=settings.REDIS_PASSWORD or None,
    default_ttl=3600,
    ttl_config=get_ttl_config()
)

# Include routers.
#
# Authentication alone would leave every job-control endpoint open to any of the ~790
# accounts that can log in -- including disabling a scheduled job. Everything this service
# exposes is platform operation, so it all requires `admin`. The internal service
# credential resolves to a context holding "*", so trusted service-to-service calls pass
# the same guard rather than bypassing it.
# action="write" rather than deriving it from the method: nearly every role holds some
# `admin:read` from the Administration menu, and a GET here returns the entire job
# inventory. Reading job control is an administrative act too.
_admin_only = [guard("admin", action=ACTION_WRITE)]

app.include_router(scheduler.router, prefix="/api/v1/scheduler", tags=["scheduler"], dependencies=_admin_only)
app.include_router(queue_health.router, tags=["queue"], dependencies=_admin_only)
app.include_router(job_monitoring.router, tags=["job-monitoring"], dependencies=_admin_only)
app.include_router(rtm_scheduling_router.router, prefix="/api/v1/scheduler", tags=["rtm-scheduling"], dependencies=_admin_only)
app.include_router(bid_trade_live_sync_router.router, prefix="/api/v1/scheduler", tags=["bid-trade-live-sync"], dependencies=_admin_only)
app.include_router(demand_trigger_router.router, prefix="/api/v1/scheduler", tags=["demand-trigger"], dependencies=_admin_only)

# Mount static files
from pathlib import Path
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# Endpoints
@app.get("/config")
async def get_config():
    """Get frontend configuration"""
    return {
        "auth_service_url": settings.AUTH_SERVICE_URL + "/api/v1",
        "base_path": settings.BASE_PATH
    }


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Serve login page"""
    static_dir = Path(__file__).parent / "static"
    login_file = static_dir / "login.html"
    
    if login_file.exists():
        return HTMLResponse(content=login_file.read_text(encoding='utf-8'))
    else:
        return HTMLResponse(content="<h1>Login page not found</h1>", status_code=404)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    """Serve dashboard page"""
    static_dir = Path(__file__).parent / "static"
    dashboard_file = static_dir / "job_dashboard.html"
    
    if dashboard_file.exists():
        return HTMLResponse(content=dashboard_file.read_text(encoding='utf-8'))
    else:
        return HTMLResponse(content="<h1>Dashboard page not found</h1>", status_code=404)


@app.get("/", response_class=HTMLResponse)
async def index_page():
    """Serve index page"""
    static_dir = Path(__file__).parent / "static"
    index_file = static_dir / "index.html"
    
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding='utf-8'))
    else:
        return HTMLResponse(content="<h1>Welcome to Scheduler Service</h1>")


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": settings.SERVICE_NAME,
        "scheduler_running": cron_manager.is_running(),
        "database": settings.AUDIT_DB_NAME
    }


@app.get("/scheduler/diagnostics")
async def scheduler_diagnostics():
    """Get detailed scheduler diagnostics to identify missed jobs"""
    from datetime import datetime, timedelta
    from libs.database.mongo_client_factory import get_mongo_client
    import pytz
    
    try:
        # Get scheduler status
        scheduler_status = await cron_manager.get_all_jobs()
        
        # Get registered jobs configuration
        registered_jobs = cron_manager.get_registered_jobs()
        
        # Use centralized MongoDB client (singleton pattern)
        client = get_mongo_client(settings.AUDIT_DB_URI, "scheduler_diagnostics")
        db = client[settings.AUDIT_DB_NAME]
        job_detail_collection = db[settings.JOB_DETAIL_COLLECTION]
        
        # Check last 24 hours for all jobs
        since_24h = datetime.now() - timedelta(hours=24)
        since_1h = datetime.now() - timedelta(hours=1)
        
        # Get all job types from registered jobs
        all_job_types = list(registered_jobs.keys())
        
        job_health = {}
        midnight_jobs = []
        
        for job_type in all_job_types:
            job_config = registered_jobs[job_type]
            schedule = job_config.get('schedule', '')
            
            # Count executions in last 24 hours
            count_24h = await job_detail_collection.count_documents({
                "job_type": job_type,
                "execution_start": {"$gte": since_24h}
            })

            # Count executions in last hour
            count_1h = await job_detail_collection.count_documents({
                "job_type": job_type,
                "execution_start": {"$gte": since_1h}
            })

            # Get last execution
            last_exec = await job_detail_collection.find_one(
                {"job_type": job_type},
                sort=[("execution_start", -1)]
            )
            
            # Determine expected runs based on schedule
            if schedule == "* * * * *":
                expected_1h = 60
                expected_24h = 1440
            elif schedule.startswith("*/15"):
                expected_1h = 4
                expected_24h = 96
            elif schedule.startswith("*/30"):
                expected_1h = 2
                expected_24h = 48
            elif schedule in ["0 0 * * *", "0 1 * * *", "0 2 * * *"]:
                expected_1h = 0  # Midnight jobs don't run every hour
                expected_24h = 1
                midnight_jobs.append(job_type)
            else:
                expected_1h = 0
                expected_24h = 0
            
            # Determine status
            if schedule in ["0 0 * * *", "0 1 * * *", "0 2 * * *"]:
                # For midnight jobs, check if they ran in last 24h
                if count_24h >= 1:
                    status = "🟢 OK (ran in last 24h)"
                elif count_24h == 0 and job_config.get('enabled'):
                    status = "🔴 NEVER RAN (enabled but no executions)"
                else:
                    status = "⚪ DISABLED or WAITING"
            else:
                # For frequent jobs, check last hour
                if expected_1h > 0 and count_1h < expected_1h:
                    status = f"🔴 MISSING ({count_1h}/{expected_1h} runs in last hour)"
                else:
                    status = "🟢 OK"
            
            # Calculate next run time
            next_run_info = None
            try:
                from apscheduler.triggers.cron import CronTrigger
                parts = schedule.split()
                if len(parts) == 5:
                    minute, hour, day, month, dow = parts
                    tz = pytz.timezone(job_config.get('timezone', 'Asia/Kolkata'))
                    trigger = CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow, timezone=tz)
                    now = datetime.now(tz)
                    next_run = trigger.get_next_fire_time(None, now)
                    if next_run:
                        time_until = (next_run - now).total_seconds()
                        next_run_info = {
                            "time": next_run.strftime('%Y-%m-%d %H:%M:%S %Z'),
                            "seconds_until": int(time_until),
                            "minutes_until": round(time_until / 60, 1)
                        }
            except Exception as e:
                next_run_info = {"error": str(e)}
            
            job_health[job_type] = {
                "name": job_config.get('name', job_type),
                "schedule": schedule,
                "enabled": job_config.get('enabled', False),
                "status": status,
                "executions_last_hour": count_1h,
                "executions_last_24h": count_24h,
                "expected_per_hour": expected_1h,
                "expected_per_24h": expected_24h,
                "last_execution": last_exec["execution_start"].isoformat() if last_exec else None,
                "next_run": next_run_info,
                "timezone": job_config.get('timezone', 'Unknown')
            }
        
        return {
            "timestamp": datetime.now().isoformat(),
            "server_timezone": datetime.now().astimezone().strftime('%Z %z'),
            "scheduler_running": scheduler_status["scheduler_running"],
            "total_registered_jobs": len(registered_jobs),
            "total_active_jobs": scheduler_status["active_jobs"],
            "currently_running": scheduler_status.get("currently_running", 0),
            "midnight_jobs": midnight_jobs,
            "job_health": job_health,
            "summary": {
                "healthy_jobs": sum(1 for j in job_health.values() if "OK" in j["status"]),
                "missing_executions": sum(1 for j in job_health.values() if "MISSING" in j["status"]),
                "never_ran": sum(1 for j in job_health.values() if "NEVER RAN" in j["status"]),
                "disabled_or_waiting": sum(1 for j in job_health.values() if "DISABLED" in j["status"] or "WAITING" in j["status"])
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting scheduler diagnostics: {e}")
        import traceback
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "timestamp": datetime.now().isoformat()
        }


@app.get("/cache/health")
async def cache_health_check():
    """Cache service health check endpoint"""
    from libs.middleware import check_cache_health
    return await check_cache_health()


@app.get("/cache/stats")
async def cache_stats():
    """Get cache service statistics"""
    from libs.middleware import get_cache_metrics
    return get_cache_metrics()


import re
from fastapi import Header, HTTPException, status

# Security: restricted cache pattern characters
_CACHE_PATTERN_RE = re.compile(r"^[a-zA-Z0-9_:\-\.\*]+$")
_MAX_PATTERN_LEN = 128

async def _require_admin_auth(authorization: str = Header(None)):
    """Dependency to require valid admin JWT for cache clear"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    
    token = authorization.split(" ", 1)[1]
    from libs.auth_client import AuthClient
    auth_client = AuthClient(settings.AUTH_SERVICE_URL)
    
    async with auth_client:
        result = await auth_client.validate_token(token)
    
    if not result.valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    
    # Require admin or cache:clear permission
    if not (result.has_role("admin") or result.has_permission("cache:clear") or result.has_permission("*")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    return result


@app.post("/cache/clear/{pattern}")
async def clear_cache_pattern(pattern: str, user=Depends(_require_admin_auth)):
    """Clear cache keys matching a pattern (admin only)"""
    # Input validation
    if len(pattern) > _MAX_PATTERN_LEN:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pattern too long")
    
    if not _CACHE_PATTERN_RE.match(pattern):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid pattern characters")
    
    # Security: block overly broad patterns that would clear all keys
    if pattern == "*" or pattern == "**":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pattern too broad")
    
    from libs.middleware import clear_cache_pattern
    cleared_count = clear_cache_pattern(pattern)
    logger.info(f"Cache clear by user {user.username}: pattern='{pattern}', cleared={cleared_count}")
    return {
        "message": f"Cleared {cleared_count} cache keys matching pattern '{pattern}'",
        "cleared_count": cleared_count
    }





if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.SERVICE_HOST,
        port=settings.SERVICE_PORT,
        reload=settings.DEBUG
    ) 