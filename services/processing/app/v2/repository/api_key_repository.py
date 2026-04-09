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

    async def find_by_api_key(self, api_key: str, include_inactive: bool = False) -> Optional[Dict[str, Any]]:
        """Find API key document by api_key"""
        try:
            filter_dict = {"api_key": api_key}
            if not include_inactive:
                filter_dict["is_active"] = True
            return await self.find_one(self.collection_name, filter_dict)
        except Exception as e:
            logger.error(f"Error finding API key: {e}")
            return None

    async def get_secret(self, api_key: str) -> Optional[str]:
        """Get decrypted secret for an API key"""
        try:
            doc = await self.find_by_api_key(api_key)
            if not doc:
                return None
            encrypted_secret = doc.get("encrypted_secret")
            if not encrypted_secret:
                return None
            return decrypt_api_key(encrypted_secret)
        except Exception as e:
            logger.error(f"Error getting secret: {e}")
            return None

    async def create_api_key(self, api_key: str, secret: str, name: str, description: Optional[str] = None, created_by: Optional[str] = None) -> Optional[str]:
        """Create a new API key"""
        try:
            existing = await self.find_by_api_key(api_key, include_inactive=True)
            if existing:
                logger.warning(f"API key already exists: {api_key}")
                return None

            encrypted_secret = encrypt_api_key(secret)
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
            if created_by:
                doc["created_by"] = created_by

            result = await self.insert_one(self.collection_name, doc)
            if result and result.inserted_id:
                logger.info(f"Created API key: {api_key} ({name}) by {created_by or 'unknown'}")
                return str(result.inserted_id)
            return None
        except Exception as e:
            logger.error(f"Error creating API key: {e}")
            return None

    async def update_api_key(self, api_key: str, updates: Dict[str, Any]) -> bool:
        """Update an API key"""
        try:
            updates["updated_at"] = datetime.now()
            if "secret" in updates:
                secret = updates.pop("secret")
                updates["encrypted_secret"] = encrypt_api_key(secret)

            result = await self.update_one(
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

    async def update_last_used(self, api_key: str) -> bool:
        """Update the last_used_at timestamp for an API key"""
        try:
            result = await self.update_one(
                self.collection_name,
                {"api_key": api_key},
                {"$set": {"last_used_at": datetime.now()}}
            )
            return result and result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating last_used_at: {e}")
            return False

    async def list_api_keys(self, filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """List all API keys"""
        try:
            query_filter = filter or {}
            projection = {"encrypted_secret": 0}
            return await self.get_all(self.collection_name, filter=query_filter, projection=projection)
        except Exception as e:
            logger.error(f"Error listing API keys: {e}")
            return []

    async def revoke_api_key(self, api_key: str) -> bool:
        """Revoke (deactivate) an API key"""
        try:
            return await self.update_api_key(api_key, {"is_active": False})
        except Exception as e:
            logger.error(f"Error revoking API key: {e}")
            return False

    async def regenerate_secret(self, api_key: str, new_secret: str) -> bool:
        """Regenerate secret for an API key"""
        try:
            return await self.update_api_key(api_key, {"secret": new_secret})
        except Exception as e:
            logger.error(f"Error regenerating secret: {e}")
            return False
