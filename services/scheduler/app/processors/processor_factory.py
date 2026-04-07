"""
Processor factory for creating appropriate processor instances based on data type.
"""

import logging
from typing import Optional

from .base_processor import BaseProcessor
from .exchange_processor import ExchangeProcessor
from .exchange_trade_processor import ExchangeTradeProcessor
from .bid_processor import BidProcessor
from .weather_processor import WeatherProcessor
from .demand_processor import DemandProcessor
from .supply_processor import SupplyProcessor
from .renewable_processor import RenewableProcessor
from .exchange_dsm_processor import ExchangeDsmProcessor
from .bid_trade_live_sync_processor import BidTradeLiveSyncProcessor


logger = logging.getLogger(__name__)


class ProcessorFactory:
    """
    Factory class for creating processor instances based on data type.
    """
    
    @staticmethod
    def create_processor(data_type: str) -> Optional[BaseProcessor]:
        """
        Create and return the appropriate processor instance based on data type.
        
        Args:
            data_type: The type of data to process (weather, bid, exchange, exchange_trade, demand, supply)
            
        Returns:
            BaseProcessor instance or None if data_type is not supported
        """
        try:
            data_type_lower = data_type.lower().strip()
            
            if data_type_lower == "exchange":
                return ExchangeProcessor()
            elif data_type_lower == "exchange_trade":
                return ExchangeTradeProcessor()
            elif data_type_lower == "bid":
                return BidProcessor()
            elif data_type_lower == "weather":
                return WeatherProcessor()
            elif data_type_lower == "demand":
                return DemandProcessor()
            elif data_type_lower == "supply":
                return SupplyProcessor()
            elif data_type_lower == "renewable":
                return RenewableProcessor()
            elif data_type_lower == "exchange_dsm":
                return ExchangeDsmProcessor()
            elif data_type_lower == "bid_trade_live_sync":
                return BidTradeLiveSyncProcessor()

            else:
                logger.error(f"Unsupported data type: {data_type}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating processor for data type '{data_type}': {str(e)}")
            return None
    
    @staticmethod
    def get_supported_data_types() -> list:
        """
        Get list of supported data types.
        
        Returns:
            List of supported data type strings
        """
        return ["exchange", "exchange_trade", "exchange_dsm", "bid", "weather", "demand", "supply", "renewable", "bid_trade_live_sync"]
    
    @staticmethod
    def is_supported_data_type(data_type: str) -> bool:
        """
        Check if a data type is supported.
        
        Args:
            data_type: The data type to check
            
        Returns:
            bool: True if supported, False otherwise
        """
        return data_type.lower().strip() in ProcessorFactory.get_supported_data_types()