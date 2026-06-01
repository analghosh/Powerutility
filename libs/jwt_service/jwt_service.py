"""
Centralized JWT service for token generation and validation
"""

import jwt
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from .models import TokenPayload, TokenResponse, ValidationResult

logger = logging.getLogger(__name__)


class JWTService:
    """Centralized JWT service for consistent token handling across services"""
    
    def __init__(self, 
                 secret_key: Optional[str] = None,
                 algorithm: str = "HS256",
                 access_token_expire_minutes: int = 30,
                 refresh_token_expire_days: int = 7):
        """
        Initialize JWT service
        
        Args:
            secret_key: JWT secret key (from environment or config)
            algorithm: JWT algorithm (default: HS256)
            access_token_expire_minutes: Access token expiration in minutes
            refresh_token_expire_days: Refresh token expiration in days
        """
        # Get secret key from parameter or environment variable
        self.secret_key = secret_key or os.getenv("JWT_SECRET_KEY")
        
        # Validate that secret key is set
        if not self.secret_key:
            raise ValueError(
                "JWT_SECRET_KEY must be set via environment variable or parameter. "
                "This is a security requirement to prevent hardcoded secrets."
            )
        
        # Validate secret key strength
        if len(self.secret_key) < 32:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least 32 characters long for security. "
                f"Current length: {len(self.secret_key)}"
            )
        
        # Check for insecure default value
        if "your-super-secret-jwt-key-change-in-production" in self.secret_key:
            raise ValueError(
                "JWT_SECRET_KEY contains insecure default value. "
                "Set a secure random string via JWT_SECRET_KEY environment variable."
            )
        
        self.algorithm = algorithm
        self.access_token_expire_minutes = access_token_expire_minutes
        self.refresh_token_expire_days = refresh_token_expire_days
        
        logger.info(f"JWTService initialized with algorithm: {self.algorithm}")
        # Do NOT log secret key fragments - this is a security risk
    
    def generate_tokens(self, user_data: Dict[str, Any]) -> TokenResponse:
        """
        Generate access and refresh tokens for a user
        
        Args:
            user_data: User information dictionary
            
        Returns:
            TokenResponse with access and refresh tokens
        """
        now = datetime.now(timezone.utc)
        
        # Access token expiration
        access_exp = now + timedelta(minutes=self.access_token_expire_minutes)
        refresh_exp = now + timedelta(days=self.refresh_token_expire_days)
        
        # Create access token payload
        access_payload = TokenPayload(
            sub=str(user_data.get("id", user_data.get("user_id", ""))),
            username=user_data.get("username", ""),
            email=user_data.get("email"),
            is_active=user_data.get("is_active", True),
            roles=user_data.get("roles", []),
            permissions=user_data.get("permissions", []),
            profile=user_data.get("profile", {}),
            last_login=user_data.get("last_login"),
            mfa_enabled=user_data.get("mfa_enabled", False),
            mfa_setup_complete=user_data.get("mfa_setup_complete", False),
            accessible_menus=user_data.get("accessible_menus", []),
            exp=int(access_exp.timestamp()),
            iat=int(now.timestamp()),
            type="access",
            session_id=user_data.get("session_id")
        )
        
        # Create refresh token payload
        refresh_payload = TokenPayload(
            sub=str(user_data.get("id", user_data.get("user_id", ""))),
            username=user_data.get("username", ""),
            email=user_data.get("email"),
            is_active=user_data.get("is_active", True),
            roles=user_data.get("roles", []),
            permissions=user_data.get("permissions", []),
            profile=user_data.get("profile", {}),
            last_login=user_data.get("last_login"),
            mfa_enabled=user_data.get("mfa_enabled", False),
            mfa_setup_complete=user_data.get("mfa_setup_complete", False),
            accessible_menus=user_data.get("accessible_menus", []),
            exp=int(refresh_exp.timestamp()),
            iat=int(now.timestamp()),
            type="refresh",
            session_id=user_data.get("session_id")
        )
        
        # Generate tokens
        access_token = jwt.encode(
            access_payload.model_dump(exclude_none=True), 
            self.secret_key, 
            algorithm=self.algorithm
        )
        
        refresh_token = jwt.encode(
            refresh_payload.model_dump(exclude_none=True), 
            self.secret_key, 
            algorithm=self.algorithm
        )
        
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=self.access_token_expire_minutes * 60,
            refresh_expires_in=self.refresh_token_expire_days * 24 * 60 * 60,
            expires_at=access_exp.isoformat(),
            refresh_expires_at=refresh_exp.isoformat()
        )
    
    def validate_token(self, token: str, token_type: str = "access") -> ValidationResult:
        """
        Validate JWT token and return user information
        
        Args:
            token: JWT token string
            token_type: Expected token type (access or refresh)
            
        Returns:
            ValidationResult with validation status and user data
        """
        try:
            # Decode and verify token
            payload = jwt.decode(
                token, 
                self.secret_key, 
                algorithms=[self.algorithm],
                options={"verify_exp": True}
            )
            
            # Validate token type
            if payload.get("type") != token_type:
                return ValidationResult(
                    is_valid=False,
                    error=f"Invalid token type. Expected: {token_type}, Got: {payload.get('type')}"
                )
            
            # Check if token is expired
            exp = payload.get("exp")
            if exp and datetime.now(timezone.utc).timestamp() > exp:
                return ValidationResult(
                    is_valid=False,
                    error="Token has expired"
                )
            
            # Create token payload object
            token_payload = TokenPayload(**payload)
            
            return ValidationResult(
                is_valid=True,
                payload=token_payload,
                user_id=token_payload.sub,
                username=token_payload.username,
                roles=token_payload.roles,
                permissions=token_payload.permissions
            )
            
        except jwt.ExpiredSignatureError:
            return ValidationResult(
                is_valid=False,
                error="Token has expired"
            )
        except jwt.InvalidTokenError as e:
            return ValidationResult(
                is_valid=False,
                error=f"Invalid token: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error validating token: {str(e)}")
            return ValidationResult(
                is_valid=False,
                error=f"Token validation failed: {str(e)}"
            )
    
    def refresh_access_token(self, refresh_token: str) -> Optional[TokenResponse]:
        """
        Generate new access token using refresh token
        
        Args:
            refresh_token: Valid refresh token
            
        Returns:
            New TokenResponse or None if refresh token is invalid
        """
        validation_result = self.validate_token(refresh_token, token_type="refresh")
        
        if not validation_result.is_valid or not validation_result.payload:
            return None
        
        # Generate new tokens using the refresh token payload
        user_data = validation_result.payload.model_dump()
        return self.generate_tokens(user_data)


# Global JWT service instance
_jwt_service: Optional[JWTService] = None


def get_jwt_service(secret_key: Optional[str] = None, 
                   algorithm: str = "HS256",
                   access_token_expire_minutes: int = 30,
                   refresh_token_expire_days: int = 7) -> JWTService:
    """
    Get or create global JWT service instance
    
    Args:
        secret_key: JWT secret key
        algorithm: JWT algorithm
        access_token_expire_minutes: Access token expiration
        refresh_token_expire_days: Refresh token expiration
        
    Returns:
        JWTService instance
    """
    global _jwt_service
    if _jwt_service is None:
        _jwt_service = JWTService(
            secret_key=secret_key,
            algorithm=algorithm,
            access_token_expire_minutes=access_token_expire_minutes,
            refresh_token_expire_days=refresh_token_expire_days
        )
    return _jwt_service
