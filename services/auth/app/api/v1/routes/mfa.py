"""
MFA API routes
"""

import os
import sys
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ....controllers.mfa_controller import mfa_controller
from ....controllers.auth_controller import auth_controller
from datetime import datetime

# Import secure audit logging
from ....core.dependencies import get_audit_logger
from ....core.dto import AuthUserDTO
from ....schemas.mfa import (
    EnableMFARequest, EnableMFAResponse, VerifyMFARequest, VerifyMFAResponse,
    DisableMFARequest, DisableMFAResponse, MFARecoveryRequest, MFARecoveryResponse,
    MFAStatusResponse, EmergencyDisableRequest
)

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..', '..'))
from libs.authz.route_guard import is_allowed
from libs.middleware.utils import get_client_ip_or_none

# Create router
router = APIRouter(prefix="/mfa", tags=["Multi-Factor Authentication"])

# Security scheme
security = HTTPBearer()

# Permissions guarding the two admin endpoints. `is_allowed` composes "<resource>:<action>",
# so these read as mfa:emergency_disable and mfa:reset_rate_limit -- both registered in
# libs/core/authz/permissions_catalog.py.
_MFA_RESOURCE = "mfa"
_ACTION_EMERGENCY_DISABLE = "emergency_disable"
_ACTION_RESET_RATE_LIMIT = "reset_rate_limit"


def _require_mfa_admin(admin_user: AuthUserDTO, action: str) -> None:
    """
    Authorize an MFA admin action against the shared authorization primitives.

    The hand-rolled check this replaces was
    `if "admin" not in roles and "<perm>" not in permissions`, which could not pass anyone:
    neither permission existed in the catalogue so the second clause never fired, and a
    superadmin carries `permissions=["*"]`, which a literal `in` test does not satisfy.
    `is_allowed` handles the `*` wildcard, `mfa:*`, and the admin/superadmin role bypass
    that the rest of the platform already relies on.
    """
    principal = {
        "roles": getattr(admin_user, "roles", []) or [],
        "permissions": getattr(admin_user, "permissions", []) or [],
    }
    if is_allowed(principal, _MFA_RESOURCE, action):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Missing required permission: {_MFA_RESOURCE}:{action}",
    )


@router.post("/enable", response_model=EnableMFAResponse, summary="Enable MFA")
async def enable_mfa(
    request: Request,
    enable_request: EnableMFARequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Enable Multi-Factor Authentication for the current user
    
    ## Security Change Notice:
    **No password required** - Authentication is handled via access token only.
    
    ## Requirements:
    - Valid access token in Authorization header
    - Request body can be empty `{}`
    
    ## Process:
    1. User provides access token (already authenticated)
    2. System generates TOTP secret and QR code
    3. User scans QR code with authenticator app (Google Authenticator, Authy, etc.)
    4. User calls `/verify` endpoint with first TOTP code to complete setup
    5. Future logins will require two-step verification
    
    ## Returns:
    - `secret`: TOTP secret key (backup - user should save this)
    - `qr_code`: Base64-encoded QR code image for easy setup
    - `backup_codes`: Emergency recovery codes (save securely!)
    - `remaining_time`: Seconds left in current TOTP period
    
    ## After enabling:
    - Login flow changes to two-step process
    - User gets temporary token from `/login`
    - Must use `/verify-login` with TOTP code to get final tokens
    
    ## App Configuration:
    - Issuer name: Configurable via `APP_NAME` environment variable
    - TOTP window: ±1 time period tolerance (configurable)
    - Backup codes: 8 single-use recovery codes generated
    """
    # Get current user
    token = credentials.credentials
    user = await auth_controller.get_current_user(token)
    
    if not user:
        # Log failed MFA enable attempt with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        get_audit_logger().log_security_event(
            service="auth",
            event_type="mfa_enable_failed",
            message="MFA enable attempt failed - invalid access token",
            success=False,
            client_ip=client_ip,
            failure_reason="invalid_access_token",
            user_agent=user_agent
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token"
        )
    
    try:
        result = await mfa_controller.enable_mfa(user.id, enable_request)
        
        # Log successful MFA enable with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        get_audit_logger().log_security_event(
            service="auth",
            event_type="mfa_enable_success",
            message=f"MFA enabled successfully for user: {user.username}",
            user_id=user.id,
            client_ip=client_ip,
            username=user.username,
            mfa_enabled=True,
            qr_code_generated=True,
            backup_codes_count=len(getattr(result, "backup_codes", [])),
            user_agent=user_agent,
            success=True
        )
        
        return result
        
    except Exception as e:
        # Log failed MFA enable with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        get_audit_logger().log_error_event(
            service="auth",
            error_name="MFAEnableError",
            error_message=f"MFA enable failed for user {user.username}: {str(e)}",
            user_id=user.id,
            client_ip=client_ip,
            username=user.username,
            mfa_enable_failed=True,
            exception_type=type(e).__name__,
            user_agent=user_agent
        )
        raise


@router.post("/verify", response_model=VerifyMFAResponse, summary="Verify MFA Code")
async def verify_mfa(
    request: Request,
    verify_request: VerifyMFARequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Verify MFA code for the current user
    
    Requires:
    - Valid access token in Authorization header
    - 6-digit TOTP code
    
    Returns:
    - Verification result
    """
    # Get current user
    token = credentials.credentials
    user = await auth_controller.get_current_user(token)
    
    if not user:
        # Log failed MFA verify attempt with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        get_audit_logger().log_security_event(
            service="auth",
            event_type="mfa_verify_failed",
            message="MFA verify attempt failed - invalid access token",
            success=False,
            client_ip=client_ip,
            failure_reason="invalid_access_token",
            user_agent=user_agent
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token"
        )
    
    try:
        result = await mfa_controller.verify_mfa(user.id, verify_request)
        
        # Log successful MFA verification with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        get_audit_logger().log_security_event(
            service="auth",
            event_type="mfa_verify_success",
            message=f"MFA verification successful for user: {user.username}",
            user_id=user.id,
            client_ip=client_ip,
            username=user.username,
            verification_successful=True,
            verification_method="totp_code",
            user_agent=user_agent,
            success=True
        )
        
        return result
        
    except Exception as e:
        # Log failed MFA verification with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        get_audit_logger().log_error_event(
            service="auth",
            error_name="MFAVerifyError",
            error_message=f"MFA verification failed for user {user.username}: {str(e)}",
            user_id=user.id,
            client_ip=client_ip,
            username=user.username,
            verification_failed=True,
            exception_type=type(e).__name__,
            user_agent=user_agent
        )
        raise


