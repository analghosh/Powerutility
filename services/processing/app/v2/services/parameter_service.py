"""
Parameter Service
Service layer for parameter business logic
"""

import logging
from typing import List, Dict, Any, Optional

from ..repository.parameter_repository import parameter_repository

logger = logging.getLogger(__name__)


# Default bid configuration used when no active record exists in DB.
DEFAULT_BID_CONFIGURATION = {
    "RTM": {
        "block_bid_buy": True,
        "block_bid_sell": True,
        "single_bid_buy": True,
        "single_bid_sell": True,
    },
    "GDAM": {
        "block_bid_buy": True,
        "block_bid_sell": True,
        "single_bid_buy": True,
        "single_bid_sell": True,
        "ocf": True,
        "premium": True,
        "discount": True,
    },
    "DAM": {
        "block_bid_buy": True,
        "block_bid_sell": True,
        "single_bid_buy": True,
        "single_bid_sell": True,
        "ocf": True,
        "premium": True,
        "discount": True,
    },
    "HPDAM": {
        "block_bid_buy": True,
        "block_bid_sell": True,
        "single_bid_buy": True,
        "single_bid_sell": True,
    },
}

BID_CONFIGURATION_KEY = "bid_configuration"


class ParameterService:
    """Service for parameter operations"""

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
            # Don't allow updating para_key to avoid conflicts
            if "para_key" in update_data:
                # Check if new key already exists
                existing = await parameter_repository.get_parameter_by_key(update_data["para_key"])
                if existing and str(existing.get("_id")) != parameter_id:
                    raise ValueError(f"Parameter with key '{update_data['para_key']}' already exists")
            
            return await parameter_repository.update_parameter(parameter_id, update_data)
            
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

    # ------------------------------------------------------------------
    # Bid configuration (parameter master)
    # ------------------------------------------------------------------

    def _normalize_bid_configuration(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize incoming bid configuration to only allowed markets and flags.
        Missing markets/flags are filled from the default.
        """
        normalized: Dict[str, Any] = {}
        for market, default_flags in DEFAULT_BID_CONFIGURATION.items():
            incoming_market = config.get(market, {}) if isinstance(config, dict) else {}
            normalized[market] = {}
            for flag in default_flags:
                value = incoming_market.get(flag)
                normalized[market][flag] = bool(value) if value is not None else default_flags[flag]
        return normalized

    async def get_bid_configuration(self) -> Dict[str, Any]:
        """
        Return the currently active bid configuration from parameters collection.
        Falls back to DEFAULT_BID_CONFIGURATION if not found.
        """
        try:
            record = await parameter_repository.get_parameter_by_key(BID_CONFIGURATION_KEY)
            if record and record.get("para_value"):
                return record["para_value"]
            return DEFAULT_BID_CONFIGURATION
        except Exception as e:
            logger.error(f"Error fetching bid configuration: {e}")
            raise

    async def save_bid_configuration(
        self,
        config: Dict[str, Any],
        user_ctx: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Save the bid configuration directly to the active parameters collection.
        It becomes active immediately application-wide.
        """
        try:
            normalized = self._normalize_bid_configuration(config)
            actor_id = user_ctx.get("user_id", "anonymous")

            record = await parameter_repository.update_parameter_by_key(
                BID_CONFIGURATION_KEY,
                {
                    "para_key": BID_CONFIGURATION_KEY,
                    "para_value": normalized,
                    "updated_by": actor_id,
                }
            )

            return {
                "para_key": BID_CONFIGURATION_KEY,
                "para_value": record.get("para_value") if record else normalized,
                "message": "Bid configuration saved successfully"
            }
        except Exception as e:
            logger.error(f"Error saving bid configuration: {e}")
            raise

    async def is_bid_enabled(self, market: str, flag: str) -> bool:
        """
        Helper for bidding services to check if a specific bid flag is enabled.
        Markets: RTM, GDAM, DAM, HPDAM.
        Flags: block_bid_buy, block_bid_sell, single_bid_buy, single_bid_sell,
               ocf, premium, discount (last three only for GDAM/DAM).
        """
        try:
            config = await self.get_bid_configuration()
            market_config = config.get(market, {})
            return bool(market_config.get(flag, False))
        except Exception as e:
            logger.error(f"Error checking bid flag {market}/{flag}: {e}")
            # Fail-safe: disable on error.
            return False

    async def get_active_market(self) -> str:
        """
        Return the currently configured active market for single-bid-table.
        Defaults to 'DAM' when not set or on error.
        """
        try:
            record = await parameter_repository.get_parameter_by_key("active_market")
            if record and record.get("para_value"):
                return str(record["para_value"]).strip().upper()
        except Exception as e:
            logger.error(f"Error reading active_market parameter: {e}")
        return "DAM"

    async def set_active_market(self, market: str) -> Dict[str, Any]:
        """
        Persist the active market parameter.
        """
        normalized = str(market).strip().upper()
        allowed = {"DAM", "GDAM", "HPDAM"}
        if normalized not in allowed:
            raise ValueError(f"Invalid active market '{market}'. Allowed: {sorted(allowed)}")
        record = await parameter_repository.update_parameter_by_key(
            "active_market",
            {"para_value": normalized}
        )
        return {
            "para_key": "active_market",
            "para_value": record.get("para_value") if record else normalized,
            "message": "Active market saved successfully"
        }

    async def get_active_exchange(self) -> str:
        """
        Return the currently configured active exchange for single-bid-table.
        Defaults to 'IEX' when not set or on error.
        """
        try:
            record = await parameter_repository.get_parameter_by_key("active_exchange")
            if record and record.get("para_value"):
                return str(record["para_value"]).strip().upper()
        except Exception as e:
            logger.error(f"Error reading active_exchange parameter: {e}")
        return "IEX"

    async def set_active_exchange(self, exchange: str) -> Dict[str, Any]:
        """
        Persist the active exchange parameter.
        """
        normalized = str(exchange).strip().upper()
        allowed = {"IEX", "PXIL", "HPX"}
        if normalized not in allowed:
            raise ValueError(f"Invalid active exchange '{exchange}'. Allowed: {sorted(allowed)}")
        record = await parameter_repository.update_parameter_by_key(
            "active_exchange",
            {"para_value": normalized}
        )
        return {
            "para_key": "active_exchange",
            "para_value": record.get("para_value") if record else normalized,
            "message": "Active exchange saved successfully"
        }


# Create singleton instance
parameter_service = ParameterService()
