"""
Parameter Repository
Repository for parameter CRUD operations
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from bson import ObjectId

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)


class ParameterRepository:
    """Repository for parameter collection operations"""

    def __init__(self):
        self.repo = GenericRepository()
        self.collection_name = "parameters"

    async def create_parameter(self, parameter_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a single parameter
        
        Args:
            parameter_data: Dictionary containing parameter data
            
        Returns:
            Created parameter with ID
        """
        try:
            # Add timestamps
            now = datetime.now()
            parameter_data["created_at"] = now
            parameter_data["updated_at"] = now
            
            result = await self.repo.insert_one_async(self.collection_name, parameter_data)
            
            logger.info(f"Created parameter with ID: {result.inserted_id}")
            
            # Return the created parameter
            return await self.repo.find_one_async(
                self.collection_name,
                {"_id": result.inserted_id}
            )
            
        except Exception as e:
            logger.error(f"Error creating parameter: {e}")
            raise

    async def create_parameters_bulk(self, parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create multiple parameters in bulk
        
        Args:
            parameters: List of parameter dictionaries
            
        Returns:
            Dictionary with inserted IDs and count
        """
        try:
            # Add timestamps to all parameters
            now = datetime.now()
            for param in parameters:
                param["created_at"] = now
                param["updated_at"] = now
            
            result = await self.repo.insert_many_async(self.collection_name, parameters)
            
            logger.info(f"Created {len(result.inserted_ids)} parameters in bulk")
            
            return {
                "inserted_ids": [str(id) for id in result.inserted_ids],
                "count": len(result.inserted_ids)
            }
            
        except Exception as e:
            logger.error(f"Error creating parameters in bulk: {e}")
            raise

    async def get_parameter_by_id(self, parameter_id: str) -> Optional[Dict[str, Any]]:
        """
        Get parameter by ID
        
        Args:
            parameter_id: Parameter ID
            
        Returns:
            Parameter document or None
        """
        try:
            if not ObjectId.is_valid(parameter_id):
                return None
            
            return await self.repo.find_one_async(
                self.collection_name,
                {"_id": ObjectId(parameter_id)}
            )
            
        except Exception as e:
            logger.error(f"Error getting parameter by ID: {e}")
            raise

    async def get_parameter_by_key(self, para_key: str) -> Optional[Dict[str, Any]]:
        """
        Get parameter by key
        
        Args:
            para_key: Parameter key
            
        Returns:
            Parameter document or None
        """
        try:
            return await self.repo.find_one_async(
                self.collection_name,
                {"para_key": para_key}
            )
            
        except Exception as e:
            logger.error(f"Error getting parameter by key: {e}")
            raise

    async def get_all_parameters(
        self,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        sort_order: int = -1
    ) -> Dict[str, Any]:
        """
        Get all parameters with pagination
        
        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            sort_by: Field to sort by
            sort_order: Sort order (1 for ascending, -1 for descending)
            
        Returns:
            Dictionary with parameters and metadata
        """
        try:
            # Get total count
            total = self.repo.get_count(self.collection_name)
            
            # Get parameters with pagination
            parameters = await self.repo.get_all_async(
                self.collection_name,
                skip=skip,
                limit=limit,
                sort=[(sort_by, sort_order)]
            )
            
            return {
                "parameters": parameters,
                "total": total,
                "skip": skip,
                "limit": limit
            }
            
        except Exception as e:
            logger.error(f"Error getting all parameters: {e}")
            raise

    async def update_parameter(
        self,
        parameter_id: str,
        update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Update parameter by ID
        
        Args:
            parameter_id: Parameter ID
            update_data: Dictionary containing fields to update
            
        Returns:
            Updated parameter document or None
        """
        try:
            if not ObjectId.is_valid(parameter_id):
                return None
            
            # Add updated_at timestamp
            update_data["updated_at"] = datetime.now()
            
            result = await self.repo.update_one_async(
                self.collection_name,
                {"_id": ObjectId(parameter_id)},
                {"$set": update_data}
            )
            
            if result.modified_count > 0:
                logger.info(f"Updated parameter with ID: {parameter_id}")
                return await self.get_parameter_by_id(parameter_id)
            
            return None
            
        except Exception as e:
            logger.error(f"Error updating parameter: {e}")
            raise

    async def delete_parameter(self, parameter_id: str) -> bool:
        """
        Delete parameter by ID
        
        Args:
            parameter_id: Parameter ID
            
        Returns:
            True if deleted, False otherwise
        """
        try:
            if not ObjectId.is_valid(parameter_id):
                return False
            
            result = await self.repo.delete_one(
                self.collection_name,
                {"_id": ObjectId(parameter_id)}
            )
            
            if result.deleted_count > 0:
                logger.info(f"Deleted parameter with ID: {parameter_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error deleting parameter: {e}")
            raise


# Create singleton instance
parameter_repository = ParameterRepository()
