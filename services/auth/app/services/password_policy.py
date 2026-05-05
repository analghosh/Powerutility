"""
Password Policy Service
Handles password validation, expiry, and lockout policies
"""

import re
import secrets
import string
from datetime import datetime, timedelta
from typing import Dict, Any

from .interfaces import IPasswordPolicyService
from ..core.config import settings


class PasswordPolicyService(IPasswordPolicyService):
    """Password policy management service following Single Responsibility Principle"""
    
    def __init__(self):
        # Password policy configuration from settings
        self.min_password_length = settings.MIN_PASSWORD_LENGTH
        self.require_uppercase = settings.REQUIRE_UPPERCASE
        self.require_lowercase = settings.REQUIRE_LOWERCASE
        self.require_numbers = settings.REQUIRE_NUMBERS
        self.require_special_chars = settings.REQUIRE_SPECIAL_CHARS
        self.password_expiry_days = settings.PASSWORD_EXPIRY_DAYS
        self.password_history_count = settings.PASSWORD_HISTORY_COUNT
        self.max_login_attempts = settings.MAX_LOGIN_ATTEMPTS
        self.lockout_duration_minutes = settings.LOCKOUT_DURATION_MINUTES
        self.lockout_window_hours = settings.LOCKOUT_WINDOW_HOURS
        self.first_login_password_expiry_hours = settings.FIRST_LOGIN_PASSWORD_EXPIRY_HOURS
    
    async def validate_password_strength(self, password: str) -> Dict[str, Any]:
        """Validate password strength according to policy"""
        violations = []
        suggestions = []
        strength_score = 0
        
        # Check minimum length
        if len(password) < self.min_password_length:
            violations.append(f"Password must be at least {self.min_password_length} characters long")
        else:
            strength_score += 20
        
        # Check for uppercase letters
        if self.require_uppercase and not re.search(r'[A-Z]', password):
            violations.append("Password must contain at least one uppercase letter")
        else:
            strength_score += 20
        
        # Check for lowercase letters
        if self.require_lowercase and not re.search(r'[a-z]', password):
            violations.append("Password must contain at least one lowercase letter")
        else:
            strength_score += 20
        
        # Check for numbers
        if self.require_numbers and not re.search(r'\d', password):
            violations.append("Password must contain at least one number")
        else:
            strength_score += 20
        
        # Check for special characters
        if self.require_special_chars and not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', password):
            violations.append("Password must contain at least one special character")
        else:
            strength_score += 20
        
        # Additional strength checks
        if len(password) >= 12:
            strength_score += 10
            suggestions.append("Consider using a longer password for better security")
        
        if re.search(r'(.)\1{2,}', password):
            suggestions.append("Avoid repeating characters")
        
        if re.search(r'(123|abc|qwe)', password.lower()):
            suggestions.append("Avoid common patterns")
        
        is_valid = len(violations) == 0
        
        return {
            "is_valid": is_valid,
            "strength_score": strength_score,
            "violations": violations,
            "suggestions": suggestions
        }
    
    async def generate_temporary_password(self) -> str:
        """Generate a secure temporary password for admin resets"""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(secrets.choice(alphabet) for _ in range(12))
    
    async def check_password_expiry(self, user_id: str, password_changed_date: datetime, user_role: str = "user") -> Dict[str, Any]:
        """Check if password has expired for a user"""
        now = datetime.now()
        
        # Superadmin has no password expiry
        if user_role == "superadmin":
            return {
                "is_expired": False,
                "days_until_expiry": None,
                "requires_change": False
            }
        
        # Calculate expiry date
        if user_role == "first_time":
            expiry_date = password_changed_date + timedelta(hours=self.first_login_password_expiry_hours)
        else:
            expiry_date = password_changed_date + timedelta(days=self.password_expiry_days)
        
        days_until_expiry = (expiry_date - now).days
        is_expired = days_until_expiry <= 0
        requires_change = days_until_expiry <= 7  # Warn if expiring within 7 days
        
        return {
            "is_expired": is_expired,
            "days_until_expiry": days_until_expiry,
            "requires_change": requires_change
        }
    
    async def check_account_lockout(self, user_id: str, failed_attempts: int, last_failed_attempt: datetime) -> Dict[str, Any]:
        """Check if account should be locked out"""
        now = datetime.now()
        time_window = timedelta(hours=self.lockout_window_hours)
        
        # Reset attempts if outside time window
        if (now - last_failed_attempt) > time_window:
            failed_attempts = 0
        
        is_locked = failed_attempts >= self.max_login_attempts
        remaining_attempts = max(0, self.max_login_attempts - failed_attempts)
        
        if is_locked:
            lockout_until = last_failed_attempt + timedelta(minutes=self.lockout_duration_minutes)
            is_locked = now < lockout_until
        else:
            lockout_until = None
        
        return {
            "is_locked": is_locked,
            "remaining_attempts": remaining_attempts,
            "lockout_until": lockout_until.isoformat() if lockout_until else None
        } 