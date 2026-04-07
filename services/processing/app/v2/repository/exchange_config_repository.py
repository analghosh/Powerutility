"""
Exchange Config Repository
Repository for managing exchange configuration settings (baseUrl, LoginUserId, ParticipantId, PortfolioId)
"""

from typing import Dict, Any, List, Optional
from bson import ObjectId
from datetime import datetime
import logging
from .base_repository import GenericRepository
from ..utils.encryption import encrypt_api_key, decrypt_api_key

logger = logging.getLogger(__name__)


def _decrypt_bearer_token(config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Decrypt BearerToken in a config document, falling back to stored value on failure."""
    if not config or not config.get("BearerToken"):
        return config
    try:
        config["BearerToken"] = decrypt_api_key(config["BearerToken"])
    except Exception as e:
        logger.warning(f"Failed to decrypt BearerToken for config {config.get('_id')}: {e}")
    return config


def _safe_encrypt_bearer_token(token: str) -> str:
    """
    Encrypt BearerToken only if it is not already encrypted.
    Prevents double-encryption if an already-encrypted value is passed in.
    """
    try:
        # If decrypt succeeds, the value is already a valid ciphertext — skip encryption
        decrypt_api_key(token)
        logger.debug("BearerToken is already encrypted, skipping re-encryption")
        return token
    except Exception:
        # Decryption failed → value is plaintext → encrypt it
        return encrypt_api_key(token)


class ExchangeConfigRepository(GenericRepository):
    """Repository for exchange configuration"""
    
    def __init__(self):
        super().__init__()
        self.collection_name = "exchange_config"

    async def get_active_config(self) -> Optional[Dict[str, Any]]:
        """
        Get the active exchange configuration (default single/DAM)
        """
        # Default to finding any active config, or specific default
        query = {"is_active": True}
        config = await self.find_one_async(self.collection_name, query)
        return _decrypt_bearer_token(config)

    async def get_bidType_market_active_config(self, bidType: str, market: str) -> Optional[Dict[str, Any]]:
        """
        Get the active exchange configuration
        
        Returns:
            Active exchange config document or None
        """
        query = {"is_active": True, "is_deleted": {"$ne": True}, "bidType": bidType, "market": market}
        config = await self.find_one_async(self.collection_name, query)
        return _decrypt_bearer_token(config)

    async def get_all_configs(self, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """
        Get all exchange configurations
        
        Args:
            include_deleted: If True, include soft-deleted records
            
        Returns:
            List of exchange config documents
        """
        filter_query = {}
        if not include_deleted:
            filter_query["is_deleted"] = {"$ne": True}
        
        configs = await self.get_all_async(
            self.collection_name,
            filter=filter_query,
            sort=[("_id", -1)]
        )
        return [_decrypt_bearer_token(c) for c in configs]

    async def create_config(self, data: Dict[str, Any]) -> Any:
        """Create a new exchange configuration"""
        if data.get("BearerToken"):
            try:
                data["BearerToken"] = _safe_encrypt_bearer_token(data["BearerToken"])
                data["encrypted"] = True
            except Exception as e:
                logger.error(f"Failed to encrypt BearerToken on create: {e}")
                raise
        data["created_at"] = datetime.now()
        data["updated_at"] = datetime.now()
        data["is_active"] = data.get("is_active", True)
        data["is_deleted"] = False
        return await self.insert_one_async(self.collection_name, data)

    async def update_config(self, id: str, data: Dict[str, Any]) -> Any:
        """Update an exchange configuration"""
        if data.get("BearerToken"):
            try:
                data["BearerToken"] = _safe_encrypt_bearer_token(data["BearerToken"])
                data["encrypted"] = True
            except Exception as e:
                logger.error(f"Failed to encrypt BearerToken on update: {e}")
                raise
        data["updated_at"] = datetime.now()
        return await self.update_one_async(
            self.collection_name, 
            {"_id": ObjectId(id)}, 
            {"$set": data}
        )

    async def find_by_id(self, id: str) -> Optional[Dict[str, Any]]:
        """Find configuration by ID"""
        config = await self.find_one_async(
            self.collection_name,
            {"_id": ObjectId(id), "is_deleted": {"$ne": True}}
        )
        return _decrypt_bearer_token(config)
        
    async def update_exchange_environment(self, exchange: str,environment: str,data: Dict[str, Any] = None) -> Dict[str, int]:
        try:
            now = datetime.now()
            data = data or {}

            # ✅ Normalize environment
            environment = environment.strip().upper()

            update_fields: Dict[str, Any] = {}

            for key, value in data.items():
                if value is None:
                    continue
                update_fields[key] = value

            update_fields["updated_at"] = now

            # 👉 Get is_active value
            is_active = update_fields.get("is_active")

            active_modified = 0
            inactive_modified = 0

            # 🔥 CASE 1: Activate environment (ONLY ONE ACTIVE ALLOWED)
            if is_active is True:

                # Activate selected
                result_active = await self.update_many_async(
                    self.collection_name,
                    {
                        "exchange": exchange,
                        "environment": environment,
                        "is_deleted": {"$ne": True}
                    },
                    {
                        "$set": {
                            **update_fields,
                            "is_active": True
                        }
                    }
                )

                # Deactivate others
                result_inactive = await self.update_many_async(
                    self.collection_name,
                    {
                        "exchange": exchange,
                        "environment": {"$ne": environment},
                        "is_deleted": {"$ne": True}
                    },
                    {
                        "$set": {
                            "is_active": False,
                            "updated_at": now
                        }
                    }
                )

                active_modified = result_active.modified_count
                inactive_modified = result_inactive.modified_count

            # 🔥 CASE 2: Deactivate only current (DO NOT TOUCH OTHERS)
            elif is_active is False:

                result = await self.update_many_async(
                    self.collection_name,
                    {
                        "exchange": exchange,
                        "environment": environment,
                        "is_deleted": {"$ne": True}
                    },
                    {
                        "$set": {
                            **update_fields,
                            "is_active": False
                        }
                    }
                )

                active_modified = result.modified_count
                inactive_modified = 0

            # 🔥 CASE 3: No is_active passed → just update fields
            else:

                result = await self.update_many_async(
                    self.collection_name,
                    {
                        "exchange": exchange,
                        "environment": environment,
                        "is_deleted": {"$ne": True}
                    },
                    {
                        "$set": update_fields
                    }
                )

                active_modified = result.modified_count
                inactive_modified = 0

            return {
                "active_modified": active_modified,
                "inactive_modified": inactive_modified
            }

        except Exception as e:
            logger.error(f"Error updating exchange configs: {e}")
            raise e

    async def get_active_list(self) -> List[Dict[str, Any]]:
        """
        Returns all active, non-deleted configs with _id, market, bidType, environment, exchange.
        """
        return await self.get_all_async(
            self.collection_name,
            filter={"is_active": True, "is_deleted": {"$ne": True}},
            projection={"_id": 1, "market": 1, "bidType": 1, "environment": 1, "exchange": 1},
            sort=[("market", 1), ("environment", 1)]
        )

    async def find_counterpart(self, market: str, bid_type: str, exchange: str, environment: str) -> Optional[Dict[str, Any]]:
        """
        Find a config by market, bidType, exchange, environment (used for environment switching).
        """
        return await self.find_one_async(
            self.collection_name,
            {
                "market": market,
                "bidType": bid_type,
                "exchange": exchange,
                "environment": environment,
                "is_deleted": {"$ne": True}
            }
        )


# Singleton instance
exchange_config_repository = ExchangeConfigRepository()
