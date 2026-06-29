"""
Parameter Controller
Business logic for parameter CRUD operations
"""

import logging
from typing import Dict, Any, List, Optional

from ..services.parameter_service import parameter_service

logger = logging.getLogger(__name__)


class ParameterController:
    """Controller for parameter operations"""

    async def create_parameter(self, parameter_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a single parameter
        
        Args:
            parameter_data: Dictionary containing parameter data
            
        Returns:
            Created parameter
        """
        try:
            logger.info(f"Creating parameter with key: {parameter_data.get('para_key')}")
            result = await parameter_service.create_parameter(parameter_data)
            logger.info(f"Successfully created parameter")
            return result
            
        except Exception as e:
            logger.error(f"Error in parameter controller create: {e}")
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
            logger.info(f"Creating {len(parameters)} parameters in bulk")
            result = await parameter_service.create_parameters_bulk(parameters)
            logger.info(f"Successfully created {result['count']} parameters")
            return result
            
        except Exception as e:
            logger.error(f"Error in parameter controller bulk create: {e}")
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
            logger.info(f"Fetching parameter with ID: {parameter_id}")
            return await parameter_service.get_parameter_by_id(parameter_id)
            
        except Exception as e:
            logger.error(f"Error in parameter controller get by ID: {e}")
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
            logger.info(f"Fetching parameter with key: {para_key}")
            return await parameter_service.get_parameter_by_key(para_key)
            
        except Exception as e:
            logger.error(f"Error in parameter controller get by key: {e}")
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
            logger.info(f"Fetching all parameters (skip: {skip}, limit: {limit})")
            result = await parameter_service.get_all_parameters(
                skip=skip,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )
            logger.info(f"Successfully fetched {len(result['parameters'])} parameters")
            return result
            
        except Exception as e:
            logger.error(f"Error in parameter controller get all: {e}")
            raise

    async def update_parameter(
        self,
        parameter_id: str,
        update_data: Dict[str, Any],
        updated_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Update parameter by ID
        
        Args:
            parameter_id: Parameter ID
            update_data: Dictionary containing fields to update
            updated_by: User id for audit when updating idf_forecast_switch
            
        Returns:
            Updated parameter or None
        """
        try:
            logger.info(f"Updating parameter with ID: {parameter_id}")
            result = await parameter_service.update_parameter(
                parameter_id,
                update_data,
                updated_by=updated_by,
            )
            if result:
                logger.info(f"Successfully updated parameter")
            return result
            
        except Exception as e:
            logger.error(f"Error in parameter controller update: {e}")
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
            logger.info(f"Deleting parameter with ID: {parameter_id}")
            result = await parameter_service.delete_parameter(parameter_id)
            if result:
                logger.info(f"Successfully deleted parameter")
            return result
            
        except Exception as e:
            logger.error(f"Error in parameter controller delete: {e}")
            raise

    # ------------------------------------------------------------------
    # Bid configuration (parameter master)
    # ------------------------------------------------------------------

    async def get_bid_configuration(self) -> Dict[str, Any]:
        """Return active bid configuration."""
        try:
            return await parameter_service.get_bid_configuration()
        except Exception as e:
            logger.error(f"Error in bid configuration controller get: {e}")
            raise

    async def save_bid_configuration(
        self,
        config: Dict[str, Any],
        user_ctx: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Save bid configuration directly."""
        try:
            return await parameter_service.save_bid_configuration(config, user_ctx)
        except Exception as e:
            logger.error(f"Error in bid configuration controller save: {e}")
            raise

    async def get_active_markets(self) -> List[str]:
        try:
            return await parameter_service.get_active_markets()
        except Exception as e:
            logger.error(f"Error in active markets controller get: {e}")
            raise

    async def set_active_markets(self, markets: Any) -> Dict[str, Any]:
        try:
            return await parameter_service.set_active_markets(markets)
        except Exception as e:
            logger.error(f"Error in active markets controller set: {e}")
            raise

    async def get_active_market(self) -> str:
        try:
            return await parameter_service.get_active_market()
        except Exception as e:
            logger.error(f"Error in active market controller get: {e}")
            raise

    async def set_active_market(self, market: str) -> Dict[str, Any]:
        try:
            return await parameter_service.set_active_market(market)
        except Exception as e:
            logger.error(f"Error in active market controller set: {e}")
            raise

    async def get_active_exchange(self) -> str:
        try:
            return await parameter_service.get_active_exchange()
        except Exception as e:
            logger.error(f"Error in active exchange controller get: {e}")
            raise

    async def set_active_exchange(self, exchange: str) -> Dict[str, Any]:
        try:
            return await parameter_service.set_active_exchange(exchange)
        except Exception as e:
            logger.error(f"Error in active exchange controller set: {e}")
            raise


# Create singleton instance
parameter_controller = ParameterController()
