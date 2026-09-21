"""
EPMS Processing Service V2
Separate FastAPI app for V2 routes with dedicated Swagger documentation
"""

import logging
from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBearer
from libs.middleware import CorrelationMiddleware
from .middleware.auth import V2AuthMiddleware
from .api.router.dsm_overview import router as dsm_overview_router
from .api.router.market_overview import router as market_overview_router
from .api.router.graph_routes import router as graph_router
from .api.router.demand_forecast_routes import router as demand_forecast_router
from .api.router.dashboard_routes import router as dashboard_router
from .api.router.management_snapshot_routes import router as management_snapshot_router
from .api.router.rtm_scheduling_snapshot_routes import router as rtm_scheduling_snapshot_router
from .api.router.realtimemarket_routes import router as realtimemarket_routes
from .api.router.new_rtm_bidding_routes import (
    router as new_rtm_bidding_router,
    public_router as new_rtm_bidding_public_router,
)
from .api.router.new_dam_bidding_routes import (
    router as new_dam_bidding_router,
)
from .api.router.supply_routes import router as supply_router
from .api.router.master_plants_routes import router as master_plants_router
from .api.router.ppa_details_routes import router as ppa_details_router
from .api.router.limit_values_routes import router as limit_values_routers
from .api.router.renewable_routes import router as renewable_router
from .api.router.dam_sheduling_routes import router as dam_scheduling_router
from .api.router.dayaheadmarket_routes import router as dayaheadmarket_router
from .api.router.parameter_routes import router as parameter_router
from .api.router.job_execution_routes import router as job_execution_router
from .api.router.supply_manual_routes import router as supply_manual_router
from .api.router.exchange_config_routes import router as exchange_config_router
from .api.router.power_exchange_routes import router as power_exchange_router

logger = logging.getLogger(__name__)

# Configure security scheme for Bearer token authentication
security = HTTPBearer(auto_error=False)

# Create a separate FastAPI app for V2
v2_app = FastAPI(
    title="EPMS Processing Service V2",
    description="""
    ## EPMS Processing Service V2
    
    This is the V2 version of the EPMS Processing Service with enhanced features and improved architecture.
    
    ### Features:
    - **Enhanced Authentication**: JWT Bearer token validation with correlation ID tracking
    - **Request Tracking**: Automatic correlation ID injection for request tracing
    - **DSM Overview**: Advanced DSM data management and analysis
    - **Better Error Handling**: More detailed error responses and logging
    - **Improved Performance**: Optimized data processing and response times
    
    ### Authentication:
    All V2 endpoints require Bearer token authentication. Include your JWT token in the Authorization header:
    ```
    Authorization: Bearer <your-jwt-token>
    ```
    
    ### Request Tracking:
    Each request is automatically assigned a correlation ID for tracking. You can also provide your own:
    ```
    X-Correlation-ID: <your-correlation-id>
    ```
    """,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    tags=[
        {
            "name": "DSM Overview",
            "description": "DSM data management and analysis operations"
        },
        {
            "name": "Market Overview",
            "description": "Market data management and analysis operations"
        },
        {
            "name": "Graph Analysis",
            "description": "Graph analysis and visualization operations for demand forecasting"
        },
        {
            "name": "Demand Forecast",
            "description": "Demand forecasting operations and monthly average MAPE analysis"
        },
        {
            "name": "Dashboard V2",
            "description": "Enhanced dashboard operations with JWT authentication"
        },
        {
            "name": "Management Snapshot V2",
            "description": "Enhanced management snapshot operations with JWT authentication"
        },
        {
            "name": "Real-Time-Market",
            "description": "Enhanced Real Time Market operations with JWT authentication"
        },
        {
            "name": "Supply V2",
            "description": "Supply data operations"
        },
        {
            "name": "Master Plants V2",
            "description": "Master plants management operations"
        },
        {
            "name": "PPA Details V2",
            "description": "PPA details management — synced from plant master"
        },
        {
            "name": "Renewable V2",
            "description": "Renewable energy data operations with JWT authentication"
        },
        {
            "name": "Day-Ahead-Market",
            "description": "Day-Ahead-Market operations"
        },
        {
            "name": "Parameters V2",
            "description": "Parameter management operations with CRUD and bulk create support"
        },
        {
            "name": "Job Executions",
            "description": "Monitor scheduled job executions and view execution history"
        },
        {
            "name": "Supply Manual V2",
            "description": "DC/SG Template operations for manual supply data management"
        }
    ]
)

