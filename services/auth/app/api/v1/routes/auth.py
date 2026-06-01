"""
Authentication routes using repository pattern
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Dict, Any, Union
from ....controllers.auth_controller import auth_controller
from ....schemas.auth import (
    LoginRequest, LoginResponse, RefreshTokenRequest, LogoutRequest,
    ChangePasswordRequest, UserResponse, MessageResponse,
    TemporaryTokenResponse, VerifyLoginRequest
)
from ....schemas.password_policy import (
    PasswordValidationRequest, PasswordValidationResponse, 
    PolicyConfigurationResponse, AdminUnlockUserRequest, AdminResetPasswordRequest
)
from ....core.dto import AuthUserDTO
from libs.core.authz import get_current_user, UserPrincipal
from ....services.password_policy import PasswordPolicyService
from ....utils.activity_logger import (
    log_auth_activity
)
from ....core.dependencies import get_audit_logger
from libs.audit_queue.simple_logger import AuditLogger

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Security scheme
security = HTTPBearer()

# Local dependency injection to avoid circular imports
def get_password_policy_service() -> PasswordPolicyService:
    """Local dependency injection for password policy service"""
    return PasswordPolicyService()


async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)) -> AuthUserDTO:
    """Get current user from token"""
    token = credentials.credentials
    user = await auth_controller.get_current_user(token)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    return user


@router.post(
    "/login",
    responses={
        200: {
            "description": "Login successful",
            "content": {
                "application/json": {
                    "examples": {
                        "non_mfa_user": {
                            "summary": "Non-MFA User - Direct Login",
                            "description": "User without MFA gets immediate access tokens",
                            "value": {
                                "user": {
                                    "first_name": "John",
                                    "last_name": "Doe", 
                                    "last_login": "2025-01-15T10:30:00Z"
                                },
                                "tokens": {
                                    "access_token": "eyJhbGciOiJIUzI1NiIs...",
                                    "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
                                    "token_type": "bearer",
                                    "expires_in": 1800,
                                    "refresh_expires_in": 604800,
                                    "expires_at": "2025-01-15T11:00:00Z",
                                    "refresh_expires_at": "2025-01-22T10:30:00Z"
                                }
                            }
                        },
                        "mfa_user": {
                            "summary": "MFA User - Temporary Token",
                            "description": "User with MFA gets temporary token for verification",
                            "value": {
                                "temporary_token": "temp_eyJhbGciOiJIUzI1NiIs...",
                                "mfa_required": True,
                                "expires_in": 300,
                                "message": "Please provide TOTP code to complete login"
                            }
                        }
                    }
                }
            }
        },
        401: {
            "description": "Invalid credentials",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid username or password"}
                }
            }
        }
    }
)
async def login(login_data: LoginRequest, request: Request, audit_logger: AuditLogger = Depends(get_audit_logger)) -> Union[LoginResponse, TemporaryTokenResponse]:
    """
    User login endpoint with support for both traditional and MFA flows
    
    ## Behavior:
    
    ### For users WITHOUT MFA enabled:
    - Returns complete `LoginResponse` with access and refresh tokens immediately
    - User can proceed with authenticated requests right away
    
    ### For users WITH MFA enabled:
    - Returns `TemporaryTokenResponse` with a temporary token
    - User must call `/verify-login` with the temporary token + TOTP code
    - Temporary token expires in 5 minutes
    
    ## Request:
    - `username`: Username or email address
    - `password`: User password
    
    ## Response Types:
    1. **LoginResponse** (non-MFA users): Contains user info and tokens
    2. **TemporaryTokenResponse** (MFA users): Contains temporary token and instructions
    
    ## Example Flows:
    
    ### Non-MFA Flow:
    ```
    POST /login → LoginResponse (complete)
    ```
    
    ### MFA Flow:
    ```
    POST /login → TemporaryTokenResponse
    POST /verify-login → LoginResponse (complete)
    ```
    """
    try:
        result = await auth_controller.login(login_data, request)
        
        # Log successful login attempt with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        if "temporary_token" in result:
            # MFA required - log once at route level
            audit_logger.log_security_event(
                service="auth",
                event_type="mfa_required",
                message=f"Multi-factor authentication required: {login_data.username}",
                client_ip=client_ip,
                username=login_data.username,
                login_step="mfa_required",
                user_agent=user_agent
            )
            return TemporaryTokenResponse(**result)
        else:
            # Non-MFA user - log successful login
            user_info = result.get("user", {})
            audit_logger.log_security_event(
                service="auth",
                event_type="login_success",
                message=f"User login successful: {login_data.username}",
                user_id=user_info.get("id", "unknown"),
                client_ip=client_ip,
                username=login_data.username,
                mfa_required=False,
                user_agent=user_agent,
                login_method="password"
            )
            audit_logger.log_user_action(
                service="auth",
                user_id=user_info.get("id", "unknown"),
                action="login",
                message=f"User logged in: {login_data.username}",
                client_ip=client_ip,
                username=login_data.username,
                user_agent=user_agent,
                success=True
            )
            return LoginResponse(**result)
            
    except HTTPException as e:
        # Log failed login attempt with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_security_event(
            service="auth",
            event_type="login_failed",
            message=f"Login attempt failed: {login_data.username}",
            success=False,
            client_ip=client_ip,
            username=login_data.username,
            failure_reason=str(e.detail),
            user_agent=user_agent
        )
        raise


@router.post(
    "/verify-login", 
    response_model=LoginResponse,
    responses={
        200: {
            "description": "Login verification successful",
            "content": {
                "application/json": {
                    "example": {
                        "user": {
                            "first_name": "John",
                            "last_name": "Doe",
                            "last_login": "2025-01-15T10:35:00Z"
                        },
                        "tokens": {
                            "access_token": "eyJhbGciOiJIUzI1NiIs...",
                            "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
                            "token_type": "bearer",
                            "expires_in": 1800,
                            "refresh_expires_in": 604800,
                            "expires_at": "2025-01-15T11:05:00Z",
                            "refresh_expires_at": "2025-01-22T10:35:00Z"
                        }
                    }
                }
            }
        },
        401: {
            "description": "Invalid temporary token or TOTP code",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid temporary token or TOTP code"}
                }
            }
        }
    }
)
async def verify_login(verify_data: VerifyLoginRequest, request: Request, audit_logger: AuditLogger = Depends(get_audit_logger)):
    """
    Complete MFA login verification (Step 2 of MFA flow)
    
    ## Purpose:
    This endpoint completes the two-factor authentication process for users with MFA enabled.
    
    ## When to use:
    - Call this endpoint after receiving a `TemporaryTokenResponse` from `/login`
    - Only required for users with MFA enabled
    - Must be called within 5 minutes of receiving the temporary token
    
    ## Request:
    - `temporary_token`: The temporary token received from `/login` endpoint
    - `totp_code`: 6-digit TOTP code from authenticator app (required if user has MFA)
    
    ## Response:
    Returns complete `LoginResponse` with access and refresh tokens, same as non-MFA login
    
    ## Example Flow:
    ```
    1. POST /login → {temporary_token: "temp_xyz...", mfa_required: true}
    2. User opens authenticator app, gets TOTP code: "123456"
    3. POST /verify-login → {user: {...}, tokens: {...}}
    4. Use access_token for authenticated requests
    ```
    
    ## Error Cases:
    - **401**: Temporary token expired (>5 minutes) or invalid
    - **401**: Invalid or expired TOTP code
    - **401**: User doesn't have MFA enabled but provided TOTP code
    
    ## Security Notes:
    - Temporary tokens are single-use and expire in 5 minutes
    - TOTP codes have ±1 time window tolerance (configurable)
    - Failed attempts are logged for security monitoring
    """
    try:
        result = await auth_controller.verify_login(verify_data, request)
        
        # Log successful MFA verification with secure audit logging
        user_info = result.get("user", {})
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        # Complete MFA login success - single comprehensive log
        audit_logger.log_security_event(
            service="auth",
            event_type="login_success",
            message=f"User authentication successful with MFA: {user_info.get('username', 'unknown')}",
            user_id=user_info.get("id", "unknown"),
            client_ip=client_ip,
            login_method="password+mfa",
            mfa_used=True,
            verification_method="totp",
            user_agent=user_agent,
            success=True
        )
        
        return result
        
    except HTTPException as e:
        # Log failed MFA verification with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_security_event(
            service="auth",
            event_type="mfa_verification_failed",
            message="MFA verification failed",
            success=False,
            client_ip=client_ip,
            verification_method="totp",
            failure_reason="invalid_token_or_code",
            user_agent=user_agent,
            error_details=str(e.detail)
        )
        raise


@router.post("/refresh", response_model=Dict[str, Any])
async def refresh_token(refresh_data: RefreshTokenRequest, request: Request, audit_logger: AuditLogger = Depends(get_audit_logger)):
    """
    Refresh access token endpoint
    
    Returns:
        New access token
    """
    try:
        result = await auth_controller.refresh_token(refresh_data, request)
        
        # Log successful token refresh with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_user_action(
            service="auth",
            user_id="token_refresh",  # User ID extracted from token in service layer
            action="token_refresh",
            message="Access token refreshed successfully",
            client_ip=client_ip,
            token_type="access_token",
            user_agent=user_agent,
            success=True
        )
        
        return result
        
    except HTTPException as e:
        # Log failed token refresh with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="TokenRefreshError",
            error_message=f"Token refresh failed: {str(e.detail)}",
            client_ip=client_ip,
            token_type="access_token",
            user_agent=user_agent,
            error_code=e.status_code
        )
        raise


@router.post("/logout", response_model=MessageResponse)
async def logout(
    logout_data: LogoutRequest,
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    audit_logger: AuditLogger = Depends(get_audit_logger)):
    """
    User logout endpoint
    
    Returns:
        Logout confirmation
    """
    try:
        # Get user info before logout for logging
        from ....services.jwt_service import jwt_service
        user_info = jwt_service.get_user_session_info(credentials.credentials)
        
        result = await auth_controller.logout(
            access_token=credentials.credentials,
            refresh_token=logout_data.refresh_token,
            request=request
        )
        
        # Log successful logout with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_security_event(
            service="auth",
            event_type="logout_success",
            message=f"User logout successful: {user_info.get('username', 'unknown')}",
            user_id=user_info.get("user_id", "unknown"),
            client_ip=client_ip,
            username=user_info.get("username", "unknown"),
            session_id=user_info.get("session_id"),
            user_agent=user_agent,
            success=True
        )

        audit_logger.log_user_action(
                service="auth",
                user_id=user_info.get("id", "unknown"),
                action="logout",
                message=f"User logged out: {user_info.get('username', 'unknown')}",
                client_ip=client_ip,
                username=user_info.get("username", "unknown"),
                user_agent=user_agent,
                success=True
            )
        
        return result
        
    except HTTPException as e:
        # Log failed logout attempt with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="LogoutError",
            error_message=f"Logout failed: {str(e.detail)}",
            client_ip=client_ip,
            failure_reason="invalid_token",
            user_agent=user_agent,
            error_code=e.status_code
        )
        raise


@router.get(
    "/me", 
    response_model=UserResponse,
    dependencies=[Security(security)],
    responses={
        200: {
            "description": "Current user information retrieved successfully"
        },
        401: {
            "description": "Unauthorized - Invalid or missing JWT token",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid access token"}
                }
            }
        }
    }
)
async def get_current_user_info(
    request: Request,
    current_user: AuthUserDTO = Security(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get current user information
    
    ## 🔐 JWT Authentication Required:
    - **Authorization Header**: `Bearer <jwt_token>`
    - **Any Authenticated User**: No special permissions required
    
    Returns:
        Current user profile information including roles and permissions
    """
    try:
        # Log user profile access with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_user_action(
            service="auth",
            user_id=current_user.id,
            action="profile_access",
            message=f"User accessed profile information: {current_user.username}",
            client_ip=client_ip,
            username=current_user.username,
            profile_fields_requested=["basic_info", "roles", "permissions"],
            user_agent=user_agent,
            success=True
        )
        
        # Convert AuthUserDTO to UserResponse
        return UserResponse(
            id=current_user.id,
            username=current_user.username,
            email=current_user.email,
            is_active=current_user.is_active,
            is_verified=True,  # Assuming verified if they have a valid token
            roles=current_user.roles,
            permissions=current_user.permissions,
            created_at=current_user.created_at,
            last_login=current_user.last_login,
            first_name=current_user.profile.get("first_name") if current_user.profile else None,
            last_name=current_user.profile.get("last_name") if current_user.profile else None,
            phone=current_user.profile.get("phone") if current_user.profile else None,
            address=current_user.profile.get("address") if current_user.profile else None,
            preferences=current_user.profile.get("preferences") if current_user.profile else None
        )
    except Exception as e:
        # Log failed profile access
        await log_auth_activity(
            audit_logger=audit_logger,
            activity_type="profile_access",
            request=request,
            user_id=getattr(current_user, 'id', 'unknown'),
            username=getattr(current_user, 'username', 'unknown'),
            success=False,
            error_message=str(e),
            details={
                "error_type": type(e).__name__,
                "access_failed": True
            }
        )
        
        print(f"DEBUG: Exception in /me endpoint: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user information"
        ) from e


