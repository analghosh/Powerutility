"""
Service interfaces following Interface Segregation Principle
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
from datetime import datetime


class IPasswordPolicyService(ABC):
    """Interface for password policy service"""
    
    @abstractmethod
    def validate_password_strength(self, password: str) -> Dict[str, Any]:
        """Validate password strength according to policy"""
        pass
    
    @abstractmethod
    def generate_temporary_password(self) -> str:
        """Generate a secure temporary password for admin resets"""
        pass
    
    @abstractmethod
    def check_password_expiry(self, user_id: str, password_changed_date: datetime, user_role: str = "user") -> Dict[str, Any]:
        """Check if password has expired for a user"""
        pass
    
    @abstractmethod
    def check_account_lockout(self, user_id: str, failed_attempts: int, last_failed_attempt: datetime) -> Dict[str, Any]:
        """Check if account should be locked out"""
        pass 
