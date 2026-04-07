"""
Encryption utility for sensitive data like API keys
Uses Fernet symmetric encryption from the cryptography library
"""

import os
import base64
import logging
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


class EncryptionService:
    """Service for encrypting and decrypting sensitive data"""
    
    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize encryption service
        
        Args:
            encryption_key: Base64-encoded encryption key. If not provided, 
                          will use ENCRYPTION_KEY from environment or generate one.
        """
        self.fernet = self._initialize_fernet(encryption_key)
    
    def _initialize_fernet(self, encryption_key: Optional[str] = None) -> Fernet:
        """
        Initialize Fernet cipher with encryption key
        
        Args:
            encryption_key: Base64-encoded encryption key
            
        Returns:
            Fernet instance
        """
        try:
            # Try to get encryption key from environment or use provided one
            key = encryption_key or os.getenv("ENCRYPTION_KEY")
            
            if key:
                # Validate that it's a valid Fernet key
                try:
                    # Decode base64 key
                    key_bytes = base64.urlsafe_b64decode(key)
                    if len(key_bytes) != 32:
                        raise ValueError("Encryption key must be 32 bytes when base64 decoded")
                    return Fernet(key)
                except Exception as e:
                    logger.error(f"Invalid encryption key format: {e}")
                    raise ValueError(f"Invalid encryption key: {e}")
            else:
                # Generate a new key from a password (for development/testing)
                # In production, you should set ENCRYPTION_KEY environment variable
                logger.warning(
                    "ENCRYPTION_KEY not set. Using default key derivation. "
                    "This is not secure for production. Please set ENCRYPTION_KEY environment variable."
                )
                # Use a default password-based key derivation (NOT SECURE FOR PRODUCTION)
                password = os.getenv("ENCRYPTION_PASSWORD", "default_epms_encryption_password_change_in_production").encode()
                salt = os.getenv("ENCRYPTION_SALT", "default_salt_change_me").encode()
                
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000,
                    backend=default_backend()
                )
                key = base64.urlsafe_b64encode(kdf.derive(password))
                return Fernet(key)
                
        except Exception as e:
            logger.error(f"Failed to initialize encryption service: {e}")
            raise
    
    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string
        
        Args:
            plaintext: String to encrypt
            
        Returns:
            Base64-encoded encrypted string
        """
        try:
            if not plaintext:
                return plaintext
            
            # Convert string to bytes
            plaintext_bytes = plaintext.encode('utf-8')
            
            # Encrypt
            encrypted_bytes = self.fernet.encrypt(plaintext_bytes)
            
            # Return as base64 string for easy storage
            return encrypted_bytes.decode('utf-8')
            
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise ValueError(f"Failed to encrypt data: {e}")
    
    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a ciphertext string
        
        Args:
            ciphertext: Base64-encoded encrypted string
            
        Returns:
            Decrypted plaintext string
        """
        try:
            if not ciphertext:
                return ciphertext
            
            # Convert string to bytes
            ciphertext_bytes = ciphertext.encode('utf-8')
            
            # Decrypt
            decrypted_bytes = self.fernet.decrypt(ciphertext_bytes)
            
            # Return as string
            return decrypted_bytes.decode('utf-8')
            
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise ValueError(f"Failed to decrypt data: {e}")
    
    @staticmethod
    def generate_key() -> str:
        """
        Generate a new encryption key (for initial setup)
        
        Returns:
            Base64-encoded encryption key
        """
        key = Fernet.generate_key()
        return key.decode('utf-8')


# Create singleton instance
_encryption_service: Optional[EncryptionService] = None


def get_encryption_service() -> EncryptionService:
    """
    Get or create singleton encryption service instance
    
    Returns:
        EncryptionService instance
    """
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service


def encrypt_api_key(api_key: Optional[str]) -> Optional[str]:
    """
    Convenience function to encrypt an API key
    
    Args:
        api_key: API key to encrypt
        
    Returns:
        Encrypted API key or None if input is None
    """
    if not api_key:
        return api_key
    
    try:
        service = get_encryption_service()
        return service.encrypt(api_key)
    except Exception as e:
        logger.error(f"Failed to encrypt API key: {e}")
        raise


def decrypt_api_key(encrypted_api_key: Optional[str]) -> Optional[str]:
    """
    Convenience function to decrypt an API key
    
    Args:
        encrypted_api_key: Encrypted API key to decrypt
        
    Returns:
        Decrypted API key or None if input is None
    """
    if not encrypted_api_key:
        return encrypted_api_key
    
    try:
        service = get_encryption_service()
        return service.decrypt(encrypted_api_key)
    except Exception as e:
        logger.error(f"Failed to decrypt API key: {e}")
        raise
