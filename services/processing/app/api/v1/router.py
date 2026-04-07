"""
Processing Service API v1 Router
Aggregates all v1 routes into a single router
"""

from fastapi import APIRouter
from .routes import (
    processing,
    dashboard_routes,
    supply_routes,
    dsmoverview_routes,
    demandforecast_routes,
    renewable_supply_routes,
    marketintgrator_routes,
    forecastAnalysis_routes,
    management_snapshot_routes,
    master_plants_routes,
    dam_scheduling_routes,
    realtimemarket_routes,
    dayaheadmarket_routes,
    audit_routes,
    limit_value_routes,
    weather_routes,
    demand_routes,
    rtm_scheduling_routes,
    block_bid_routes
)

# Create the main v1 router with proper API prefix
router = APIRouter(
    prefix="/api/v1"
)

# Include all v1 route modules
router.include_router(processing.router)
router.include_router(dashboard_routes.router)
router.include_router(supply_routes.router)
router.include_router(dsmoverview_routes.router)
router.include_router(demandforecast_routes.router)
router.include_router(renewable_supply_routes.router)
router.include_router(marketintgrator_routes.router)
router.include_router(forecastAnalysis_routes.router)
router.include_router(management_snapshot_routes.router)
router.include_router(master_plants_routes.router)
router.include_router(dam_scheduling_routes.router)
router.include_router(realtimemarket_routes.router)
router.include_router(dayaheadmarket_routes.router)
router.include_router(audit_routes.router)
router.include_router(limit_value_routes.router)
router.include_router(weather_routes.router)
router.include_router(demand_routes.router)
router.include_router(rtm_scheduling_routes.router)
router.include_router(block_bid_routes.router)