"""
Parameter Service
Service layer for parameter business logic
"""

import logging
from typing import List, Dict, Any, Optional

from libs.constants.idf_forecast import IDF_FORECAST_SWITCH_KEY
from ..repository.parameter_repository import parameter_repository

logger = logging.getLogger(__name__)


class ParameterService:
    """Service for parameter operations"""

    @staticmethod
    def _is_idf_forecast_switch_update(
        para_key: Optional[str], update_data: Dict[str, Any]
    ) -> bool:
        return para_key == IDF_FORECAST_SWITCH_KEY and "para_value" in update_data

    async def create_parameter(self, parameter_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a single parameter
        
        Args:
            parameter_data: Dictionary containing parameter data
            
        Returns:
            Created parameter
        """
        try:
            # Validate required fields
            if "para_key" not in parameter_data:
                raise ValueError("para_key is required")
            
            if "para_value" not in parameter_data:
                raise ValueError("para_value is required")
            
            # Check if parameter with same key already exists
            existing = await parameter_repository.get_parameter_by_key(parameter_data["para_key"])
            if existing:
                raise ValueError(f"Parameter with key '{parameter_data['para_key']}' already exists")
            
            return await parameter_repository.create_parameter(parameter_data)
            
        except Exception as e:
            logger.error(f"Error in parameter service create: {e}")
            raise

    async def create_parameters_bulk(self, parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create multiple parameters in bulk
        
        Args:
            parameters: List of parameter dictionaries
            
        Returns:
            Dictionary with created count and IDs
        """
        try:
            # Validate all parameters
            for param in parameters:
                if "para_key" not in param:
                    raise ValueError("All parameters must have para_key")
                if "para_value" not in param:
                    raise ValueError("All parameters must have para_value")
            
            # Check for duplicate keys in the input
            keys = [param["para_key"] for param in parameters]
            if len(keys) != len(set(keys)):
                raise ValueError("Duplicate para_key found in input parameters")
            
            # Check if any keys already exist in database
            for key in keys:
                existing = await parameter_repository.get_parameter_by_key(key)
                if existing:
                    raise ValueError(f"Parameter with key '{key}' already exists")
            
            return await parameter_repository.create_parameters_bulk(parameters)
            
        except Exception as e:
            logger.error(f"Error in parameter service bulk create: {e}")
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
            return await parameter_repository.get_parameter_by_id(parameter_id)
            
        except Exception as e:
            logger.error(f"Error in parameter service get by ID: {e}")
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
            return await parameter_repository.get_parameter_by_key(para_key)
            
        except Exception as e:
            logger.error(f"Error in parameter service get by key: {e}")
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
            return await parameter_repository.get_all_parameters(
                skip=skip,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )
            
        except Exception as e:
            logger.error(f"Error in parameter service get all: {e}")
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
            Updated parameter or None
        """
        try:
            existing = await parameter_repository.get_parameter_by_id(parameter_id)
            if not existing:
                return None

            # Don't allow updating para_key to avoid conflicts
            if "para_key" in update_data:
                # Check if new key already exists
                key_existing = await parameter_repository.get_parameter_by_key(
                    update_data["para_key"]
                )
                if key_existing and str(key_existing.get("_id")) != parameter_id:
                    raise ValueError(
                        f"Parameter with key '{update_data['para_key']}' already exists"
                    )

            effective_key = update_data.get("para_key") or existing.get("para_key")
            if self._is_idf_forecast_switch_update(effective_key, update_data):
                from .idf_forecast_switch_service import idf_forecast_switch_service

                await idf_forecast_switch_service.validate_parameter_switch(
                    existing.get("para_value"),
                    update_data["para_value"],
                )

            result = await parameter_repository.update_parameter(parameter_id, update_data)
            if (
                result
                and self._is_idf_forecast_switch_update(effective_key, update_data)
            ):
                from .idf_forecast_switch_service import idf_forecast_switch_service

                result["merge_result"] = (
                    await idf_forecast_switch_service.merge_after_parameter_switch(
                        update_data["para_value"],
                        updated_by=update_data.get("updated_by"),
                    )
                )
            return result

        except Exception as e:
            logger.error(f"Error in parameter service update: {e}")
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
            return await parameter_repository.delete_parameter(parameter_id)
            
        except Exception as e:
            logger.error(f"Error in parameter service delete: {e}")
            raise


# Create singleton instance
parameter_service = ParameterService()
