"""
Processor service utility functions for data processing operations.
This service provides utility functions that can be used by other components.
"""

import logging
from typing import Dict, Any

from .processor_factory import ProcessorFactory

logger = logging.getLogger(__name__)


class ProcessorService:
    """
    Utility service for processor-related operations.
    Note: Direct processor execution is now handled by individual processor classes.
    """
    
    def __init__(self):
        self.processor_factory = ProcessorFactory()
    
    def get_processing_status(self, data_type: str) -> Dict[str, Any]:
        """
        Get processing status information for a specific data type.
        
        Args:
            data_type: The data type to check status for
            
        Returns:
            Dict containing status information
        """
        try:
            status_info = {
                "data_type": data_type,
                "supported": self.processor_factory.is_supported_data_type(data_type),
                "processor_available": False
            }
            
            if status_info["supported"]:
                processor = self.processor_factory.create_processor(data_type)
                status_info["processor_available"] = processor is not None
            
            return status_info
            
        except Exception as e:
            logger.error(f"Error getting processing status: {str(e)}")
            return {
                "data_type": data_type,
                "supported": False,
                "processor_available": False,
                "error": str(e)
            }
    
    def list_supported_data_types(self) -> list:
        """
        Get list of all supported data types.
        
        Returns:
            List of supported data type strings
        """
        return self.processor_factory.get_supported_data_types()
    
    def create_processor(self, data_type: str):
        """
        Create a processor instance for the given data type.
        
        Args:
            data_type: The type of data processor to create
            
        Returns:
            Processor instance or None if not supported
        """
        return self.processor_factory.create_processor(data_type)