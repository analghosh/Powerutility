"""
Parser Factory
Creates appropriate parser instances based on configuration
"""

import logging
from typing import Dict, Any, Optional

from libs.parsers.base_parser import BaseParser
from libs.parsers.standard_csv_parser import StandardCSVParser
from libs.parsers.trading_parser import TradingParser
from libs.parsers.supply_csv_parser import SupplyCSVParser
from libs.parsers.grid_india_parser import GridIndiaParser
from libs.parsers.renewable_parser import RenewableParser
from libs.parsers.iex_green_day_ahead_parser import IEXGreenDayAheadParser
from libs.parsers.standard_html_table_parser import StandardHTMLTableParser
from libs.parsers.rtm_optimization_parser import OptimizationParser, RTMOptimizationParser
from libs.parsers.hpx_exchange_parser import HPXExchangeParser
from libs.parsers.hpx_rtm_exchange_parser import HPXRTMExchangeParser
from libs.parsers.pxil_rtm_parser import PXILRTMParser
from libs.parsers.pxil_dam_parser import PXILDAMParser
from libs.parsers.pxil_gdam_parser import PXILGDAMParser
from libs.parsers.pxil_hpdam_parser import PXILHPDAMParser
from libs.parsers.pxil_dsm_parser import PXILDsmParser
from libs.parsers.hpx_green_day_ahead_parser import HPXGreenDayAheadParser
from libs.models.provider_models import ProviderEntity

logger = logging.getLogger(__name__)


class ParserFactory:
    """
    Factory class for creating parser instances based on provider configuration
    """
    
    # Registry of available parsers
    _parsers = {
        'standard': StandardCSVParser,
        'trading': TradingParser,
        'supply_csv': SupplyCSVParser,
        'supply': SupplyCSVParser,
        'grid_india': GridIndiaParser,
        'renewable': RenewableParser,
        'iex_green_day_ahead': IEXGreenDayAheadParser,
        'standard_html_table': StandardHTMLTableParser,
        'optimization': OptimizationParser,
        'rtm_optimization': RTMOptimizationParser,  # Alias for backwards compatibility
        'hpx_exchange': HPXExchangeParser,
        'hpx_rtm_exchange': HPXRTMExchangeParser,
        'hpx_green_day_ahead': HPXGreenDayAheadParser,
        'pxil_rtm': PXILRTMParser,
        'pxil_dam': PXILDAMParser,
        'pxil_gdam': PXILGDAMParser,
        'pxil_hpdam': PXILHPDAMParser,
        'pxil_dsm': PXILDsmParser,
    }
    
    @classmethod
    def create_parser(cls, provider: Optional[ProviderEntity] = None, parser_type: Optional[str] = None) -> BaseParser:
        """
        Create appropriate parser based on provider configuration or explicit type
        
        Args:
            provider: Provider entity containing metadata
            parser_type: Explicit parser type (overrides provider metadata)
            
        Returns:
            BaseParser: Appropriate parser instance
        """
        try:
            # Determine parser type
            if parser_type:
                selected_type = parser_type.lower()
            elif provider and provider.metadata:
                selected_type = provider.metadata.get('parser_type', 'standard').lower()
            else:
                selected_type = 'standard'  # Default fallback
            
            # Get parser class
            parser_class = cls._parsers.get(selected_type)
            if not parser_class:
                logger.warning("Unknown parser type '%s', falling back to standard parser", selected_type)
                parser_class = StandardCSVParser
            
            # Create and return parser instance
            parser_instance = parser_class()
            logger.info("Created parser: %s (type: %s)", parser_class.__name__, selected_type)
            return parser_instance
            
        except Exception as e:
            logger.error("Error creating parser: %s", e)
            # Fallback to standard parser
            logger.info("Falling back to standard CSV parser")
            return StandardCSVParser()
    
    @classmethod
    def get_available_parsers(cls) -> Dict[str, str]:
        """
        Get list of available parser types
        
        Returns:
            Dict[str, str]: Mapping of parser type to class name
        """
        return {parser_type: parser_class.__name__ for parser_type, parser_class in cls._parsers.items()}
    
    @classmethod
    def register_parser(cls, parser_type: str, parser_class: type) -> None:
        """
        Register a new parser type
        
        Args:
            parser_type: Parser type identifier
            parser_class: Parser class (must inherit from BaseParser)
        """
        if not issubclass(parser_class, BaseParser):
            raise ValueError(f"Parser class must inherit from BaseParser")
        
        cls._parsers[parser_type.lower()] = parser_class
        logger.info("Registered parser: %s -> %s", parser_type, parser_class.__name__)


# Convenience function for easy import
def create_parser(provider: Optional[ProviderEntity] = None, parser_type: Optional[str] = None) -> BaseParser:
    """
    Convenience function to create parser
    
    Args:
        provider: Provider entity containing metadata
        parser_type: Explicit parser type
        
    Returns:
        BaseParser: Appropriate parser instance
    """
    return ParserFactory.create_parser(provider, parser_type)