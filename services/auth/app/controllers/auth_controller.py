"""
Authentication controller using repository pattern
"""

import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status, Request
from services.auth.app.services.auth_service import auth_service
from services.auth.app.schemas.auth import LoginRequest, RefreshTokenRequest, ChangePasswordRequest, VerifyLoginRequest
from services.auth.app.core.dto import AuthUserDTO
from datetime import datetime
import sys
import os

# Add project root to path for audit queue import
project_root = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..')
if project_root not in sys.path:
    sys.path.append(project_root)

from libs.audit_queue.simple_logger import AuditLogger
from ..core.dependencies import get_audit_logger

logger = logging.getLogger(__name__)


class AuthController:
    """Authentication controller using repository pattern"""
    
    def __init__(self):
        self.auth_service = auth_service
        self.audit_logger : AuditLogger = get_audit_logger()
    
    def _extract_request_info(self, request: Request) -> Dict[str, Any]:
        """Extract request information for audit logging"""
        return {
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "method": request.method,
            "path": request.url.path,
            "timestamp": request.headers.get("x-request-timestamp")
        }
    
    async def login(self, login_data: LoginRequest, request: Request) -> Dict[str, Any]:
        """
        Handle user login
        
        Args:
            login_data: Login credentials
            request: FastAPI request object
            
        Returns:
            Login response with tokens
        """
        try:
            # Extract request info for audit
            request_info = self._extract_request_info(request)
            
            # Attempt login
            result = await self.auth_service.login(
                username=login_data.username,
                password=login_data.password,
                request_info=request_info
            )
            
            if not result:
                # Log failed login attempt with PII protection
                self.await audit_logger.log_security_event(
                    service="auth",
                    event_type="login_failed",
                    message=f"Failed login attempt for user",
                    user_id=login_data.username,  # Will be sanitized
                    client_ip=request_info.get("ip_address"),  # Will be masked
                    success=False,
                    username=login_data.username,  # Will be sanitized
                    user_agent=request_info.get("user_agent"),
                    failure_reason="Invalid credentials",
                    login_method="password"
                )
                
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid username or password"
                )
            
            # Check if this is a temporary token response (MFA enabled user)
            if "temporary_token" in result:
                # MFA required logging handled at route layer to avoid duplication
                return result
            else:
                # Log successful login without MFA
                access_token = result.get("tokens", {}).get("access_token")
                session_id = await self._extract_session_id(access_token) if access_token else None
                
                self.await audit_logger.log_security_event(
                    service="auth",
                    event_type="login_success",
                    message="User authentication successful",
                    user_id=result.get("user", {}).get("id") or login_data.username,
                    client_ip=request_info.get("ip_address"),  # Will be masked
                    success=True,
                    username=login_data.username,  # Will be sanitized
                    user_email=result.get("user", {}).get("email"),  # Will be sanitized
                    user_agent=request_info.get("user_agent"),
                    login_method="password",
                    mfa_used=False,
                    session_id=session_id,
                    user_roles=result.get("user", {}).get("roles", []),
                    request_id=request_info.get("timestamp")
                )
                return result
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Login controller error: %s", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )
    
    async def _extract_session_id(self, token: str) -> Optional[str]:
        """Extract session ID from JWT token"""
        try:
            from services.auth.app.services.jwt_service import jwt_service
            return await jwt_service.get_session_id(token)
        except Exception:
            return None
    
    async def verify_login(self, verify_data: "VerifyLoginRequest", request: Request) -> Dict[str, Any]:
        """
        Verify login with temporary token and optional TOTP code
        
        Args:
            verify_data: Verify login request data
            request: FastAPI request object
            
        Returns:
            Login response with tokens
        """
        try:
            # Extract request info for audit
            request_info = self._extract_request_info(request)
            
            # Attempt verify login
            result = await self.auth_service.verify_login(
                temporary_token=verify_data.temporary_token,
                totp_code=verify_data.totp_code,
                backup_code=verify_data.backup_code,
                request_info=request_info
            )
            
            if not result:
                # Log failed MFA verification
                self.await audit_logger.log_security_event(
                    service="auth",
                    event_type="mfa_verification_failed",
                    message="MFA verification failed",
                    client_ip=request_info.get("ip_address"),
                    success=False,
                    user_agent=request_info.get("user_agent"),
                    failure_reason="Invalid temporary token or TOTP code",
                    login_method="password+mfa"
                )
                
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid temporary token or TOTP code"
                )
            
            # MFA login success logging handled at route layer to avoid duplication
            
            return result
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Verify login controller error: %s", e)
            
            # Log system error
            self.await audit_logger.log_error_event(
                service="auth",
                error_name="VerifyLoginError",
                error_message="System error during MFA verification",
                client_ip=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                operation="verify_login"
            )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )
    
    async def refresh_token(self, refresh_data: RefreshTokenRequest, request: Request) -> Dict[str, Any]:
        """
        Handle token refresh
        
        Args:
            refresh_data: Refresh token data
            request: FastAPI request object
            
        Returns:
            New access token
        """
        try:
            # Extract request info for audit
            request_info = self._extract_request_info(request)
            
            # Attempt token refresh
            result = await self.auth_service.refresh_token(
                refresh_token=refresh_data.refresh_token,
                request_info=request_info
            )
            
            if not result:
                # Log failed token refresh
                self.await audit_logger.log_security_event(
                    service="auth",
                    event_type="token_refresh_failed",
                    message="Token refresh failed",
                    client_ip=request_info.get("ip_address"),
                    success=False,
                    user_agent=request_info.get("user_agent"),
                    failure_reason="Invalid or expired refresh token"
                )
                
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired refresh token"
                )
            
            return result
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Token refresh controller error: {e}")
            
            # Log system error
            self.await audit_logger.log_error_event(
                service="auth",
                error_name="TokenRefreshError",
                error_message="System error during token refresh",
                client_ip=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                operation="token_refresh"
            )
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )
    
    async def logout(self, access_token: str, refresh_token: Optional[str], request: Request) -> Dict[str, Any]:
        """
        Handle user logout
        
        Args:
            access_token: JWT access token
            refresh_token: JWT refresh token (optional)
            request: FastAPI request object
            
        Returns:
            Logout confirmation
        """
        try:
            # Extract request info for audit
            request_info = self._extract_request_info(request)
            
            # Attempt logout
            self.await audit_logger.log_user_action(
                service="auth",
                user_id=await self._extract_session_id(access_token),
                action="Log Out",
                message="User logged out",
                client_ip=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                success=True
            )
            success = await self.auth_service.logout(
                access_token=access_token,
                refresh_token=refresh_token,
                request_info=request_info
            )
            
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid token"
                )
            
            return {"message": "Logged out successfully"}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Logout controller error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )
    
    async def get_current_user(self, token: str) -> Optional[AuthUserDTO]:
        """
        Get current user from token
        
        Args:
            token: JWT access token
            
        Returns:
            Current user information
        """
        try:
            self.await audit_logger.log_user_action(
                service="auth",
                user_id=await self._extract_session_id(token),
                action="Get Current User",
                message="Getting Current user",
                success=True
            )
            user = await self.auth_service.get_current_user(token)
            
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired token"
                )
            
            return user
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Get current user controller error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )
    
    async def change_password(self, user_id: str, password_data: ChangePasswordRequest, request: Request) -> Dict[str, Any]:
        """
        Handle password change
        
        Args:
            token: JWT access token
            user_id: User ID
            password_data: Password change data
            request: FastAPI request object
            
        Returns:
            Password change confirmation
        """
        try:
            # Extract request info for audit
            request_info = self._extract_request_info(request)
            
            # Attempt password change
            success = await self.auth_service.change_password(
                user_id=user_id,
                current_password=password_data.current_password,
                new_password=password_data.new_password,
                request_info=request_info
            )
            
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid current password or password requirements not met"
                )
            
            return {"message": "Password changed successfully"}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Change password controller error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )
    
    async def admin_unlock_user(self, user_id: str, admin_user: "AuthUserDTO", request: Request) -> Dict[str, Any]:
        """
        Admin unlock user account
        
        Args:
            user_id: User ID to unlock
            admin_user: Admin user performing the unlock
            request: FastAPI request object
            
        Returns:
            Unlock confirmation
        """
        try:
            # Check admin permissions
            if "admin" not in admin_user.roles and "user:unlock" not in admin_user.permissions:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin permissions required to unlock user accounts"
                )
            
            # Extract request info for audit
            request_info = self._extract_request_info(request)
            
            # Attempt unlock
            success = await self.auth_service.admin_unlock_user(
                user_id=user_id,
                admin_id=admin_user.id,
                request_info=request_info
            )
            
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to unlock user account"
                )
            
            return {"message": "User account unlocked successfully"}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Admin unlock user controller error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )
    
    async def admin_reset_password(self, user_id: str, new_password: str, admin_user: "AuthUserDTO", request: Request) -> Dict[str, Any]:
        """
        Admin reset user password and unlock account
        
        Args:
            user_id: User ID to reset password for
            new_password: New password
            admin_user: Admin user performing the reset
            request: FastAPI request object
            
        Returns:
            Password reset confirmation
        """
        try:
            # Check admin permissions
            if "admin" not in admin_user.roles and "user:reset_password" not in admin_user.permissions:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin permissions required to reset user passwords"
                )
            
            # Extract request info for audit
            request_info = self._extract_request_info(request)
            
            # Attempt password reset
            success = await self.auth_service.admin_reset_password(
                user_id=user_id,
                new_password=new_password,
                admin_id=admin_user.id,
                request_info=request_info
            )
            
            if not success:
                # Try to provide more specific error message
                from ..services.password_policy import PasswordPolicyService
                policy_service = PasswordPolicyService()
                validation_result = policy_service.validate_password_strength(new_password)
                
                if not validation_result['is_valid']:
                    detail = f"Password does not meet policy requirements: {', '.join(validation_result['violations'])}"
                else:
                    detail = "Failed to reset user password - user may not exist or database error occurred"
                
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=detail
                )
            
            return {"message": "User password reset and account unlocked successfully"}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Admin reset password controller error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )
    
    async def get_lockout_status(self, user_id: str, admin_user: "AuthUserDTO") -> Dict[str, Any]:
        """
        Get user lockout status
        
        Args:
            user_id: User ID to check
            admin_user: Admin user requesting the status
            
        Returns:
            Lockout status information
        """
        try:
            # Check admin permissions
            if "admin" not in admin_user.roles and "user:view_status" not in admin_user.permissions:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin permissions required to view user status"
                )
            
            # Get lockout status
            status_info = await self.auth_service.get_lockout_status(user_id)
            
            if "error" in status_info:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=status_info["error"]
                )
            
            return status_info
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Get lockout status controller error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error"
            )


# Global instance
auth_controller = AuthController() 
