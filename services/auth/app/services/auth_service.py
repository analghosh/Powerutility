"""
Authentication service using repository pattern
"""

import time
import asyncio
import logging
from fastapi import HTTPException
from fastapi import Header
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from bson import ObjectId

# Import timezone utilities
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
from libs.audit_queue.timezone_utils import now_ist
from ..repository.user_repository import UserRepository
# from ..repository.audit_repository import AuditRepository  # Remove direct audit repo
from ..core.dto import UserDTO, AuthUserDTO
from ..core.config import settings
from ..services.jwt_service import jwt_service
from ..services.redis_blacklist import redis_blacklist_service
from ..services.mfa_service import mfa_service
from ..services.password_policy import (
    PasswordAge,
    PasswordPolicyService,
    evaluate_password_age,
    matches_recent_password,
)

# Import the secure audit logging
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
from libs.audit_queue.simple_logger import AuditLogger, AuditLogLevel
from ..core.dependencies import get_audit_logger



logger = logging.getLogger(__name__)


class AuthService:
    """Authentication service using repository pattern"""
    
    def __init__(self):
        self.user_repo = UserRepository()
        # self.audit_repo = AuditRepository()  # Remove direct audit repo
        self.jwt_service = jwt_service
        self.audit_logger : AuditLogger = get_audit_logger()

    def _password_age(self, user) -> PasswordAge:
        """Where this user's password sits in its 90-day cycle."""
        return evaluate_password_age(
            getattr(user, "password_changed_at", None),
            getattr(user, "roles", None) or [],
            created_at=getattr(user, "created_at", None),
        )

    def _password_change_required(self, user, password_age: PasswordAge, request_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Login response for an expired password: no session, only a short-lived
        token that /change-expired-password accepts. Reached only after every
        login factor has passed, so it tells nothing to someone guessing passwords.
        """
        self.audit_logger.log_security_event(
            service="auth",
            event_type="password_expired_login_blocked",
            message=f"Login blocked until the expired password is changed: {user.username}",
            user_id=user.id,
            client_ip=request_info.get("ip_address"),
            username=user.username,
            user_agent=request_info.get("user_agent"),
            password_age_days=password_age.age_days,
            success=False,
        )
        return {
            "password_expired": True,
            "password_change_token": self.jwt_service.create_password_change_token(user.id, user.username),
            "expires_in": int(settings.PASSWORD_CHANGE_TOKEN_EXPIRE_MINUTES * 60),
            "message": "Your password has expired. Change it to continue.",
            "password_expiry": password_age.to_client(),
        }

    def _issue_session(self, user_with_perms) -> Dict[str, Any]:
        """Access + refresh tokens for a user who has passed every check."""
        user_dict = user_with_perms.to_dict()
        now = now_ist()
        user_profile = getattr(user_with_perms, 'profile', {}) or {}
        return {
            "user": {
                "first_name": user_profile.get("first_name"),
                "last_name": user_profile.get("last_name"),
                "last_login": getattr(user_with_perms, 'last_login', None),
                "id": user_with_perms.id
            },
            "tokens": {
                "access_token": self.jwt_service.create_access_token(user_dict),
                "refresh_token": self.jwt_service.create_refresh_token(user_dict),
                "token_type": "bearer",
                "expires_in": int(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60),
                "refresh_expires_in": int(settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60),
                "expires_at": now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
                "refresh_expires_at": now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
            },
            "password_expiry": None,
        }

    async def _apply_password_change(self, user, current_password: str, new_password: str) -> None:
        """
        Validate and store a new password, restarting its 90-day cycle. Raises
        HTTPException(400) with the reason the user needs to see. The current
        password is checked through authenticate_user so a wrong guess counts
        toward the account lockout, as a wrong login does.
        """
        if not await self.user_repo.authenticate_user(user.username, current_password):
            raise HTTPException(400, detail="Current password is incorrect")
        if new_password == current_password:
            raise HTTPException(400, detail="New password must be different from your current password")
        strength = PasswordPolicyService().validate_password_strength(new_password)
        if not strength["is_valid"]:
            raise HTTPException(400, detail="; ".join(strength["violations"]))
        user_doc = await self.user_repo.find_one({"_id": ObjectId(user.id)})
        # bcrypt is deliberately slow; checking up to five hashes must not stall the event loop.
        if user_doc and await asyncio.to_thread(matches_recent_password, new_password, user_doc):
            raise HTTPException(
                400,
                detail=f"New password must not match any of your last {settings.PASSWORD_HISTORY_COUNT} passwords",
            )
        if not await self.user_repo.update_password(user.id, new_password):
            raise HTTPException(500, detail="Failed to change password")

    async def login(self, username: str, password: str, request_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Authenticate user and return temporary token
        
        Args:
            username: Username or email
            password: User password
            request_info: Request information (IP, user agent, etc.)
            
        Returns:
            Dict containing temporary token and MFA status
        """
        try:
            # Authenticate user
            user = await self.user_repo.authenticate_user(username, password)
            
            if not user:
                # Failed login logging handled at route layer to avoid duplication
                return None
                
            # Create temporary token data
            temp_token_data = {
                "user_id": user.id,
                "username": user.username,
                "login_step": "password_verified",
                "ip_address": request_info.get("ip_address"),
                "user_agent": request_info.get("user_agent")
            }
            
            # Generate temporary token (expires in 5 minutes)
            temporary_token = self.jwt_service.create_temporary_token(temp_token_data, expire_minutes=5)
            
            # Check if MFA is enabled for this user
            mfa_required = user.mfa_enabled and user.mfa_setup_complete
            
            if mfa_required:
                # MFA enabled: return temporary token for two-step flow
                message = "Please provide TOTP code to complete login"
                return {
                    "temporary_token": temporary_token,
                    "mfa_required": mfa_required,
                    "expires_in": 300,  # 5 minutes
                    "message": message
                }
            else:
                # The password is this user's only factor, so expiry is checked now.
                # MFA users are checked in verify_login, after their code.
                password_age = self._password_age(user)
                if password_age.blocks_access:
                    return self._password_change_required(user, password_age, request_info)

                # MFA not enabled: complete login immediately (traditional flow)
                user_with_perms = await self.user_repo.get_user_with_permissions(user.username)
                if not user_with_perms:
                    raise HTTPException(403, detail="User role or permissions not found")
                
                # Generate final tokens immediately
                user_dict = user_with_perms.to_dict()
                access_token = self.jwt_service.create_access_token(user_dict)
                refresh_token = self.jwt_service.create_refresh_token(user_dict)
                
                # Calculate expiry times
                access_expires_in = int(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60)
                refresh_expires_in = int(settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)

                # Calculate expiry datetimes (using IST)
                now = now_ist()
                access_expires_at = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
                refresh_expires_at = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

                # Login success logging handled at route layer to avoid duplication
                
                # Get user profile information
                user_profile = getattr(user, 'profile', {}) or {}
                
                return {
                    "user": {
                        "first_name": user_profile.get("first_name"),
                        "last_name": user_profile.get("last_name"),
                        "last_login": getattr(user, 'last_login', None),
                        "id": user.id
                    },
                    "tokens": {
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "token_type": "bearer",
                        "expires_in": access_expires_in,
                        "refresh_expires_in": refresh_expires_in,
                        "expires_at": access_expires_at,
                        "refresh_expires_at": refresh_expires_at
                    },
                    "password_expiry": password_age.to_client()
                }
            
        except Exception as e:
            logger.error(f"Login error: {e}")
            raise 
    
    async def verify_login(self, temporary_token: str, totp_code: Optional[str], request_info: Dict[str, Any], backup_code: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Verify login with temporary token and TOTP code or backup code.

        Args:
            temporary_token: Temporary token from login step
            totp_code: TOTP code from authenticator app
            backup_code: Backup code (alternative when authenticator is unavailable)
            request_info: Request information (IP, user agent, etc.)

        Returns:
            Dict containing user info and tokens
        """
        try:
            # Verify temporary token
            temp_payload = self.jwt_service.verify_temporary_token(temporary_token)
            if not temp_payload:
                return None
            
            user_id = temp_payload.get("user_id")
            if not user_id:
                return None
            
            # Get user with permissions (single query optimization)
            user_with_perms = await self.user_repo.get_user_with_permissions_by_id(user_id)
            if not user_with_perms:
                # Fallback to username-based lookup
                user = await self.user_repo.get_user_by_id(user_id)
                if not user:
                    return None
                user_with_perms = await self.user_repo.get_user_with_permissions(user.username)
                if not user_with_perms:
                    raise HTTPException(403, detail="User role or permissions not found")
            else:
                user = user_with_perms  # Use the same object
            
            # Check if MFA is required
            if user.mfa_enabled and user.mfa_setup_complete:
                logger.info(f"MFA verification required for user {user.username}")

                if not totp_code and not backup_code:
                    logger.warning(f"No TOTP or backup code provided for MFA user {user.username}")
                    return None

                # Get full user document to access MFA secret and backup codes
                full_user_doc = await self.user_repo.find_one({"_id": ObjectId(user_id)})
                if not full_user_doc:
                    logger.error(f"Full user document not found for user_id {user_id}")
                    raise HTTPException(500, detail="MFA configuration error")

                if backup_code:
                    # Backup code login path
                    from .mfa_rate_limiter import mfa_rate_limiter
                    from ..controllers.mfa_controller import mfa_controller

                    rate_limit_key = f"{user_id}_backup"
                    if not mfa_rate_limiter.can_attempt(rate_limit_key):
                        self.audit_logger.log_security_event(
                            service="auth",
                            event_type="mfa_rate_limited",
                            message=f"Backup code login rate limited: {user.username}",
                            user_id=user.id,
                            success=False,
                            client_ip=request_info.get("ip_address"),
                            username=user.username,
                            failure_reason="Rate limited",
                            login_method="password+backup_code",
                            user_agent=request_info.get("user_agent"),
                        )
                        return None

                    hashed_backup_codes = full_user_doc.get("mfa_backup_codes", [])
                    valid_code = None
                    for hashed in hashed_backup_codes:
                        if mfa_controller.verify_backup_code(backup_code, hashed):
                            valid_code = hashed
                            break

                    if not valid_code:
                        mfa_rate_limiter.record_attempt(rate_limit_key, success=False)
                        self.audit_logger.log_security_event(
                            service="auth",
                            event_type="mfa_backup_code_failed",
                            message=f"Invalid backup code login attempt: {user.username}",
                            user_id=user.id,
                            success=False,
                            client_ip=request_info.get("ip_address"),
                            username=user.username,
                            failure_reason="Invalid backup code",
                            login_method="password+backup_code",
                            user_agent=request_info.get("user_agent"),
                        )
                        return None

                    # Consume the used backup code
                    mfa_rate_limiter.record_attempt(rate_limit_key, success=True)
                    hashed_backup_codes.remove(valid_code)
                    await self.user_repo.update_user(user_id, {"mfa_backup_codes": hashed_backup_codes})
                    logger.warning(f"User {user.username} logged in via backup code — {len(hashed_backup_codes)} codes remaining")

                else:
                    # TOTP login path
                    if not full_user_doc.get("mfa_secret"):
                        logger.error(f"MFA secret not found for user {user.username}")
                        raise HTTPException(500, detail="MFA configuration error")

                    verification_result = mfa_service.verify_totp_with_rate_limit(
                        user_id, full_user_doc["mfa_secret"], totp_code or ""
                    )

                    if verification_result['rate_limited']:
                        self.audit_logger.log_security_event(
                            service="auth",
                            event_type="mfa_rate_limited",
                            message=f"MFA verification rate limited: {user.username}",
                            user_id=user.id,
                            success=False,
                            client_ip=request_info.get("ip_address"),
                            username=user.username,
                            failure_reason="Rate limited",
                            login_method="password+mfa",
                            user_agent=request_info.get("user_agent"),
                            lockout_info=verification_result.get('lockout_info')
                        )
                        return None

                    if not verification_result['verified']:
                        self.audit_logger.log_security_event(
                            service="auth",
                            event_type="mfa_failed",
                            message=f"MFA verification failed: {user.username}",
                            user_id=user.id,
                            success=False,
                            client_ip=request_info.get("ip_address"),
                            username=user.username,
                            failure_reason="Invalid MFA code",
                            login_method="password+mfa",
                            user_agent=request_info.get("user_agent"),
                            remaining_attempts=verification_result.get('remaining_attempts')
                        )
                        return None
            
            # Every factor has passed; now the password's age decides.
            password_age = self._password_age(user)
            if password_age.blocks_access:
                redis_blacklist_service.invalidate_temporary_token(temporary_token, user_id)
                return self._password_change_required(user, password_age, request_info)

            # Generate final tokens
            user_dict = user_with_perms.to_dict()
            access_token = self.jwt_service.create_access_token(user_dict)
            refresh_token = self.jwt_service.create_refresh_token(user_dict)
            
            # Calculate expiry times
            access_expires_in = int(settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60)
            refresh_expires_in = int(settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)

            # Calculate expiry datetimes (using IST)
            now = now_ist()
            access_expires_at = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
            refresh_expires_at = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

            # Invalidate temporary token after successful login
            redis_blacklist_service.invalidate_temporary_token(temporary_token, user_id)
            
            # MFA login success logging handled at route layer to avoid duplication
            
            # Get user profile information
            user_profile = getattr(user, 'profile', {}) or {}
            
            return {
                "user": {
                    "first_name": user_profile.get("first_name"),
                    "last_name": user_profile.get("last_name"),
                    "last_login": getattr(user, 'last_login', None)
                },
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_type": "bearer",
                    "expires_in": access_expires_in,
                    "refresh_expires_in": refresh_expires_in,
                    "expires_at": access_expires_at,
                    "refresh_expires_at": refresh_expires_at
                },
                "password_expiry": password_age.to_client()
            }
            
        except Exception as e:
            logger.error(f"Verify login error: {e}")
            return None
    
    async def refresh_token(self, refresh_token: str, request_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Refresh access token using refresh token
        
        Args:
            refresh_token: Valid refresh token
            request_info: Request information
            
        Returns:
            Dict containing new access token
        """
        try:
            # Validate refresh token (this includes blacklist check)
            payload = self.jwt_service.verify_refresh_token(refresh_token)
            if not payload:
                print(f"DEBUG: refresh_token - validation failed")
                return None
            
            # Get user
            user_id = payload.get("sub") or payload.get("id")
            if not user_id:
                return None
            user = await self.user_repo.get_user_by_id(user_id)
            if not user or not user.is_active:
                return None

            # A session opened on day 89 must not carry on past day 90: refusing
            # the refresh sends the user back through login, which blocks them.
            if self._password_age(user).blocks_access:
                self.audit_logger.log_security_event(
                    service="auth",
                    event_type="token_refresh_denied_password_expired",
                    message=f"Token refresh refused, password expired: {user.username}",
                    user_id=user.id,
                    client_ip=request_info.get("ip_address"),
                    username=user.username,
                    user_agent=request_info.get("user_agent"),
                    success=False
                )
                return None
            
            # Generate new access token
            user_dict = user.to_dict()
            access_token = self.jwt_service.create_access_token(user_dict)
            
            # Log token refresh with PII protection
            self.audit_logger.log_security_event(
                service="auth",
                event_type="token_refresh",
                message=f"Access token refreshed: {user.username}",
                user_id=user.id,
                client_ip=request_info.get("ip_address"),
                username=user.username,
                user_agent=request_info.get("user_agent"),
                success=True
            )
            
            return {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
            }
            
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            return None
    
    async def logout(self, access_token: str, refresh_token: Optional[str], request_info: Dict[str, Any]) -> bool:
        """
        Logout user by blacklisting tokens
        
        Args:
            access_token: Current access token
            refresh_token: Current refresh token (optional)
            request_info: Request information
            
        Returns:
            True if logout successful
        """
        try:
            # Validate access token to get user info
            payload = self.jwt_service.verify_access_token(access_token)
            if not payload:
                return False
            
            access_user_id = payload.get("sub") or payload.get("id")
            
            # Blacklist access token
            if access_user_id:
                redis_blacklist_service.invalidate_access_token(access_token, access_user_id)
            
            # If refresh token is provided, validate and blacklist it
            if refresh_token:
                refresh_payload = self.jwt_service.verify_refresh_token(refresh_token)
                if refresh_payload:
                    refresh_user_id = refresh_payload.get("sub") or refresh_payload.get("id")
                    
                    # Ensure both tokens belong to the same user
                    if access_user_id != refresh_user_id:
                        logger.warning(f"Token mismatch during logout: access_user_id={access_user_id}, refresh_user_id={refresh_user_id}")
                        return False
                    # Blacklist refresh token
                    if access_user_id:
                        redis_blacklist_service.invalidate_refresh_token(refresh_token, access_user_id)
            
            # Log logout with PII protection
            self.audit_logger.log_security_event(
                service="auth",
                event_type="logout",
                message=f"User logout successful: {payload['username']}",
                user_id=access_user_id,
                client_ip=request_info.get("ip_address"),
                username=payload["username"],
                user_agent=request_info.get("user_agent"),
                success=True
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Logout error: {e}")
            return False
    
    async def get_current_user(self, token: Optional[str] = Header(default=None, alias="Authorization")) -> Optional[AuthUserDTO]:
        """
        Get current user from token
        
        Args:
            token: JWT access token
            
        Returns:
            AuthUserDTO if valid token, None otherwise
        """
        try:
            # Check if token is provided
            if not token:
                return None
                
            # Validate token
            payload = self.jwt_service.verify_access_token(token)
            if not payload:
                # Return None for invalid tokens to trigger unauthorized response
                return None
            # Check if token is blacklisted
            if redis_blacklist_service.is_access_token_blacklisted(token):
                return None
            
            # Get user from database
            user_id = payload.get("sub") or payload.get("id")
            if not user_id:
                return None
            user = await self.user_repo.get_user_by_id(user_id)
            if not user or not user.is_active:
                return None
            
            # Add roles and permissions from token payload
            user.roles = payload.get("roles", [])
            user.permissions = payload.get("permissions", [])

            # Return auth user DTO
            return AuthUserDTO.from_user_dto(user)
            
        except Exception as e:
            logger.error(f"Get current user error: {e}")
            return None
    
    async def change_password(self, user_id: str, current_password: str, new_password: str, request_info: Dict[str, Any]) -> bool:
        """
        Change user password
        
        Args:
            user_id: User ID
            current_password: Current password
            new_password: New password
            request_info: Request information
            
        Returns:
            True if password changed successfully
        """
        try:
            # Get user
            user = await self.user_repo.get_user_by_id(user_id)
            if not user:
                return False
            
            # Verify current password, validate and store the new one
            await self._apply_password_change(user, current_password, new_password)
            
            # Log password change with PII protection
            self.audit_logger.log_security_event(
                service="auth",
                event_type="password_change",
                message=f"Password changed successfully: {user.username}",
                user_id=user_id,
                client_ip=request_info.get("ip_address"),
                username=user.username,
                user_agent=request_info.get("user_agent"),
                success=True
            )
            
            return True
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Change password error: {e}")
            return False

    async def change_expired_password(self, password_change_token: str, current_password: str,
                                      new_password: str, request_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        The only thing an expired password is good for. Takes the token login
        issued in place of a session, changes the password under the same rules
        as /change-password, spends the token, and opens the session.
        """
        expired_session = "Your password change session has expired. Sign in again."
        payload = self.jwt_service.verify_password_change_token(password_change_token)
        if not payload:
            raise HTTPException(401, detail=expired_session)
        user_id = payload["password_change_user_id"]
        user_with_perms = await self.user_repo.get_user_with_permissions_by_id(user_id)
        if not user_with_perms or not user_with_perms.is_active:
            raise HTTPException(401, detail=expired_session)

        await self._apply_password_change(user_with_perms, current_password, new_password)
        redis_blacklist_service.invalidate_temporary_token(
            password_change_token, user_id,
            ttl_seconds=int(settings.PASSWORD_CHANGE_TOKEN_EXPIRE_MINUTES * 60),
        )
        self.audit_logger.log_security_event(
            service="auth",
            event_type="expired_password_changed",
            message=f"Expired password changed: {user_with_perms.username}",
            user_id=user_id,
            client_ip=request_info.get("ip_address"),
            username=user_with_perms.username,
            user_agent=request_info.get("user_agent"),
            success=True
        )
        return self._issue_session(user_with_perms)

    async def get_user_by_id(self, user_id: str) -> Optional[UserDTO]:
        """Retrieve a single user by ID."""
        try:
            return await self.user_repo.get_user_by_id(user_id)
        except Exception as e:
            logger.error(f"Get user by id error: {e}")
            return None
    
    async def admin_unlock_user(self, user_id: str, admin_id: str, request_info: Dict[str, Any]) -> bool:
        """Admin unlock user account"""
        try:
            # Get user info for logging
            user = await self.user_repo.get_user_by_id(user_id)
            if not user:
                return False
            
            # Unlock the user
            success = await self.user_repo.admin_unlock_user(user_id, admin_id)
            
            if success:
                # Log admin unlock action
                self.audit_logger.log_security_event(
                    service="auth",
                    event_type="admin_unlock_user",
                    message=f"User account unlocked by admin: {user.username}",
                    user_id=user_id,
                    client_ip=request_info.get("ip_address"),
                    username=user.username,
                    admin_id=admin_id,
                    user_agent=request_info.get("user_agent"),
                    success=True
                )
            
            return success
        except Exception as e:
            logger.error(f"Admin unlock user error: {e}")
            return False
    
    async def admin_reset_password(self, user_id: str, new_password: str, admin_id: str, request_info: Dict[str, Any]) -> bool:
        """Admin reset user password and unlock account"""
        try:
            logger.info(f"Auth service admin_reset_password called for user_id: {user_id}")
            
            # Validate password policy
            from .password_policy import PasswordPolicyService
            policy_service = PasswordPolicyService()
            validation_result = policy_service.validate_password_strength(new_password)
            
            if not validation_result['is_valid']:
                logger.warning(f"Password policy validation failed: {validation_result['violations']}")
                return False
            
            # Get user info for logging
            user = await self.user_repo.get_user_by_id(user_id)
            if not user:
                logger.warning(f"User not found in auth service for user_id: {user_id}")
                return False
            
            # Reset password and unlock
            success = await self.user_repo.admin_reset_password(user_id, new_password, admin_id)
            
            if success:
                # Log admin password reset action
                self.audit_logger.log_security_event(
                    service="auth",
                    event_type="admin_password_reset",
                    message=f"Password reset by admin for user: {user.username}",
                    user_id=user_id,
                    client_ip=request_info.get("ip_address"),
                    username=user.username,
                    admin_id=admin_id,
                    user_agent=request_info.get("user_agent"),
                    success=True
                )
            
            return success
        except Exception as e:
            logger.error(f"Admin reset password error: {e}")
            return False
    
    async def get_lockout_status(self, user_id: str) -> Dict[str, Any]:
        """Get user lockout status"""
        try:
            return await self.user_repo.get_lockout_status(user_id)
        except Exception as e:
            logger.error(f"Get lockout status error: {e}")
            return {"error": str(e)}


# Global instance
auth_service = AuthService() 