# Add middleware in correct order (last added = first executed)
# 1. Add authentication middleware (validates JWT tokens)
v2_app.add_middleware(
    V2AuthMiddleware,
    exclude_paths=["/rtm-bidding/public", "/api/v2/rtm-bidding/public"]
)
v2_app.add_middleware(
    V2AuthMiddleware,
    exclude_paths=["/rtm-bidding/public", "/api/v2/rtm-bidding/public"]
)

# 2. Add correlation middleware (injects correlation IDs)
v2_app.add_middleware(CorrelationMiddleware)

logger.info("✅ V2AuthMiddleware enabled for JWT authentication")

logger.info("V2 app initialized with authentication and correlation middleware")

# Include all V2 route modules
v2_app.include_router(dsm_overview_router)
v2_app.include_router(market_overview_router)
v2_app.include_router(graph_router)
v2_app.include_router(demand_forecast_router)
v2_app.include_router(dashboard_router) 
v2_app.include_router(management_snapshot_router)
v2_app.include_router(rtm_scheduling_snapshot_router)
v2_app.include_router(realtimemarket_routes)
v2_app.include_router(new_rtm_bidding_router)
v2_app.include_router(new_rtm_bidding_public_router)
v2_app.include_router(new_dam_bidding_router)
v2_app.include_router(supply_router)
v2_app.include_router(master_plants_router)
v2_app.include_router(ppa_details_router)
v2_app.include_router(limit_values_routers)
v2_app.include_router(renewable_router)
v2_app.include_router(dam_scheduling_router)
v2_app.include_router(dayaheadmarket_router)
v2_app.include_router(parameter_router)
v2_app.include_router(job_execution_router)
v2_app.include_router(supply_manual_router)
v2_app.include_router(exchange_config_router)
v2_app.include_router(power_exchange_router)

# Add a health check endpoint for V2
@v2_app.get("/health", tags=["Health"])
async def v2_health_check():
    """V2 Health check endpoint"""
    return {
        "status": "healthy",
        "service": "processing-service-v2",
        "version": "2.0.0",
        "features": [
            "JWT Authentication",
            "Correlation ID Tracking", 
            "Enhanced Error Handling"
        ]
    }

# Test authentication endpoint removed

# Simple OpenAPI configuration - security is handled by the main app
def custom_openapi():
    """Custom OpenAPI schema for V2 with Bearer token authentication"""
    if v2_app.openapi_schema:
        return v2_app.openapi_schema
    
    openapi_schema = get_openapi(
        title="EPMS Processing Service V2",
        version="2.0.0",
        description="Enhanced data processing service with JWT authentication",
        routes=v2_app.routes,
    )
    
    # Add /api/v2 prefix to all paths
    prefixed_paths = {}
    for path, path_item in openapi_schema["paths"].items():
        prefixed_path = f"/api/v2{path}" if not path.startswith("/api/v2") else path
        prefixed_paths[prefixed_path] = path_item
    
    openapi_schema["paths"] = prefixed_paths
    
    # Add security scheme
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter your Bearer token"
        }
    }
    
    # Add security to all endpoints except health
    for path, path_item in openapi_schema["paths"].items():
        if not path.endswith("/health"):
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation["security"] = [{"BearerAuth": []}]
    
    v2_app.openapi_schema = openapi_schema
    return v2_app.openapi_schema

v2_app.openapi = custom_openapi

# Export the app for mounting in main.py
__all__ = ["v2_app"]
