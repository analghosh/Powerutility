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
# mfa.router already declares prefix="/mfa"; adding it again here produced /mfa/mfa/...
# Harmless only because main.py mounts the route modules directly and never uses this
# aggregate router -- which is exactly why the mistake survived.
router.include_router(mfa.router, tags=["Multi-Factor Authentication"])
router.include_router(menu.router, prefix="/auth", tags=["Menu Management"])
router.include_router(user_management.router, prefix="/auth/user", tags=["User Management"]) 