@router.post("/disable", response_model=DisableMFAResponse, summary="Disable MFA")
async def disable_mfa(
    request: DisableMFARequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Disable MFA for the current user
    
    ## IMPROVED SECURITY: No password required!
    
    Requires:
    - Valid access token in Authorization header (user is already authenticated)
    - 6-digit TOTP code for verification (proves user controls the authenticator)
    
    ## Why this is more secure:
    - User is already authenticated via access token
    - TOTP code proves physical possession of authenticator device
    - No need to transmit password over network again
    - Reduces password exposure
    
    Returns:
    - Disable result
    """
    # Get current user
    token = credentials.credentials
    user = await auth_controller.get_current_user(token)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token"
        )
    
    return await mfa_controller.disable_mfa(user.id, request)


@router.post("/recover", response_model=MFARecoveryResponse, summary="Regenerate Backup Codes")
async def recover_mfa(
    request: MFARecoveryRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Spend one backup code to replace the whole set

    ## This is not the locked-out-of-login path
    It requires a full access token, which a user stuck at the MFA prompt does not have.
    **To log in without an authenticator**, call `POST /auth/verify-login` with the
    temporary token from `/auth/login` and a `backup_code` instead of a `totp_code`.

    What this endpoint is for is topping the set back up afterwards: a backup-code login
    consumes one code, and this is the only way to get a fresh set without disabling and
    re-enrolling MFA.

    ## Requires:
    - Valid access token in Authorization header
    - One unused backup code

    ## Returns:
    - Recovery result
    - A complete new set of backup codes. **Every previous code, including unused ones, is
      invalidated** — write the new set down before closing the response.
    """
    # Get current user
    token = credentials.credentials
    user = await auth_controller.get_current_user(token)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token"
        )
    
    return await mfa_controller.recover_mfa(user.id, request)


