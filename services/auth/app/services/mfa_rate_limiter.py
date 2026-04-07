"""
MFA Rate Limiter Service for preventing brute force attacks
"""

import time
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta
from collections import defaultdict, deque

# Import timezone utilities
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
from libs.audit_queue.timezone_utils import now_ist

logger = logging.getLogger(__name__)


class MFARateLimiter:
    """
    Rate limiter for MFA verification attempts to prevent brute force attacks.
    
    Features:
    - Per-user rate limiting
    - Exponential backoff for repeated failures
    - Automatic cleanup of old attempts
    - Configurable limits and windows
    """
    
    def __init__(
        self,
        max_attempts: int = 5,
        window_minutes: int = 15,
        lockout_minutes: int = 30,
        max_lockout_minutes: int = 1440  # 24 hours
    ):
        """
        Initialize rate limiter.
        
        Args:
            max_attempts: Maximum attempts allowed in window
            window_minutes: Time window for attempt counting
            lockout_minutes: Initial lockout duration
            max_lockout_minutes: Maximum lockout duration
        """
        self.max_attempts = max_attempts
        self.window_minutes = window_minutes
        self.lockout_minutes = lockout_minutes
        self.max_lockout_minutes = max_lockout_minutes
        
        # Store attempts per user: {user_id: deque of timestamps}
        self.attempts: Dict[str, deque] = defaultdict(lambda: deque())
        
        # Store lockout info: {user_id: {'locked_until': datetime, 'failure_count': int}}
        self.lockouts: Dict[str, Dict] = {}
        
        # Last cleanup time (using IST)
        self.last_cleanup = now_ist()
    
    def _cleanup_old_data(self):
        """Clean up old attempts and expired lockouts"""
        now = now_ist()
        
        # Only cleanup every 5 minutes to avoid overhead
        if (now - self.last_cleanup).total_seconds() < 300:
            return
        
        cutoff_time = now - timedelta(minutes=self.window_minutes)
        
        # Clean old attempts
        for user_id in list(self.attempts.keys()):
            attempts = self.attempts[user_id]
            # Remove attempts older than window
            while attempts and attempts[0] < cutoff_time:
                attempts.popleft()
            
            # Remove empty deques
            if not attempts:
                del self.attempts[user_id]
        
        # Clean expired lockouts
        for user_id in list(self.lockouts.keys()):
            if now >= self.lockouts[user_id]['locked_until']:
                del self.lockouts[user_id]
        
        self.last_cleanup = now
    
    def is_locked_out(self, user_id: str) -> bool:
        """Check if user is currently locked out"""
        self._cleanup_old_data()
        
        if user_id not in self.lockouts:
            return False
        
        return now_ist() < self.lockouts[user_id]['locked_until']
    
    def get_lockout_info(self, user_id: str) -> Optional[Dict]:
        """Get lockout information for user"""
        if not self.is_locked_out(user_id):
            return None
        
        lockout_info = self.lockouts[user_id]
        remaining_seconds = int((lockout_info['locked_until'] - now_ist()).total_seconds())
        
        return {
            'locked_until': lockout_info['locked_until'],
            'remaining_seconds': remaining_seconds,
            'failure_count': lockout_info['failure_count']
        }
    
    def can_attempt(self, user_id: str) -> bool:
        """Check if user can make an MFA attempt"""
        self._cleanup_old_data()
        
        # Check if locked out
        if self.is_locked_out(user_id):
            return False
        
        # Check attempt count in current window
        now = now_ist()
        cutoff_time = now - timedelta(minutes=self.window_minutes)
        
        attempts = self.attempts[user_id]
        # Count recent attempts
        recent_attempts = sum(1 for attempt_time in attempts if attempt_time >= cutoff_time)
        
        return recent_attempts < self.max_attempts
    
    def record_attempt(self, user_id: str, success: bool) -> Dict:
        """
        Record an MFA attempt and return rate limit status.
        
        Args:
            user_id: User identifier
            success: Whether the attempt was successful
            
        Returns:
            Dict with rate limit status and lockout info
        """
        self._cleanup_old_data()
        now = now_ist()
        
        # Record the attempt
        self.attempts[user_id].append(now)
        
        if success:
            # Clear lockout on successful attempt
            if user_id in self.lockouts:
                del self.lockouts[user_id]
            
            return {
                'success': True,
                'locked_out': False,
                'remaining_attempts': self.max_attempts,
                'message': 'MFA verification successful'
            }
        
        # Handle failed attempt
        cutoff_time = now - timedelta(minutes=self.window_minutes)
        attempts = self.attempts[user_id]
        recent_attempts = sum(1 for attempt_time in attempts if attempt_time >= cutoff_time)
        
        remaining_attempts = max(0, self.max_attempts - recent_attempts)
        
        if remaining_attempts == 0:
            # Lock out the user
            failure_count = self.lockouts.get(user_id, {}).get('failure_count', 0) + 1
            
            # Exponential backoff: base_time * (2 ^ (failure_count - 1))
            lockout_duration = min(
                self.lockout_minutes * (2 ** (failure_count - 1)),
                self.max_lockout_minutes
            )
            
            locked_until = now + timedelta(minutes=lockout_duration)
            
            self.lockouts[user_id] = {
                'locked_until': locked_until,
                'failure_count': failure_count
            }
            
            logger.warning(f"User {user_id} locked out for {lockout_duration} minutes after {failure_count} failed attempts")
            
            return {
                'success': False,
                'locked_out': True,
                'remaining_attempts': 0,
                'lockout_duration_minutes': lockout_duration,
                'locked_until': locked_until,
                'failure_count': failure_count,
                'message': f'Account locked for {lockout_duration} minutes due to too many failed attempts'
            }
        
        return {
            'success': False,
            'locked_out': False,
            'remaining_attempts': remaining_attempts,
            'message': f'{remaining_attempts} attempts remaining before lockout'
        }
    
    def get_attempt_count(self, user_id: str) -> int:
        """Get current attempt count for user in window"""
        self._cleanup_old_data()
        
        now = now_ist()
        cutoff_time = now - timedelta(minutes=self.window_minutes)
        attempts = self.attempts[user_id]
        
        return sum(1 for attempt_time in attempts if attempt_time >= cutoff_time)
    
    def reset_user(self, user_id: str):
        """Reset rate limiting for a user (admin function)"""
        if user_id in self.attempts:
            del self.attempts[user_id]
        if user_id in self.lockouts:
            del self.lockouts[user_id]
        
        logger.info(f"Rate limiting reset for user {user_id}")


# Global rate limiter instance
mfa_rate_limiter = MFARateLimiter(
    max_attempts=5,      # 5 attempts per window
    window_minutes=15,   # 15-minute window
    lockout_minutes=30,  # Start with 30-minute lockout
    max_lockout_minutes=1440  # Max 24-hour lockout
)