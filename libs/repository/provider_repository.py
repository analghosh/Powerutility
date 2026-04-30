"""
Provider Repository
Database operations for provider management
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from bson import ObjectId

from libs.database.base_repository import BaseRepository
from libs.models.provider_models import (
    ProviderEntity, IngestionType, DataType
)


logger = logging.getLogger(__name__)


class ProviderRepository(BaseRepository):
    """Repository for provider management"""
    
    def __init__(self, mongodb_uri: str, database_name: str):
        self.collection_name = "providers"
        super().__init__(self.collection_name, mongodb_uri, database_name)

    async def ensure_indexes(self):
        """Ensure indexes exist on the providers collection — call from app startup"""
        try:
            await self.collection.create_index([
                ("code", 1),
                ("ingestion_type", 1),
                ("data_type", 1)
            ], unique=True)
            await self.collection.create_index([
                ("ingestion_type", 1),
                ("data_type", 1),
                ("enabled", 1)
            ])
            await self.collection.create_index([("enabled", 1)])
            await self.collection.create_index([("ingestion_type", 1)])
            await self.collection.create_index([("data_type", 1)])
            logger.info("Created indexes for providers collection")
        except Exception as e:
            logger.error("Error ensuring providers indexes: %s", e)
    
    async def create_provider(self, provider: ProviderEntity) -> str:
        """
        Create a new provider
        
        Args:
            provider: Provider entity
            
        Returns:
            str: Created provider ID
        """
        try:
            provider_dict = provider.model_dump(by_alias=True, exclude={"id"})
            provider_dict["created_at"] = datetime.now()
            provider_dict["updated_at"] = datetime.now()
            
            result = await self.collection.insert_one(provider_dict)
            provider_id = str(result.inserted_id)
            
            logger.info("Created provider: %s (%s)", provider_id, provider.code)
            return provider_id
            
        except Exception as e:
            logger.error("Error creating provider: %s", e)
            raise
    
    async def get_provider_by_id(self, provider_id: str) -> Optional[ProviderEntity]:
        """
        Get provider by ID
        
        Args:
            provider_id: Provider ID
            
        Returns:
            Optional[ProviderEntity]: Provider entity or None
        """
        try:
            provider_doc = await self.collection.find_one({"_id": ObjectId(provider_id)})
            
            if provider_doc:
                provider_doc["_id"] = str(provider_doc["_id"])
                # Let the field validators handle the conversion
                return ProviderEntity(**provider_doc)
            
            return None
            
        except Exception as e:
            logger.error("Error getting provider by ID: %s", e)
            raise
    
    async def get_provider_by_code(self, code: str) -> Optional[ProviderEntity]:
        """
        Get provider by code
        
        Args:
            code: Provider code
            
        Returns:
            Optional[ProviderEntity]: Provider entity or None
        """
        try:
            provider_doc = await self.collection.find_one({"code": code})
            
            if provider_doc:
                provider_doc["_id"] = str(provider_doc["_id"])
                # Let the field validators handle the conversion
                return ProviderEntity(**provider_doc)
            
            return None
            
        except Exception as e:
            logger.error("Error getting provider by code: %s", e)
            raise
    
    async def get_provider_by_code_and_data_type(self, code: str, data_type: str) -> Optional[ProviderEntity]:
        """
        Get provider by code and data type
        
        Args:
            code: Provider code
            data_type: Data type
            
        Returns:
            Optional[ProviderEntity]: Provider entity or None
        """
        try:
            provider_doc = self.collection.find_one({
                "code": code,
                "data_type": data_type
            })
            
            if provider_doc:
                provider_doc["_id"] = str(provider_doc["_id"])
                # Let the field validators handle the conversion
                return ProviderEntity(**provider_doc)
            
            return None
            
        except Exception as e:
            logger.error("Error getting provider by code and data type: %s", e)
            raise
    
    async def get_all_providers(
        self,
        ingestion_type: Optional[IngestionType] = None,
        data_type: Optional[DataType] = None,
        enabled: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[ProviderEntity]:
        """
        Get all providers with optional filters
        
        Args:
            ingestion_type: Filter by ingestion type
            data_type: Filter by data type
            enabled: Filter by enabled status
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            List[ProviderEntity]: List of providers
        """
        try:
            query = {}
            
            if ingestion_type:
                query["ingestion_type"] = ingestion_type.value
            
            if data_type:
                query["data_type"] = data_type.value
            
            if enabled is not None:
                query["enabled"] = enabled
            
            cursor = self.collection.find(query).skip(skip).limit(limit).sort("created_at", -1)
            docs = await cursor.to_list(length=limit)
            
            providers = []
            for doc in docs:
                doc["_id"] = str(doc["_id"])
                # Let the field validators handle the conversion
                providers.append(ProviderEntity(**doc))
            
            logger.info("Retrieved %d providers", len(providers))
            return providers
            
        except Exception as e:
            logger.error("Error getting all providers: %s", e)
            raise
    
    async def count_providers(
        self,
        ingestion_type: Optional[IngestionType] = None,
        data_type: Optional[DataType] = None,
        enabled: Optional[bool] = None
    ) -> int:
        """
        Count providers with optional filters
        
        Args:
            ingestion_type: Filter by ingestion type
            data_type: Filter by data type
            enabled: Filter by enabled status
            
        Returns:
            int: Number of providers matching the criteria
        """
        try:
            query = {}
            
            if ingestion_type:
                query["ingestion_type"] = ingestion_type.value
            
            if data_type:
                query["data_type"] = data_type.value
            
            if enabled is not None:
                query["enabled"] = enabled
            
            count = await self.collection.count_documents(query)
            logger.info("Counted %d providers", count)
            return count
            
        except Exception as e:
            logger.error("Error counting providers: %s", e)
            raise
    
    async def update_provider(self, provider_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update provider
        
        Args:
            provider_id: Provider ID
            updates: Updates to apply
            
        Returns:
            bool: True if updated successfully
        """
        try:
            updates["updated_at"] = datetime.now()
            
            result = await self.collection.update_one(
                {"_id": ObjectId(provider_id)},
                {"$set": updates}
            )
            
            if result.modified_count > 0:
                logger.info("Updated provider: %s", provider_id)
                return True
            
            logger.warning("No provider found to update: %s", provider_id)
            return False
            
        except Exception as e:
            logger.error("Error updating provider: %s", e)
            raise
    
    async def delete_provider(self, provider_id: str) -> bool:
        """
        Delete provider
        
        Args:
            provider_id: Provider ID
            
        Returns:
            bool: True if deleted successfully
        """
        try:
            result = await self.collection.delete_one({"_id": ObjectId(provider_id)})
            
            if result.deleted_count > 0:
                logger.info("Deleted provider: %s", provider_id)
                return True
            
            logger.warning("No provider found to delete: %s", provider_id)
            return False
            
        except Exception as e:
            logger.error("Error deleting provider: %s", e)
            raise
    
    async def enable_disable_provider(self, provider_id: str, enabled: bool) -> bool:
        """
        Enable or disable provider
        
        Args:
            provider_id: Provider ID
            enabled: Whether to enable or disable
            
        Returns:
            bool: True if updated successfully
        """
        try:
            return await self.update_provider(provider_id, {"enabled": enabled})
            
        except Exception as e:
            logger.error("Error enabling/disabling provider: %s", e)
            raise
    
    async def get_providers_by_ingestion_type(self, ingestion_type: IngestionType) -> List[ProviderEntity]:
        """
        Get providers by ingestion type
        
        Args:
            ingestion_type: Ingestion type
            
        Returns:
            List[ProviderEntity]: List of providers
        """
        try:
            return await self.get_all_providers(ingestion_type=ingestion_type)
            
        except Exception as e:
            logger.error("Error getting providers by ingestion type: %s", e)
            raise
    
    async def get_providers_by_data_type(self, data_type: DataType) -> List[ProviderEntity]:
        """
        Get providers by data type
        
        Args:
            data_type: Data type
            
        Returns:
            List[ProviderEntity]: List of providers
        """
        try:
            return await self.get_all_providers(data_type=data_type)
            
        except Exception as e:
            logger.error("Error getting providers by data type: %s", e)
            raise
    
    async def get_enabled_providers(self) -> List[ProviderEntity]:
        """
        Get all enabled providers
        
        Returns:
            List[ProviderEntity]: List of enabled providers
        """
        try:
            return await self.get_all_providers(enabled=True)
            
        except Exception as e:
            logger.error("Error getting enabled providers: %s", e)
            raise
    
    async def check_code_exists(self, code: str, exclude_id: Optional[str] = None) -> bool:
        """
        Check if provider code already exists
        
        Args:
            code: Provider code to check
            exclude_id: Provider ID to exclude from check (for updates)
            
        Returns:
            bool: True if code exists
        """
        try:
            query = {"code": code}
            
            if exclude_id:
                query["_id"] = {"$ne": ObjectId(exclude_id)}  # type: ignore
            
            count = await self.collection.count_documents(query)
            return count > 0
        except Exception as e:
            logger.error("Error checking code existence: %s", e)
            raise
    
    async def check_provider_combination_exists(
        self, 
        code: str, 
        ingestion_type: IngestionType, 
        data_type: DataType,
        exclude_id: Optional[str] = None
    ) -> bool:
        """
        Check if provider combination (code + ingestion_type + data_type) already exists
        
        Args:
            code: Provider code to check
            ingestion_type: Ingestion type to check
            data_type: Data type to check
            exclude_id: Provider ID to exclude from check (for updates)
            
        Returns:
            bool: True if combination exists
        """
        try:
            query = {
                "code": code,
                "ingestion_type": ingestion_type.value,
                "data_type": data_type.value
            }
            
            if exclude_id:
                query["_id"] = {"$ne": ObjectId(exclude_id)}  # type: ignore
            
            count = await self.collection.count_documents(query)
            return count > 0
            
        except Exception as e:
            logger.error("Error checking provider combination existence: %s", e)
            raise
