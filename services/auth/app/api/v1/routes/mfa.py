"""
MFA API routes
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ....controllers.mfa_controller import mfa_controller
from ....controllers.auth_controller import auth_controller
from ....utils.activity_logger import log_mfa_activity
from datetime import datetime

# Import secure audit logging
from ....core.dependencies import get_audit_logger
from ....core.dto import AuthUserDTO
from ....schemas.mfa import (
    EnableMFARequest, EnableMFAResponse, VerifyMFARequest, VerifyMFAResponse,
    DisableMFARequest, DisableMFAResponse, MFARecoveryRequest, MFARecoveryResponse,
    MFAStatusResponse
)

# Create router
router = APIRouter(prefix="/mfa", tags=["Multi-Factor Authentication"])

# Security scheme
security = HTTPBearer()


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
        client_ip = request.client.host if request.client else None
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
        client_ip = request.client.host if request.client else None
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
        client_ip = request.client.host if request.client else None
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
        client_ip = request.client.host if request.client else None
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
        client_ip = request.client.host if request.client else None
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
        client_ip = request.client.host if request.client else None
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


@router.post("/recover", response_model=MFARecoveryResponse, summary="Recover MFA")
async def recover_mfa(
    request: MFARecoveryRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Recover MFA using backup code
    
    Requires:
    - Valid access token in Authorization header
    - Valid backup code
    
    Returns:
    - Recovery result
    - New backup codes if successful
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


@router.post("/admin/emergency-disable/{target_user_id}")
async def emergency_disable_mfa(
    target_user_id: str,
    reason: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Emergency MFA disable by admin (for account recovery)
    
    Requires:
    - Valid admin access token
    - Target user ID
    - Reason for emergency disable
    
    Returns:
    - Emergency disable result
    """
    # Get current admin user
    token = credentials.credentials
    admin_user = await auth_controller.get_current_user(token)
    
    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token"
        )
    
    # Check admin permissions (require admin role or specific permission)
    if "admin" not in admin_user.roles and "mfa:emergency_disable" not in admin_user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin permissions required for emergency MFA disable"
        )
    
    return await mfa_controller.emergency_disable_mfa(target_user_id, admin_user.id, reason)


@router.post("/admin/reset-rate-limit/{target_user_id}")
async def reset_mfa_rate_limit(
    target_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """
    Reset MFA rate limiting for a user (admin function)
    
    Requires:
    - Valid admin access token
    - Target user ID
    
    Returns:
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
    
    # Check admin permissions
    if "admin" not in admin_user.roles and "mfa:reset_rate_limit" not in admin_user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin permissions required for rate limit reset"
        )
    
    from ....services.mfa_rate_limiter import mfa_rate_limiter
    mfa_rate_limiter.reset_user(target_user_id)
    
    return {
        "reset": True,
        "message": f"MFA rate limiting reset for user {target_user_id}",
        "admin_user": admin_user.username,
        "timestamp": datetime.now().isoformat()
    } 
