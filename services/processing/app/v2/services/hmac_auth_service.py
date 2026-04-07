"""
HMAC Authentication Service
Core HMAC signature verification logic
"""

import time
import hmac
import hashlib
import base64
import logging
from typing import Optional
from ...core.config import settings
from .redis_nonce_service import redis_nonce_service

logger = logging.getLogger(__name__)


class HMACAuthService:
    """Service for HMAC signature verification"""
    
    def __init__(self):
        self.allowed_time_drift = settings.HMAC_ALLOWED_TIME_DRIFT
        logger.info(f"HMACAuthService initialized with time drift: {self.allowed_time_drift}s")
    
    def compute_signature(
        self,
        secret: str,
        method: str,
        path: str,
        body_hash: str,
        timestamp: str,
        nonce: str
    ) -> str:
        """
        Compute HMAC signature
        
        Args:
            secret: The secret key
            method: HTTP method (GET, POST, etc.)
            path: Request path
            body_hash: SHA256 hash of request body
            timestamp: Request timestamp
            nonce: Unique nonce
            
        Returns:
            Base64-encoded HMAC signature
        """
        # Build string to sign
        string_to_sign = "\n".join([
            method.upper(),
            path,
            body_hash,
            timestamp,
            nonce
        ])
        
        # Compute HMAC-SHA256
        signature_bytes = hmac.new(
            secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        # Return base64-encoded signature
        return base64.b64encode(signature_bytes).decode('utf-8')
    
    def validate_timestamp(self, timestamp: str) -> bool:
        """
        Validate that timestamp is within allowed drift
        
        Args:
            timestamp: Request timestamp as string
            
        Returns:
            True if valid, False otherwise
        """
        try:
            request_time = int(timestamp)
            current_time = int(time.time())
            time_drift = abs(current_time - request_time)
            
            if time_drift > self.allowed_time_drift:
                logger.warning(
                    f"Timestamp validation failed: drift={time_drift}s, "
                    f"allowed={self.allowed_time_drift}s"
                )
                return False
            
            return True
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid timestamp format: {e}")
            return False
    
    def validate_nonce(self, api_key: str, nonce: str) -> bool:
        """
        Validate that nonce has not been used before
        
        Args:
            api_key: The API key
            nonce: The nonce to validate
            
        Returns:
            True if nonce is valid (not used), False otherwise
        """
        try:
            # Check if nonce already exists
            is_used = redis_nonce_service.is_nonce_used(api_key, nonce)
            if is_used:
                logger.warning(f"Replay attack detected: nonce already used for {api_key[:8]}...")
                return False
            
            # Mark nonce as used
            redis_nonce_service.mark_nonce_used(api_key, nonce)
            return True
        except Exception as e:
            logger.error(f"Error validating nonce: {e}")
            return False
    
    def verify_signature(
        self,
        method: str,
        path: str,
        body_hash: str,
        secret: str,
        provided_signature: str,
        timestamp: str,
        nonce: str
    ) -> bool:
        """
        Verify HMAC signature for a request
        
        Args:
            method: HTTP method
            path: Request path
            body_hash: SHA256 hash of request body
            secret: The secret key
            provided_signature: Signature from request header
            timestamp: Timestamp from request header
            nonce: Nonce from request header
            
        Returns:
            True if signature is valid, False otherwise
        """
        try:
            # Compute expected signature
            expected_signature = self.compute_signature(
                secret=secret,
                method=method,
                path=path,
                body_hash=body_hash,
                timestamp=timestamp,
                nonce=nonce
            )
            
            # Constant-time comparison to prevent timing attacks
            is_valid = hmac.compare_digest(expected_signature, provided_signature)
            
            if not is_valid:
                logger.warning(
                    f"Signature validation failed (path: {path})"
                )
            
            return is_valid
        except Exception as e:
            logger.error(f"Error verifying signature: {e}")
            return False
    
    def get_body_hash(self, body: bytes) -> str:
        """
        Compute SHA256 hash of request body
        
        Args:
            body: Request body as bytes
            
        Returns:
            Hex-encoded SHA256 hash
        """
        if not body:
            body = b""
        return hashlib.sha256(body).hexdigest()


# Create global HMAC auth service instance
hmac_auth_service = HMACAuthService()
