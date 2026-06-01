"""
API Key Repository for HMAC Authentication
Manages API keys and secrets in MongoDB
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from ..repository.base_repository import GenericRepository
from ..utils.encryption import encrypt_api_key, decrypt_api_key
from ...core.config import settings

logger = logging.getLogger(__name__)


class APIKeyRepository(GenericRepository):
    """
    Repository for API key management.
    Handles storage and retrieval of API keys and secrets.
    Uses encryption (not hashing) since we need the plain secret for HMAC verification.
    """
    
    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_API_KEYS_COLLECTION
        logger.info(f"APIKeyRepository initialized with collection: {self.collection_name}")
    
    def find_by_api_key(self, api_key: str, include_inactive: bool = False) -> Optional[Dict[str, Any]]:
        """
        Find API key document by api_key
        
        Args:
            api_key: The API key to lookup
            include_inactive: If True, find keys regardless of active status. If False, only find active keys.
            
        Returns:
            API key document if found, None otherwise
        """
        try:
            filter_dict = {"api_key": api_key}
            if not include_inactive:
                filter_dict["is_active"] = True
            
            doc = self.find_one(
                self.collection_name,
                filter_dict
            )
            return doc
        except Exception as e:
            logger.error(f"Error finding API key: {e}")
            return None
    
    def get_secret(self, api_key: str) -> Optional[str]:
        """
        Get decrypted secret for an API key
        
        Args:
            api_key: The API key
            
        Returns:
            Decrypted secret if found, None otherwise
        """
        try:
            doc = self.find_by_api_key(api_key)
            if not doc:
                return None
            
            encrypted_secret = doc.get("encrypted_secret")
            if not encrypted_secret:
                return None
            
            return decrypt_api_key(encrypted_secret)
        except Exception as e:
            logger.error(f"Error getting secret: {e}")
            return None
    
    def create_api_key(self, api_key: str, secret: str, name: str, description: Optional[str] = None, created_by: Optional[str] = None) -> Optional[str]:
        """
        Create a new API key
        
        Args:
            api_key: The API key identifier
            secret: The secret (will be encrypted)
            name: Name/identifier for this API key
            description: Optional description
            created_by: Optional username of admin who created the key
            
        Returns:
            Document ID if created successfully, None otherwise
        """
        try:
            # Check if API key already exists (check all keys, not just active ones)
            existing = self.find_by_api_key(api_key, include_inactive=True)
            if existing:
                logger.warning(f"API key already exists: {api_key}")
                return None
            
            # Encrypt the secret (we need plain secret for HMAC, so encrypt for storage)
            encrypted_secret = encrypt_api_key(secret)
            
            # Create document
            doc = {
                "api_key": api_key,
                "encrypted_secret": encrypted_secret,
                "name": name,
                "description": description or "",
                "is_active": True,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
                "last_used_at": None
            }
            
            # Add created_by if provided
            if created_by:
                doc["created_by"] = created_by
            
            result = self.insert_one(self.collection_name, doc)
            if result and result.inserted_id:
                logger.info(f"Created API key: {api_key} ({name}) by {created_by or 'unknown'}")
                return str(result.inserted_id)
            return None
        except Exception as e:
            logger.error(f"Error creating API key: {e}")
            return None
    
    def update_api_key(self, api_key: str, updates: Dict[str, Any]) -> bool:
        """
        Update an API key
        
        Args:
            api_key: The API key to update
            updates: Dictionary of fields to update
            
        Returns:
            True if updated successfully, False otherwise
        """
        try:
            updates["updated_at"] = datetime.now()
            
            # If updating secret, encrypt it
            if "secret" in updates:
                secret = updates.pop("secret")
                updates["encrypted_secret"] = encrypt_api_key(secret)
            
            result = self.update_one(
                self.collection_name,
                {"api_key": api_key},
                {"$set": updates}
            )
            
            if result and result.modified_count > 0:
                logger.info(f"Updated API key: {api_key}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating API key: {e}")
            return False
    
    def update_last_used(self, api_key: str) -> bool:
        """
        Update the last_used_at timestamp for an API key
        
        Args:
            api_key: The API key to update
            
        Returns:
            True if updated successfully, False otherwise
        """
        try:
            result = self.update_one(
                self.collection_name,
                {"api_key": api_key},
                {"$set": {"last_used_at": datetime.now()}}
            )
            return result and result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating last_used_at: {e}")
            return False
    
    def list_api_keys(self, filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        List all API keys
        
        Args:
            filter: Optional MongoDB filter
            
        Returns:
            List of API key documents
        """
        try:
            query_filter = filter or {}
            # Exclude encrypted_secret from results for security
            projection = {"encrypted_secret": 0}
            return self.get_all(
                self.collection_name,
                filter=query_filter,
                projection=projection
            )
        except Exception as e:
            logger.error(f"Error listing API keys: {e}")
            return []
    
    def revoke_api_key(self, api_key: str) -> bool:
        """
        Revoke (deactivate) an API key
        
        Args:
            api_key: The API key to revoke
            
        Returns:
            True if revoked successfully, False otherwise
        """
        try:
            return self.update_api_key(api_key, {"is_active": False})
        except Exception as e:
            logger.error(f"Error revoking API key: {e}")
            return False
    
    def regenerate_secret(self, api_key: str, new_secret: str) -> bool:
        """
        Regenerate secret for an API key
        
        Args:
            api_key: The API key
            new_secret: The new secret
            
        Returns:
            True if regenerated successfully, False otherwise
        """
        try:
            return self.update_api_key(api_key, {"secret": new_secret})
        except Exception as e:
            logger.error(f"Error regenerating secret: {e}")
            return False
