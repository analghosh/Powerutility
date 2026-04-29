"""
Demand Test Raw Data Processor
Processes raw data for demand data type from test provider
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from .base_raw_data_processor import BaseRawDataProcessor
from .demand_data_processing import DemandDataProcessing

logger = logging.getLogger(__name__)


class DemandTestRawDataProcessor(BaseRawDataProcessor):
    """
    Raw data processor for demand data type from test provider
    Handles transformation of raw demand data into processed format
    """
    
    def __init__(self, mongodb_uri: str, database_name: str, provider_type: str = "test_data", data_type: str = "demand", audit_db_uri: Optional[str] = None):
        """
        Initialize the demand data processor
        
        Args:
            mongodb_uri: MongoDB connection URI for EPMS database
            database_name: EPMS database name
            provider_type: Data provider type (default: test_data)
            data_type: Data type to process (default: demand)
            audit_db_uri: MongoDB connection URI for audit database (optional)
        """
        super().__init__(
            mongodb_uri=mongodb_uri,
            database_name=database_name,
            provider_type=provider_type,
            data_type=data_type,
            audit_db_uri=audit_db_uri
        )
        
        # Initialize demand data processing service
        self.demand_data_processing = DemandDataProcessing()
        
        logger.info("Initialized DemandTestRawDataProcessor for provider: %s, data_type: %s", provider_type, data_type)
    
    async def _process_raw_data(self, raw_data_record: Dict[str, Any], status_record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process raw demand data from test_data provider using DemandDataProcessing service
        
        Args:
            raw_data_record: Raw data from time series collection
            status_record: Processing status record
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            logger.info("Processing test_data demand raw data: raw_data_id=%s", raw_data_record.get("_id"))
            
            # Use the DemandDataProcessing service to process the raw data
            processing_result = await self.demand_data_processing.process_demand_record(raw_data_record)
            
            if processing_result.get("success", False):
                processed_count = processing_result.get("processed_count", 0)
                errors = processing_result.get("errors", [])
                
                logger.info("Successfully processed test_data demand data: %d records processed", processed_count)
                
                if errors:
                    logger.warning("Processing completed with %d errors: %s", len(errors), errors)
                
                return {
                    "success": True,
                    "processed_records": processed_count,
                    "errors": errors,
                    "result": {
                        "data_type": self.data_type,
                        "provider_type": self.provider_type,
                        "processed_at": datetime.now().isoformat(),
                        "raw_data_id": raw_data_record.get("_id"),
                        "processing_errors": errors
                    }
                }
            else:
                errors = processing_result.get("errors", ["Unknown processing error"])
                error_message = "; ".join(errors) if isinstance(errors, list) else str(errors)
                
                logger.error("Failed to process test_data demand data: %s", error_message)
                
                return {
                    "success": False,
                    "error": error_message,
                    "processed_records": 0,
                    "errors": errors
                }
            
        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Error processing test_data demand raw data: %s", str(e))
            return {
                "success": False,
                "error": f"Processing failed: {str(e)}",
                "processed_records": 0
            }
    
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get processing statistics for demand test processor
        
        Returns:
            Dict[str, Any]: Statistics for this processor
        """
        try:
            # Get base statistics from parent class
            base_stats = super().get_statistics()
            
            # Add provider-specific statistics
            demand_stats = {
                "provider_type": self.provider_type,
                "data_type": self.data_type,
                "processor_name": "DemandTestRawDataProcessor",
                "base_statistics": base_stats
            }
            
            return demand_stats
            
        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Error getting statistics for demand test processor: %s", str(e))
            return {
                "error": str(e),
                "provider_type": self.provider_type,
                "data_type": self.data_type
            }
