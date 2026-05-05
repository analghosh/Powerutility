"""
Data Transfer Objects (DTOs) for safe data transfer
"""

from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class UserDTO:
    """Safe user data transfer object"""
    id: str
    username: str
    email: Optional[str]
    is_active: bool
    is_verified: bool
    roles: list
    permissions: list
    profile: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    last_login: Optional[datetime]
    login_attempts: int
    locked_until: Optional[datetime]
    password_expires_at: Optional[datetime]
    mfa_enabled: bool
    mfa_setup_complete: bool
    accessible_menus: list = None  # List of accessible menu IDs
    
    @classmethod
    def from_db_document(cls, user_doc: Dict[str, Any]) -> 'UserDTO':
        """Create UserDTO from database document"""
        return cls(
            id=str(user_doc["_id"]),
            username=user_doc["username"],
            email=user_doc.get("email"),
            is_active=user_doc.get("is_active", False),
            is_verified=user_doc.get("is_verified", False),
            roles=user_doc.get("roles", []),
            permissions=user_doc.get("permissions", []),
            profile=user_doc.get("profile", {}),
            created_at=user_doc["created_at"],
            updated_at=user_doc.get("updated_at", user_doc["created_at"]),
            last_login=user_doc.get("last_login"),
            login_attempts=user_doc.get("login_attempts", 0),
            locked_until=user_doc.get("locked_until"),
            password_expires_at=user_doc.get("password_expires_at"),
            mfa_enabled=user_doc.get("mfa_enabled", False),
            mfa_setup_complete=user_doc.get("mfa_setup_complete", False),
            accessible_menus=[]  # Will be populated by user repository when fetching permissions
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (excluding sensitive data)"""
        return {
            "sub": self.id,  # Use 'sub' for JWT standard
            "username": self.username,
            "email": self.email,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "roles": self.roles,
            "permissions": self.permissions,
            "profile": self.profile,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "login_attempts": self.login_attempts,
            "locked_until": self.locked_until.isoformat() if self.locked_until else None,
            "password_expires_at": self.password_expires_at.isoformat() if self.password_expires_at else None,
            "mfa_enabled": self.mfa_enabled,
            "mfa_setup_complete": self.mfa_setup_complete,
            "accessible_menus": self.accessible_menus or []
        }


@dataclass
class AuthUserDTO:
    """Authentication-specific user DTO"""
    id: str
    username: str
    email: Optional[str]
    is_active: bool
    roles: list
    permissions: list
    profile: Dict[str, Any]
    last_login: Optional[datetime]
    created_at: datetime
    mfa_enabled: bool
    mfa_setup_complete: bool
    accessible_menus: list = None  # List of accessible menu IDs
    
    @classmethod
    def from_user_dto(cls, user_dto: UserDTO) -> 'AuthUserDTO':
        """Create AuthUserDTO from UserDTO"""
        return cls(
            id=user_dto.id,
            username=user_dto.username,
            email=user_dto.email,
            is_active=user_dto.is_active,
            roles=user_dto.roles,
            permissions=user_dto.permissions,
            profile=user_dto.profile,
            last_login=user_dto.last_login,
            created_at=user_dto.created_at,
            mfa_enabled=user_dto.mfa_enabled,
            mfa_setup_complete=user_dto.mfa_setup_complete,
            accessible_menus=user_dto.accessible_menus or []
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "sub": self.id,  # Use 'sub' for JWT standard
            "username": self.username,
            "email": self.email,
            "is_active": self.is_active,
            "roles": self.roles,
            "permissions": self.permissions,
            "profile": self.profile,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "mfa_enabled": self.mfa_enabled,
            "mfa_setup_complete": self.mfa_setup_complete,
            "accessible_menus": self.accessible_menus or []
        } 
