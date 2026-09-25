"""
Password Policy Service
Handles password validation, expiry, and lockout policies
"""

import json
import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any, Iterable, List, Optional, Union

import bcrypt

from .interfaces import IPasswordPolicyService
from ..core.config import settings


@dataclass(frozen=True)
class PasswordAge:
    """Where a password sits in its expiry cycle."""
    status: str  # "ok" | "warning" | "expired" | "exempt"
    age_days: Optional[int] = None
    days_remaining: Optional[int] = None
    expires_at: Optional[datetime] = None
    enforced: bool = False

    @property
    def blocks_access(self) -> bool:
        return self.status == "expired" and self.enforced

    def to_client(self) -> Optional[Dict[str, Any]]:
        """What the login response tells the client; None when there is nothing to say."""
        if self.status not in ("warning", "expired"):
            return None
        return {
            "status": self.status,
            "age_days": self.age_days,
            "days_remaining": self.days_remaining,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


def _role_names(value: Union[str, Iterable[str], None]) -> set:
    """Role names from a list, a JSON list string, or a comma-separated string."""
    if value is None:
        return set()
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                value = json.loads(text)
            except ValueError:
                value = text.strip("[]").replace('"', "").replace("'", "").split(",")
        else:
            value = text.split(",")
    return {str(role).strip() for role in value if str(role).strip()}


def evaluate_password_age(
    changed_at: Optional[datetime],
    roles: Iterable[str],
    *,
    created_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
    expiry_days: Optional[int] = None,
    warning_days: Optional[int] = None,
    mode: Optional[str] = None,
    exempt_roles: Union[str, List[str], None] = None,
    clock_start: Optional[datetime] = None,
) -> PasswordAge:
    """
    Classify a password by whole days since it was last changed.

    Dates are compared as naive local time because that is how the repository
    writes them (``datetime.now()``); an aware value is converted to match.
    """
    expiry_days = settings.PASSWORD_EXPIRY_DAYS if expiry_days is None else expiry_days
    warning_days = settings.PASSWORD_EXPIRY_WARNING_DAYS if warning_days is None else warning_days
    mode = settings.PASSWORD_EXPIRY_MODE if mode is None else mode
    exempt_roles = settings.PASSWORD_EXPIRY_EXEMPT_ROLES if exempt_roles is None else exempt_roles
    if clock_start is None:
        clock_start = settings.PASSWORD_EXPIRY_CLOCK_START

    if mode == "off":
        return PasswordAge("ok")
    if set(roles or []) & _role_names(exempt_roles):
        return PasswordAge("exempt")

    def _naive(value: Optional[datetime]) -> Optional[datetime]:
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone().replace(tzinfo=None)

    start = _naive(changed_at) or _naive(created_at)
    if start is None:
        return PasswordAge("ok")
    clock_start = _naive(clock_start)
    if clock_start is not None and clock_start > start:
        start = clock_start

    now = _naive(now) or datetime.now()
    age_days = max(0, (now - start).days)
    expires_at = start + timedelta(days=expiry_days)

    if age_days >= expiry_days:
        return PasswordAge("expired", age_days, 0, expires_at, enforced=(mode == "enforce"))
    if age_days >= expiry_days - warning_days:
        return PasswordAge("warning", age_days, expiry_days - age_days, expires_at)
    return PasswordAge("ok", age_days, expiry_days - age_days, expires_at)


def matches_recent_password(password: str, user_doc: Dict[str, Any], count: Optional[int] = None) -> bool:
    """
    True when ``password`` is one of the user's last ``count`` passwords, the
    current one included. bcrypt salts every hash, so the only valid comparison
    is checkpw against each stored hash. History holds raw hashes (written by
    update_password before this fix) and {"password_hash": ...} dicts (written
    by admin_reset_password); both are read.
    """
    count = settings.PASSWORD_HISTORY_COUNT if count is None else count
    hashes = [user_doc.get("password_hash")]
    previous = user_doc.get("password_history") or []
    if count > 1:
        for entry in previous[-(count - 1):]:
            hashes.append(entry.get("password_hash") if isinstance(entry, dict) else entry)
    encoded = password.encode("utf-8")
    for stored in hashes:
        if not stored:
            continue
        if isinstance(stored, str):
            stored = stored.encode("utf-8")
        try:
            if bcrypt.checkpw(encoded, bytes(stored)):
                return True
        except ValueError:
            continue
    return False


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
    
    def validate_password_strength(self, password: str) -> Dict[str, Any]:
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
    
    def generate_temporary_password(self) -> str:
        """Generate a secure temporary password for admin resets"""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(secrets.choice(alphabet) for _ in range(12))
    
    def check_password_expiry(self, user_id: str, password_changed_date: datetime, user_role: str = "user") -> Dict[str, Any]:
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
    
    def check_account_lockout(self, user_id: str, failed_attempts: int, last_failed_attempt: datetime) -> Dict[str, Any]:
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