"""
JWT service models and data structures
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel


class TokenPayload(BaseModel):
    """JWT token payload structure"""
    sub: str  # Subject (user ID)
    username: str
    email: Optional[str] = None
    is_active: bool = True
    roles: List[str] = []
    permissions: List[str] = []
    profile: Dict[str, Any] = {}
    last_login: Optional[str] = None
    mfa_enabled: bool = False
    mfa_setup_complete: bool = False
    accessible_menus: List[str] = []
    exp: Optional[int] = None  # Expiration timestamp
    iat: Optional[int] = None  # Issued at timestamp
    type: str = "access"  # Token type: access or refresh
    session_id: Optional[str] = None


class TokenResponse(BaseModel):
    """Token generation response"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # Seconds until access token expires
    refresh_expires_in: int  # Seconds until refresh token expires
    expires_at: str  # ISO format expiration time
    refresh_expires_at: str  # ISO format refresh expiration time


class ValidationResult(BaseModel):
    """Token validation result"""
    is_valid: bool
    payload: Optional[TokenPayload] = None
    error: Optional[str] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    roles: List[str] = []
    permissions: List[str] = []
