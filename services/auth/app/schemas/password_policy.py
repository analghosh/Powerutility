"""
Password Policy Schemas
Pydantic models for password policy validation and responses
"""

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class PasswordValidationRequest(BaseModel):
    """Password validation request model"""
    password: str = Field(..., min_length=1, description="Password to validate")
    user_id: str = Field(..., description="User ID for validation context")
    
    class Config:
        json_schema_extra = {
            "example": {
                "password": "MySecureP@ss123",
                "user_id": "user123"
            }
        }


class PasswordValidationResponse(BaseModel):
    """Password validation response model"""
    is_valid: bool = Field(..., description="Whether password meets all requirements")
    strength_score: int = Field(..., ge=0, le=100, description="Password strength score (0-100)")
    violations: List[str] = Field(default_factory=list, description="List of policy violations")
    suggestions: List[str] = Field(default_factory=list, description="Suggestions for improvement")
    user_id: str = Field(..., description="User ID")


class PasswordExpiryResponse(BaseModel):
    """Password expiry response model"""
    is_expired: bool = Field(..., description="Whether password has expired")
    days_until_expiry: Optional[int] = Field(None, description="Days until password expires")
    expiry_date: Optional[str] = Field(None, description="Password expiry date")
    user_id: str = Field(..., description="User ID")
    requires_change: bool = Field(..., description="Whether password change is required")


class AdminPasswordResetRequest(BaseModel):
    """Admin password reset request model"""
    user_id: str = Field(..., description="User ID to reset password for")
    admin_id: str = Field(..., description="Admin ID performing the reset")
    reason: str = Field(default="password_reset", description="Reason for password reset")
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user123",
                "admin_id": "admin456",
                "reason": "password_reset"
            }
        }


class AdminPasswordResetResponse(BaseModel):
    """Admin password reset response model"""
    temporary_password: str = Field(..., description="Generated temporary password")
    expires_at: str = Field(..., description="Temporary password expiry time")
    user_id: str = Field(..., description="User ID")
    admin_id: str = Field(..., description="Admin ID who performed reset")
    message: str = Field(..., description="Response message")


class AdminUnlockUserRequest(BaseModel):
    """Admin unlock user request model"""
    user_id: str = Field(..., description="User ID to unlock")
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user123"
            }
        }


class AdminResetPasswordRequest(BaseModel):
    """Admin reset password request model"""
    user_id: str = Field(..., description="User ID to reset password for")
    new_password: str = Field(..., min_length=8, description="New password for the user")
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user123",
                "new_password": "NewSecureP@ss123"
            }
        }


class LockoutStatusResponse(BaseModel):
    """Account lockout status response model"""
    is_locked: bool = Field(..., description="Whether account is currently locked")
    remaining_attempts: int = Field(..., ge=0, description="Remaining login attempts")
    lockout_until: Optional[str] = Field(None, description="Lockout expiry time")
    user_id: str = Field(..., description="User ID")


class PolicyConfigurationResponse(BaseModel):
    """Password policy configuration response model"""
    min_password_length: int = Field(..., description="Minimum password length")
    require_uppercase: bool = Field(..., description="Require uppercase letters")
    require_lowercase: bool = Field(..., description="Require lowercase letters")
    require_numbers: bool = Field(..., description="Require numbers")
    require_special_chars: bool = Field(..., description="Require special characters")
    password_expiry_days: int = Field(..., description="Password expiry period in days")
    password_history_count: int = Field(..., description="Number of previous passwords to remember")
    max_login_attempts: int = Field(..., description="Maximum failed login attempts")
    lockout_duration_minutes: int = Field(..., description="Account lockout duration in minutes")
    lockout_window_hours: int = Field(..., description="Lockout window in hours") 