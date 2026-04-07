"""
API Routes for Auth Service
"""

from fastapi import APIRouter

router = APIRouter()

# Import route modules here
from . import auth
from . import mfa
from . import menu
from . import user_management

# Include route modules
router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
router.include_router(mfa.router, prefix="/mfa", tags=["Multi-Factor Authentication"])
router.include_router(menu.router, prefix="/auth", tags=["Menu Management"])
router.include_router(user_management.router, prefix="/auth/user", tags=["User Management"]) 