"""
Raw Data Provider Interface
Defines the common interface for all raw data providers with different data provision approaches
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from enum import Enum
from datetime import datetime


class DataIngestionMethod(str, Enum):
    """Enum for data ingestion methods"""
    UPLOAD = "upload"  # File upload (CSV, JSON, XML)
    API = "api"  # REST/GraphQL API endpoints
    FTP = "ftp"  # FTP/SFTP file transfer
    SCRAPE = "scrape"  # Web scraping data collection

class RawDataProvider(ABC):
    """
    Abstract base class for raw data providers
    Each provider implements this interface with specific ingestion methods
    """
    
    def __init__(self, ingestion_method: DataIngestionMethod):
        self.ingestion_method = ingestion_method
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
    
    # Common utility methods
    def _log_operation(self, operation: str, success: bool, details: str = ""):
        """Standardized logging for all operations"""
        status = "SUCCESS" if success else "FAILED"
        self.logger.info("%s - %s - %s", operation, status, details)
    
    def _handle_error(self, operation: str, error: Exception, context: str = ""):
        """Standardized error handling"""
        self.logger.error("%s - ERROR - %s: %s", operation, context, str(error), exc_info=True)
        return False
    
    def _create_response(self, success: bool, data: Any = None, message: str = "", error: Optional[Exception] = None) -> Dict[str, Any]:
        """Create standardized response format"""
        response = {
            "success": success,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "provider": {
                "ingestion_method": self.ingestion_method.value,
            }
        }
        
        if success and data is not None:
            response["data"] = data
        elif not success and error:
            response["error"] = {
                "type": error.__class__.__name__,
                "message": str(error)
            }
        
        return response
    
    @abstractmethod
    def get_pending_data(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get pending raw data records in insertion order
        
        Args:
            limit: Maximum number of records to fetch
            
        Returns:
            List[Dict[str, Any]]: List of pending raw data records
        """
        raise NotImplementedError
    
    @abstractmethod
    def translate_data(self, raw_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Translate raw data to serve format
        
        Args:
            raw_data: Raw data record
            
        Returns:
            List[Dict[str, Any]]: Translated data in serve format
        """
        raise NotImplementedError
    
    @abstractmethod
    def mark_as_processing(self, raw_data_id: str) -> bool:
        """
        Mark raw data record as processing
        
        Args:
            raw_data_id: ID of the raw data record
            
        Returns:
            bool: True if successful, False otherwise
        """
        raise NotImplementedError
    
    @abstractmethod
    def mark_as_completed(self, raw_data_id: str) -> bool:
        """
        Mark raw data record as completed
        
        Args:
            raw_data_id: ID of the raw data record
            
        Returns:
            bool: True if successful, False otherwise
        """
        raise NotImplementedError
    
    @abstractmethod
    def mark_as_failed(self, raw_data_id: str, error_message: str) -> bool:
        """
        Mark raw data record as failed
        
        Args:
            raw_data_id: ID of the raw data record
            error_message: Error message to store
            
        Returns:
            bool: True if successful, False otherwise
        """
        raise NotImplementedError
    
    @abstractmethod
    def get_data_type(self, raw_data: Dict[str, Any]) -> str:
        """
        Determine the data type (demand/supply) from raw data
        
        Args:
            raw_data: Raw data record
            
        Returns:
            str: Data type ('demand' or 'supply')
        """
        raise NotImplementedError
    
    # Provider-specific methods (optional overrides)
    
    def validate_data_format(self, _data: Any) -> bool:
        """
        Validate data format for this provider
        
        Args:
            data: Data to validate
            
        Returns:
            bool: True if valid format
        """
        return True
