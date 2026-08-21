"""
Authentication schemas for request/response models
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Login request schema"""
    username: str = Field(..., description="Username or email")
    password: str = Field(..., description="Password")


class TokenResponse(BaseModel):
    """Token response schema"""
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiry time in seconds")
    refresh_expires_in: int = Field(..., description="Refresh token expiry time in seconds")
    expires_at: datetime = Field(..., description="Access token expiry datetime")
    refresh_expires_at: datetime = Field(..., description="Refresh token expiry datetime")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIs...",
                    "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
                    "token_type": "bearer",
                    "expires_in": 1800,
                    "refresh_expires_in": 604800,
                    "expires_at": "2025-07-23T03:30:00Z",
                    "refresh_expires_at": "2025-07-30T03:30:00Z"
                }
            ]
        }
    }


class MinimalUserResponse(BaseModel):
    """Minimal user response schema for login with essential user info"""
    # The plain login path already returned an id; this schema dropped it, so the route
    # layer audited every MFA login as user_id="unknown" -- the one flow that proves who
    # the user is was the one the audit trail could not name. Optional so an older caller
    # that omits it still validates.
    id: Optional[str] = Field(None, description="User ID")
    # Only include fields that exist in the database profile
    first_name: Optional[str] = Field(None, description="User's first name from profile")
    last_name: Optional[str] = Field(None, description="User's last name from profile")
    last_login: Optional[datetime] = Field(None, description="Last login time")


class UserResponse(BaseModel):
    """Full user response schema for profile endpoints"""
    # Core user fields from database
    id: str = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    email: Optional[str] = Field(None, description="Email address")
    is_active: bool = Field(..., description="Account active status")
    is_verified: bool = Field(..., description="Email verification status")
    is_mfa_enabled: bool = Field(False, description="Whether MFA is enabled for this account")
    roles: List[str] = Field(default=[], description="User roles")
    permissions: List[str] = Field(default=[], description="User permissions")
    created_at: datetime = Field(..., description="Account creation time")
    last_login: Optional[datetime] = Field(None, description="Last login time")
    
    # Profile fields (only if they exist in the database profile)
    first_name: Optional[str] = Field(None, description="User's first name from profile")
    last_name: Optional[str] = Field(None, description="User's last name from profile")
    
    # Additional profile fields that might exist
    phone: Optional[str] = Field(None, description="User's phone number from profile")
    address: Optional[Dict[str, Any]] = Field(None, description="User's address from profile")
    preferences: Optional[Dict[str, Any]] = Field(None, description="User's preferences from profile")


class TemporaryTokenResponse(BaseModel):
    """Response with temporary token for MFA verification"""
    temporary_token: str = Field(..., description="Temporary token for MFA verification")
    mfa_required: bool = Field(..., description="Whether MFA is required for this user")
    expires_in: int = Field(..., description="Temporary token expiry time in seconds")
    message: str = Field(..., description="Instructions for next step")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "temporary_token": "temp_eyJhbGciOiJIUzI1NiIs...",
                    "mfa_required": True,
                    "expires_in": 300,
                    "message": "Please provide TOTP code to complete login"
                },
                {
                    "temporary_token": "temp_eyJhbGciOiJIUzI1NiIs...",
                    "mfa_required": False,
                    "expires_in": 300,
                    "message": "Please verify login to complete authentication"
                }
            ]
        }
    }


class VerifyLoginRequest(BaseModel):
    """Verify login request schema"""
    temporary_token: str = Field(..., description="Temporary token from login step")
    totp_code: Optional[str] = Field(None, description="TOTP code from authenticator app")
    backup_code: Optional[str] = Field(None, description="Backup code (use if authenticator is unavailable)")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "temporary_token": "temp_eyJhbGciOiJIUzI1NiIs...",
                    "totp_code": "123456"
                },
                {
                    "temporary_token": "temp_eyJhbGciOiJIUzI1NiIs...",
                    "backup_code": "DQ2O-AFJF-ZLFA"
                }
            ]
        }
    }


class LoginResponse(BaseModel):
    """Login response schema with minimal user info and tokens"""
    user: MinimalUserResponse = Field(..., description="Minimal user information")
    tokens: TokenResponse = Field(..., description="Authentication tokens")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "user": {
                        "first_name": "Admin",
                        "last_name": "User",
                        "last_login": "2025-07-23T03:20:00Z"
                    },
                    "tokens": {
                        "access_token": "eyJhbGciOiJIUzI1NiIs...",
                        "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
                        "token_type": "bearer",
                        "expires_in": 1800,
                        "refresh_expires_in": 604800,
                        "expires_at": "2025-07-23T03:50:00Z",
                        "refresh_expires_at": "2025-07-30T03:50:00Z"
                    }
                }
            ]
        }
    }


class RefreshTokenRequest(BaseModel):
    """Refresh token request schema"""
    refresh_token: str = Field(..., description="Refresh token")
    # The access token this refresh replaces. Optional so a client that predates this
    # field still refreshes normally -- during a rollout the previous bundle is still
    # loaded in browsers, and rejecting those would log every one of them out.
    access_token: Optional[str] = Field(
        None, description="Access token being replaced; blacklisted once the new one is issued"
    )


class LogoutRequest(BaseModel):
    """Logout request schema"""
    refresh_token: Optional[str] = Field(None, description="Refresh token to invalidate (optional)")


class ChangePasswordRequest(BaseModel):
    """Change password request schema"""
    current_password: str = Field(..., description="Current password")
    new_password: str = Field(..., description="New password")


class ErrorResponse(BaseModel):
    """Error response schema"""
    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Error details")
    code: Optional[str] = Field(None, description="Error code")


# -------------------------------------------------------------
# Generic single-message response (used by /logout and similar)
# -------------------------------------------------------------


class MessageResponse(BaseModel):
    """Simple message wrapper"""

    message: str = Field(..., description="Human-readable confirmation message")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"message": "Logged out successfully"}
            ]
        }
    } 