"""
MFA schemas for request/response models
"""

from typing import Optional
from pydantic import BaseModel, Field


class EnableMFARequest(BaseModel):
    """Enable MFA request schema"""
    pass  # No additional fields required - authentication via access token only


class EnableMFAResponse(BaseModel):
    """Enable MFA response schema"""
    secret: str = Field(..., description="TOTP secret key")
    qr_code: str = Field(..., description="QR code data URL for authenticator app")
    backup_codes: list[str] = Field(..., description="Backup codes for account recovery")
    remaining_time: int = Field(..., description="Seconds remaining in current TOTP period")


class VerifyMFARequest(BaseModel):
    """Verify MFA code request schema"""
    code: str = Field(..., description="6-digit TOTP code")


class VerifyMFAResponse(BaseModel):
    """Verify MFA response schema"""
    verified: bool = Field(..., description="Whether MFA code is valid")
    message: str = Field(..., description="Verification result message")


class DisableMFARequest(BaseModel):
    """Disable MFA request schema"""
    code: str = Field(..., description="6-digit TOTP code for verification - no password required")


class DisableMFAResponse(BaseModel):
    """Disable MFA response schema"""
    disabled: bool = Field(..., description="Whether MFA was successfully disabled")
    message: str = Field(..., description="Disable result message")


class MFARecoveryRequest(BaseModel):
    """MFA recovery request schema"""
    backup_code: str = Field(..., description="Backup code for account recovery")


class MFARecoveryResponse(BaseModel):
    """MFA recovery response schema"""
    recovered: bool = Field(..., description="Whether recovery was successful")
    message: str = Field(..., description="Recovery result message")
    new_backup_codes: Optional[list[str]] = Field(None, description="New backup codes if recovery successful")


class MFAStatusResponse(BaseModel):
    """MFA status response schema"""
    # Whether the authenticator method is offered at all, independent of this user's own
    # state. The client reads this to decide whether to show the enrolment option, rather
    # than presenting a button that returns 503.
    method_available: bool = Field(
        True, description="Whether authenticator-app 2FA is currently offered"
    )
    enabled: bool = Field(..., description="Whether MFA is enabled")
    setup_complete: bool = Field(..., description="Whether MFA setup is complete")
    backup_codes_remaining: int = Field(..., description="Number of backup codes remaining")


class EmergencyDisableRequest(BaseModel):
    """
    Body for the admin emergency-disable endpoint.

    The endpoint historically took `reason` as a query parameter. It still does, so no
    existing caller breaks; this optional body is the form a UI would rather send, and
    takes precedence when both are supplied.
    """
    reason: str = Field(..., description="Why MFA is being removed from this account") 