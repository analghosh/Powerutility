"""
API Routes for Ingestion Service
"""

from fastapi import APIRouter

router = APIRouter()

# Import route modules
from . import file_upload, ingestion, providers

# Include route modules
router.include_router(file_upload.router)
router.include_router(ingestion.router)
router.include_router(providers.router)

@router.get("/")
async def root():
    """API root endpoint"""
    return {"message": "Ingestion Service API v1"} 