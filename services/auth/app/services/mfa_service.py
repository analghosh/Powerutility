"""
MFA Service for TOTP-based two-factor authentication
"""

import base64
import qrcode
import io
import logging
from datetime import datetime
from typing import Optional, Tuple, Dict
import pyotp
from ..core.config import settings

logger = logging.getLogger(__name__)


class MFAService:
    """MFA service for TOTP authentication"""
    
    def __init__(self):
        # Use APP_NAME as issuer if available, otherwise fall back to MFA_ISSUER
        self.issuer = settings.APP_NAME or settings.MFA_ISSUER
        self.algorithm = settings.MFA_ALGORITHM
        self.digits = settings.MFA_DIGITS
        self.period = settings.MFA_PERIOD
        self.window = settings.MFA_WINDOW
    
    def generate_secret(self) -> str:
        """Generate a new TOTP secret"""
        return pyotp.random_base32()
    
    def generate_totp(self, secret: str) -> str:
        """Generate current TOTP code"""
        totp = pyotp.TOTP(
            secret,
            digits=self.digits,
            interval=self.period,
            name=self.issuer
        )
        return totp.now()
    
    def verify_totp(self, secret: str, code: str) -> bool:
        """Verify TOTP code with window buffer"""
        if not secret or not code:
            logger.warning("TOTP verification failed: empty secret or code")
            return False
            
        try:
            totp = pyotp.TOTP(
                secret,
                digits=self.digits,
                interval=self.period,
                name=self.issuer
            )
            # Use window parameter for time tolerance (1 window = ±30 seconds)
            result = totp.verify(code, valid_window=self.window)
            logger.info(f"TOTP verification result: {result} for code: {code}")
            return result
        except Exception as e:
            logger.error(f"TOTP verification error: {e}")
            return False
    
    def verify_totp_with_rate_limit(self, user_id: str, secret: str, code: str) -> Dict:
        """
        Verify TOTP code with rate limiting protection.
        
        Args:
            user_id: User identifier for rate limiting
            secret: TOTP secret
            code: TOTP code to verify
            
        Returns:
            Dict with verification result and rate limit info
        """
        from .mfa_rate_limiter import mfa_rate_limiter
        
        # Check if user can attempt
        if not mfa_rate_limiter.can_attempt(user_id):
            lockout_info = mfa_rate_limiter.get_lockout_info(user_id)
            logger.warning(f"MFA attempt blocked for user {user_id} - rate limited")
            
            return {
                'verified': False,
                'rate_limited': True,
                'lockout_info': lockout_info,
                'message': f"Too many failed attempts. Try again in {lockout_info['remaining_seconds']} seconds."
            }
        
        # Verify TOTP code
        is_valid = self.verify_totp(secret, code)
        
        # Record attempt with rate limiter
        rate_limit_result = mfa_rate_limiter.record_attempt(user_id, is_valid)
        
        return {
            'verified': is_valid,
            'rate_limited': rate_limit_result['locked_out'],
            'remaining_attempts': rate_limit_result['remaining_attempts'],
            'lockout_info': rate_limit_result.get('lockout_duration_minutes'),
            'message': rate_limit_result['message']
        }
    
    def generate_qr_code(self, secret: str, username: str) -> str:
        """Generate QR code for authenticator app"""
        # Create TOTP URI
        totp_uri = pyotp.totp.TOTP(
            secret,
            digits=self.digits,
            interval=self.period,
            name=self.issuer
        ).provisioning_uri(
            name=username,
            issuer_name=self.issuer
        )
        
        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(totp_uri)
        qr.make(fit=True)
        
        # Create QR code image
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Convert to base64
        buffer = io.BytesIO()
        img.save(buffer, 'PNG')
        img_str = base64.b64encode(buffer.getvalue()).decode()
        
        return f"data:image/png;base64,{img_str}"
    
    def get_remaining_time(self) -> int:
        """Get remaining seconds in current TOTP period"""
        # Import timezone utilities
        import sys
        import os
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
        from libs.audit_queue.timezone_utils import now_ist
        
        # Use IST time for TOTP calculations
        return int(self.period - (now_ist().timestamp() % self.period))


# Create global MFA service instance
mfa_service = MFAService() 