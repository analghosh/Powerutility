"""
Schemas module for Auth Service
"""

from .password_policy import (
    PasswordValidationRequest,
    PasswordValidationResponse,
    PasswordExpiryResponse,
    AdminPasswordResetRequest,
    AdminPasswordResetResponse,
    AdminUnlockUserRequest,
    AdminResetPasswordRequest,
    LockoutStatusResponse,
    PolicyConfigurationResponse
)

from .auth import (
    LoginRequest,
    LoginResponse,
    TokenResponse,
    UserResponse,
    MinimalUserResponse,
    RefreshTokenRequest,
    LogoutRequest,
    ErrorResponse
)

from .mfa import (
    EnableMFARequest,
    EnableMFAResponse,
    VerifyMFARequest,
    VerifyMFAResponse,
    DisableMFARequest,
    DisableMFAResponse,
    MFARecoveryRequest,
    MFARecoveryResponse,
    MFAStatusResponse
)

__all__ = [
    "PasswordValidationRequest",
    "PasswordValidationResponse", 
    "PasswordExpiryResponse",
    "AdminPasswordResetRequest",
    "AdminPasswordResetResponse",
    "AdminUnlockUserRequest",
    "AdminResetPasswordRequest",
    "LockoutStatusResponse",
    "PolicyConfigurationResponse",
    "LoginRequest",
    "LoginResponse",
    "TokenResponse",
    "UserResponse",
    "MinimalUserResponse",
    "RefreshTokenRequest",
    "LogoutRequest",
    "ErrorResponse",
    "EnableMFARequest",
    "EnableMFAResponse",
    "VerifyMFARequest",
    "VerifyMFAResponse",
    "DisableMFARequest",
    "DisableMFAResponse",
    "MFARecoveryRequest",
    "MFARecoveryResponse",
    "MFAStatusResponse"
] 
