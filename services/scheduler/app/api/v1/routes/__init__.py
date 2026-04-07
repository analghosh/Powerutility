"""
API Routes for Scheduler Service
"""

from fastapi import APIRouter

router = APIRouter()

# Import route modules here
# from . import jobs, cron, health_check, etc.

@router.get("/")
async def root():
    """API root endpoint"""
    return {"message": "Scheduler Service API v1"} 