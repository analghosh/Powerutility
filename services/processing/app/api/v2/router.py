"""
Processing Service API v2 Router
Integrates with the existing v2 FastAPI app structure
"""

from fastapi import APIRouter

# Import the existing v2 app which contains all v2 routes
from ...v2.v2 import v2_app

# Create a router that represents the v2 app
# Note: The actual v2 routes are handled by the v2_app which gets mounted in main.py
# This router exists for consistency with the API structure but delegates to v2_app
router = APIRouter(
    prefix="/api/v2",
    tags=["v2"]
)

# The v2 routes are actually handled by the v2_app FastAPI instance
# which includes:
# - DSM Overview routes
# - Market Overview routes  
# - Graph Analysis routes
# - Demand Forecast routes
# - Dashboard V2 routes
# - Management Snapshot V2 routes
# - JWT Authentication middleware
# - Correlation ID middleware