@router.post(
    "/change-password", 
    response_model=Dict[str, Any],
    responses={
        200: {
            "description": "Password changed successfully",
            "content": {
                "application/json": {
                    "example": {"message": "Password changed successfully"}
                }
            }
        },
        400: {
            "description": "Invalid current password or password requirements not met",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid current password or password requirements not met"}
                }
            }
        },
        401: {
            "description": "Unauthorized - Invalid or missing JWT token",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid access token"}
                }
            }
        },
        403: {
            "description": "Forbidden - Admin permissions required",
            "content": {
                "application/json": {
                    "example": {"detail": "Missing permissions: users:write"}
                }
            }
        }
    }
)
async def change_password(
    password_data: ChangePasswordRequest, 
    request: Request,
    current_user: AuthUserDTO = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Change user password endpoint (Admin Only)
    
    ## 🔐 JWT Authentication Required:
    - **Authorization Header**: `Bearer <jwt_token>` 
    - **Admin Permissions**: Must have `users:write` permission
    - **Self-Service**: Admins can change their own passwords
    - **Super-Admin**: Users with `*` permission get automatic access
    
    ## Requirements:
    - **JWT Token**: Valid access token in `Authorization: Bearer <token>` header
    - **Admin Role**: Must have admin permissions (`users:write`)
    - **Current Password**: For security verification
    - **New Password**: Must meet all policy requirements
    
    ## Request:
    - `current_password`: User's current password
    - `new_password`: New password (must meet policy requirements)
    
    ## Password Policy:
    - Minimum length requirements
    - Character complexity requirements  
    - Cannot reuse recent passwords
    - Must be different from current password
    
    ## Security Features:
    - **Admin-only access** for enhanced security
    - Validates current password before change
    - Enforces password policy rules
    - Tracks password history to prevent reuse
    - Logs password change events for audit
    - Resets failed login attempts on successful change
    
    ## Use Cases:
    - **Admin Self-Service**: Admins changing their own passwords
    - **Account Recovery**: Admin-assisted password changes
    - **Security Enforcement**: Forced password changes for security reasons
    
    Returns:
        Password change confirmation message
    """
    try:
        result = await auth_controller.change_password(
            user_id=current_user.id,
            password_data=password_data,
            request=request
        )
        
        # Log successful password change with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_security_event(
            service="auth",
            event_type="password_change_success",
            message=f"Password changed successfully by admin: {current_user.username}",
            user_id=current_user.id,
            client_ip=client_ip,
            username=current_user.username,
            change_method="admin_endpoint",
            admin_permissions=current_user.permissions,
            user_agent=user_agent,
            success=True
        )
        
        return result
        
    except HTTPException as e:
        # Log failed password change with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="PasswordChangeError",
            error_message=f"Password change failed: {str(e.detail)}",
            user_id=current_user.id,
            client_ip=client_ip,
            username=current_user.username,
            change_method="admin_endpoint",
            failure_reason="validation_or_permission_error",
            user_agent=user_agent,
            error_code=e.status_code
        )
        raise


@router.post(
    "/validate-password",
    response_model=PasswordValidationResponse,
    responses={
        200: {
            "description": "Password validation result",
            "content": {
                "application/json": {
                    "examples": {
                        "valid_password": {
                            "summary": "Valid Password",
                            "description": "Password meets all policy requirements",
                            "value": {
                                "is_valid": True,
                                "strength_score": 85,
                                "violations": [],
                                "suggestions": [],
                                "user_id": "user123"
                            }
                        },
                        "invalid_password": {
                            "summary": "Invalid Password",
                            "description": "Password fails policy requirements",
                            "value": {
                                "is_valid": False,
                                "strength_score": 45,
                                "violations": [
                                    "Password must be at least 8 characters long",
                                    "Password must contain at least one uppercase letter"
                                ],
                                "suggestions": [
                                    "Add uppercase letters",
                                    "Increase password length"
                                ],
                                "user_id": "user123"
                            }
                        }
                    }
                }
            }
        }
    }
)
async def validate_password(
    validation_request: PasswordValidationRequest,
    request: Request,
    password_service: PasswordPolicyService = Depends(get_password_policy_service),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> PasswordValidationResponse:
    """
    Validate password against policy requirements
    
    ## 🌐 Public Access (No JWT Required):
    This endpoint is publicly accessible to support registration flows and real-time validation.
    
    ## Purpose:
    This endpoint allows clients to validate passwords against the configured password policy 
    before submission, providing real-time feedback to users.
    
    ## Request:
    - `password`: The password to validate
    - `user_id`: (Optional) User ID for checking against password history
    
    ## Response:
    - `is_valid`: Boolean indicating if password meets all requirements
    - `strength_score`: Password strength score (0-100)
    - `violations`: List of policy violations
    - `suggestions`: List of improvement suggestions
    - `user_id`: User ID from request
    
    ## Use Cases:
    - **Registration**: Validate new user passwords
    - **Password Change**: Check new password before submission
    - **Real-time Feedback**: Show password strength as user types
    - **Admin Tools**: Validate bulk password imports
    
    ## Policy Checks:
    - Minimum/maximum length requirements
    - Character complexity (uppercase, lowercase, digits, special chars)
    - Common password detection
    - Password history (if user_id provided)
    - Dictionary word detection
    
    ## No Authentication Required:
    This endpoint is publicly accessible to support registration flows.
    """
    try:
        result = password_service.validate_password_strength(validation_request.password)
        
        # Log password validation with secure audit logging (public endpoint)
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_system_operation(
            service="auth",
            operation="password_validation",
            message="Password validation performed",
            client_ip=client_ip,
            validation_result=result.get("is_valid", False),
            strength_score=result.get("strength_score", 0),
            violation_count=len(result.get("violations", [])),
            user_id_provided=validation_request.user_id is not None,
            user_agent=user_agent,
            success=True
        )
        
        return PasswordValidationResponse(
            is_valid=result.get("is_valid", False),
            strength_score=result.get("strength_score", 0),
            violations=result.get("violations", []),
            suggestions=result.get("suggestions", []),
            user_id=validation_request.user_id
        )
    except Exception as e:
        # Log failed password validation with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="PasswordValidationError",
            error_message=f"Password validation failed: {str(e)}",
            client_ip=client_ip,
            validation_failed=True,
            exception_type=type(e).__name__,
            user_id_provided=validation_request.user_id is not None,
            user_agent=user_agent
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password validation failed"
        ) from e


@router.get(
    "/password-policy",
    response_model=PolicyConfigurationResponse,
    responses={
        200: {
            "description": "Current password policy configuration",
            "content": {
                "application/json": {
                    "example": {
                        "min_password_length": 8,
                        "require_uppercase": True,
                        "require_lowercase": True,
                        "require_numbers": True,
                        "require_special_chars": True,
                        "password_expiry_days": 90,
                        "password_history_count": 5,
                        "max_login_attempts": 5,
                        "lockout_duration_minutes": 15,
                        "lockout_window_hours": 24
                    }
                }
            }
        }
    }
)
async def get_password_policy(
    request: Request,
    password_service: PasswordPolicyService = Depends(get_password_policy_service),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> PolicyConfigurationResponse:
    """
    Get current password policy configuration
    
    ## 🌐 Public Access (No JWT Required):
    This endpoint is publicly accessible to help clients understand password requirements.
    
    ## Purpose:
    Returns the current password policy settings to help clients understand 
    requirements and build appropriate validation UI.
    
    ## Response:
    Complete password policy configuration including:
    - Length requirements (min/max)
    - Character complexity requirements
    - Special character requirements
    - Password expiry settings
    - Login attempt limits
    - Account lockout settings
    
    ## Use Cases:
    - **Frontend Validation**: Build client-side password validation
    - **User Guidance**: Show password requirements to users
    - **Admin Interface**: Display current policy settings
    - **API Integration**: Understand password requirements for automated tools
    
    ## No Authentication Required:
    This endpoint is publicly accessible to support registration and login flows.
    """
    try:
        # Log policy access with secure audit logging (public endpoint)
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_system_operation(
            service="auth",
            operation="password_policy_access",
            message="Password policy configuration accessed",
            client_ip=client_ip,
            policy_accessed=True,
            user_agent=user_agent,
            success=True
        )
        
        return PolicyConfigurationResponse(
            min_password_length=password_service.min_password_length,
            require_uppercase=password_service.require_uppercase,
            require_lowercase=password_service.require_lowercase,
            require_numbers=password_service.require_numbers,
            require_special_chars=password_service.require_special_chars,
            password_expiry_days=password_service.password_expiry_days,
            password_history_count=password_service.password_history_count,
            max_login_attempts=password_service.max_login_attempts,
            lockout_duration_minutes=password_service.lockout_duration_minutes,
            lockout_window_hours=password_service.lockout_window_hours
        )
    except Exception as e:
        # Log failed policy access with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="PasswordPolicyAccessError",
            error_message=f"Failed to retrieve password policy: {str(e)}",
            client_ip=client_ip,
            policy_access_failed=True,
            exception_type=type(e).__name__,
            user_agent=user_agent
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve password policy"
        ) from e


# Admin Routes
@router.post(
    "/admin/unlock-user",
    response_model=Dict[str, Any],
    responses={
        200: {
            "description": "User account unlocked successfully",
            "content": {
                "application/json": {
                    "example": {
                        "message": "User account unlocked successfully"
                    }
                }
            }
        },
        400: {"description": "Failed to unlock user account"},
        403: {"description": "Admin permissions required"},
        404: {"description": "User not found"},
        500: {"description": "Internal server error"}
    }
)
async def admin_unlock_user(
    unlock_request: "AdminUnlockUserRequest",
    request: Request,
    current_user: AuthUserDTO = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> Dict[str, Any]:
    """
    Admin unlock user account
    
    This endpoint allows administrators to unlock user accounts that have been
    locked due to too many failed login attempts.
    
    ## Admin Permissions Required:
    - Must have "admin" role OR "user:unlock" permission
    
    ## Security Features:
    - **Admin-only access** for enhanced security
    - Logs unlock events for audit trail
    - Validates admin permissions before unlock
    - Resets failed login attempts
    - Removes lockout status
    
    ## Use Cases:
    - **Account Recovery**: Unlock accounts after failed login attempts
    - **User Support**: Help users regain access to their accounts
    - **Security Management**: Administrative control over account lockouts
    
    Returns:
        Unlock confirmation message
    """
    try:
        from ....schemas.password_policy import AdminUnlockUserRequest
        
        result = await auth_controller.admin_unlock_user(
            user_id=unlock_request.user_id,
            admin_user=current_user,
            request=request
        )
        
        # Log successful unlock with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_security_event(
            service="auth",
            event_type="admin_unlock_success",
            message=f"User account unlocked by admin: {current_user.username}",
            user_id=unlock_request.user_id,
            client_ip=client_ip,
            username=current_user.username,
            admin_action="unlock_user",
            target_user_id=unlock_request.user_id,
            admin_permissions=current_user.permissions,
            user_agent=user_agent,
            success=True
        )
        
        return result
        
    except HTTPException as e:
        # Log failed unlock with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="AdminUnlockError",
            error_message=f"Failed to unlock user account: {str(e.detail)}",
            client_ip=client_ip,
            admin_id=current_user.id if current_user else None,
            target_user_id=unlock_request.user_id,
            error_code=e.status_code,
            user_agent=user_agent
        )
        
        raise e
    except Exception as e:
        # Log system error
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="AdminUnlockSystemError",
            error_message=f"System error during user unlock: {str(e)}",
            client_ip=client_ip,
            admin_id=current_user.id if current_user else None,
            target_user_id=unlock_request.user_id,
            exception_type=type(e).__name__,
            user_agent=user_agent
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        ) from e


@router.post(
    "/admin/reset-password",
    response_model=Dict[str, Any],
    responses={
        200: {
            "description": "User password reset successfully",
            "content": {
                "application/json": {
                    "example": {
                        "message": "User password reset and account unlocked successfully"
                    }
                }
            }
        },
        400: {"description": "Failed to reset user password"},
        403: {"description": "Admin permissions required"},
        404: {"description": "User not found"},
        500: {"description": "Internal server error"}
    }
)
async def admin_reset_password(
    reset_request: "AdminResetPasswordRequest",
    request: Request,
    current_user: AuthUserDTO = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> Dict[str, Any]:
    """
    Admin reset user password and unlock account
    
    This endpoint allows administrators to reset user passwords and automatically
    unlock their accounts. This is useful for account recovery scenarios.
    
    ## Admin Permissions Required:
    - Must have "admin" role OR "user:reset_password" permission
    
    ## Security Features:
    - **Admin-only access** for enhanced security
    - Enforces password policy rules
    - Tracks password history to prevent reuse
    - Logs password reset events for audit
    - Automatically unlocks account after reset
    - Resets failed login attempts
    
    ## Use Cases:
    - **Account Recovery**: Reset passwords for locked accounts
    - **User Support**: Help users who forgot their passwords
    - **Security Enforcement**: Force password changes for security reasons
    
    Returns:
        Password reset confirmation message
    """
    try:
        from ....schemas.password_policy import AdminResetPasswordRequest
        
        result = await auth_controller.admin_reset_password(
            user_id=reset_request.user_id,
            new_password=reset_request.new_password,
            admin_user=current_user,
            request=request
        )
        
        # Log successful password reset with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_security_event(
            service="auth",
            event_type="admin_password_reset_success",
            message=f"Password reset by admin: {current_user.username}",
            user_id=reset_request.user_id,
            client_ip=client_ip,
            username=current_user.username,
            admin_action="reset_password",
            target_user_id=reset_request.user_id,
            admin_permissions=current_user.permissions,
            user_agent=user_agent,
            success=True
        )
        
        return result
        
    except HTTPException as e:
        # Log failed password reset with secure audit logging
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="AdminPasswordResetError",
            error_message=f"Failed to reset user password: {str(e.detail)}",
            client_ip=client_ip,
            admin_id=current_user.id if current_user else None,
            target_user_id=reset_request.user_id,
            error_code=e.status_code,
            user_agent=user_agent
        )
        
        raise e
    except Exception as e:
        # Log system error
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="AdminPasswordResetSystemError",
            error_message=f"System error during password reset: {str(e)}",
            client_ip=client_ip,
            admin_id=current_user.id if current_user else None,
            target_user_id=reset_request.user_id,
            exception_type=type(e).__name__,
            user_agent=user_agent
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        ) from e


@router.get(
    "/admin/lockout-status/{user_id}",
    response_model=Dict[str, Any],
    responses={
        200: {
            "description": "User lockout status retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "is_locked": True,
                        "requires_admin_unlock": True,
                        "remaining_attempts": 0,
                        "lockout_until": "2024-01-01T12:00:00Z",
                        "user_id": "user123"
                    }
                }
            }
        },
        403: {"description": "Admin permissions required"},
        404: {"description": "User not found"},
        500: {"description": "Internal server error"}
    }
)
async def get_lockout_status(
    user_id: str,
    current_user: AuthUserDTO = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> Dict[str, Any]:
    """
    Get user lockout status
    
    This endpoint allows administrators to check the lockout status of a user account,
    including whether it's locked, requires admin unlock, and remaining attempts.
    
    ## Admin Permissions Required:
    - Must have "admin" role OR "user:view_status" permission
    
    ## Response Fields:
    - **is_locked**: Whether the account is currently locked
    - **requires_admin_unlock**: Whether admin intervention is required
    - **remaining_attempts**: Number of login attempts remaining
    - **lockout_until**: When the lockout expires (if applicable)
    - **user_id**: The user ID that was checked
    
    ## Use Cases:
    - **User Support**: Check account status for support requests
    - **Security Monitoring**: Monitor account lockout patterns
    - **Administrative Oversight**: Manage user account security
    
    Returns:
        User lockout status information
    """
    try:
        result = await auth_controller.get_lockout_status(
            user_id=user_id,
            admin_user=current_user
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="auth",
            error_name="LockoutStatusError",
            error_message=f"System error getting lockout status: {str(e)}",
            admin_id=current_user.id if current_user else None,
            target_user_id=user_id,
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        ) from e