@router.get("/status", response_model=MFAStatusResponse, summary="Get MFA Status")
async def get_mfa_status(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Get MFA status for the current user
    
    Requires:
    - Valid access token in Authorization header
    
    Returns:
    - MFA status information
    """
    # Get current user
    token = credentials.credentials
    user = await auth_controller.get_current_user(token)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token"
        )
    
    return await mfa_controller.get_mfa_status(user.id)


@router.post("/admin/emergency-disable/{target_user_id}", summary="Emergency MFA Disable (admin)")
async def emergency_disable_mfa(
    target_user_id: str,
    request: Request,
    reason: Optional[str] = None,
    payload: Optional[EmergencyDisableRequest] = Body(default=None),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Emergency MFA disable by admin (for account recovery)

    This is the recovery path for a user who has lost the device holding their
    authenticator and exhausted their backup codes. It clears the secret, the enrolment
    flags and the backup codes, and resets that user's MFA rate limiting.

    ## Requires:
    - An access token holding `mfa:emergency_disable` (the `admin` and `superadmin` roles
      pass without it)
    - Target user ID in the path
    - A reason, either as a `?reason=` query parameter or as `{"reason": "..."}` in the body

    ## Returns:
    - Emergency disable result

    The action is written to the security audit log with both user IDs and the reason,
    whether it succeeds or is refused.
    """
    # Get current admin user
    token = credentials.credentials
    admin_user = await auth_controller.get_current_user(token)

    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token"
        )

    client_ip = get_client_ip_or_none(request)
    user_agent = request.headers.get("user-agent")

    try:
        _require_mfa_admin(admin_user, _ACTION_EMERGENCY_DISABLE)
    except HTTPException:
        get_audit_logger().log_security_event(
            service="auth",
            event_type="mfa_emergency_disable_denied",
            message=f"Emergency MFA disable refused for {admin_user.username}",
            user_id=admin_user.id,
            username=admin_user.username,
            target_user_id=target_user_id,
            client_ip=client_ip,
            user_agent=user_agent,
            failure_reason="missing_permission",
            success=False,
        )
        raise

    reason_text = (payload.reason if payload else None) or reason
    if not reason_text or not reason_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A reason is required for an emergency MFA disable",
        )

    result = await mfa_controller.emergency_disable_mfa(
        target_user_id, admin_user.id, reason_text.strip()
    )

    # Removing somebody's second factor is exactly the action an attacker with an admin
    # session wants, so it is logged unconditionally rather than only on the error path.
    get_audit_logger().log_security_event(
        service="auth",
        event_type="mfa_emergency_disable",
        message=(
            f"MFA emergency disabled for {result.get('target_user')} "
            f"by admin {admin_user.username}"
        ),
        user_id=admin_user.id,
        username=admin_user.username,
        target_user_id=target_user_id,
        target_username=result.get("target_user"),
        reason=reason_text.strip(),
        client_ip=client_ip,
        user_agent=user_agent,
        success=True,
    )

    return result


@router.post("/admin/reset-rate-limit/{target_user_id}", summary="Reset MFA Rate Limit (admin)")
async def reset_mfa_rate_limit(
    target_user_id: str,
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Reset MFA rate limiting for a user (admin function)

    Clears the failed-attempt window, any active lockout and the escalating strike count,
    so a user locked out by repeated bad codes can try again immediately. It does not
    change the user's MFA enrolment.

    ## Requires:
    - An access token holding `mfa:reset_rate_limit` (the `admin` and `superadmin` roles
      pass without it)
    - Target user ID

    ## Returns:
    - Reset result
    """
    # Get current admin user
    token = credentials.credentials
    admin_user = await auth_controller.get_current_user(token)

    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token"
        )

    client_ip = get_client_ip_or_none(request)
    user_agent = request.headers.get("user-agent")

    try:
        _require_mfa_admin(admin_user, _ACTION_RESET_RATE_LIMIT)
    except HTTPException:
        get_audit_logger().log_security_event(
            service="auth",
            event_type="mfa_rate_limit_reset_denied",
            message=f"MFA rate limit reset refused for {admin_user.username}",
            user_id=admin_user.id,
            username=admin_user.username,
            target_user_id=target_user_id,
            client_ip=client_ip,
            user_agent=user_agent,
            failure_reason="missing_permission",
            success=False,
        )
        raise

    from ....services.mfa_rate_limiter import mfa_rate_limiter
    mfa_rate_limiter.reset_user(target_user_id)

    get_audit_logger().log_security_event(
        service="auth",
        event_type="mfa_rate_limit_reset",
        message=f"MFA rate limiting reset for {target_user_id} by admin {admin_user.username}",
        user_id=admin_user.id,
        username=admin_user.username,
        target_user_id=target_user_id,
        client_ip=client_ip,
        user_agent=user_agent,
        success=True,
    )

    return {
        "reset": True,
        "message": f"MFA rate limiting reset for user {target_user_id}",
        "admin_user": admin_user.username,
        "timestamp": datetime.now().isoformat()
    }
