"""
Timeblock Validation Service
Validates timeblock values against the master timeblock collection
"""

import logging
from typing import List, Set, Optional, Dict, Any
from datetime import datetime

from services.ingestion.app.repositories.timeblock_repository import TimeblockRepository
from services.ingestion.app.core.config import settings


logger = logging.getLogger(__name__)


class TimeblockValidationService:
    """Service for validating timeblock values against database"""
    
    def __init__(self):
        """Initialize the timeblock validation service"""
        self.timeblock_repository = TimeblockRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )
    
    def get_valid_timeblocks(self) -> Set[int]:
        """
        Get valid timeblock numbers from database
        
        Returns:
            Set[int]: Set of valid timeblock numbers
        """
        try:
            return self.timeblock_repository.get_valid_timeblock_numbers()
        except Exception as e:
            logger.error("Error getting valid timeblocks: %s", e)
            return set(range(1, 97))  # Fallback range
    
    def is_valid_timeblock(self, timeblock: int) -> bool:
        """
        Check if timeblock number is valid
        
        Args:
            timeblock: Timeblock number to validate
            
        Returns:
            bool: True if valid, False otherwise
        """
        try:
            return self.timeblock_repository.is_valid_timeblock(timeblock)
        except Exception as e:
            logger.error("Error validating timeblock %d: %s", timeblock, e)
            return False
    
    def get_timeblock_info(self, timeblock: int) -> Optional[Dict[str, Any]]:
        """
        Get timeblock information from database
        
        Args:
            timeblock: Timeblock number
            
        Returns:
            Optional[Dict[str, Any]]: Timeblock info or None if not found
        """
        try:
            return self.timeblock_repository.get_timeblock_info(timeblock)
        except Exception as e:
            logger.error("Error getting timeblock info for %d: %s", timeblock, e)
            return None
    
    def get_timeblock_range(self) -> tuple[int, int]:
        """
        Get the valid timeblock range from database
        
        Returns:
            tuple[int, int]: (min_timeblock, max_timeblock)
        """
        try:
            return self.timeblock_repository.get_timeblock_range()
        except Exception as e:
            logger.error("Error getting timeblock range: %s", e)
            return (1, 96)  # Fallback range
    
    def validate_timeblock_list(self, timeblocks: List[int]) -> Dict[str, Any]:
        """
        Validate a list of timeblock numbers
        
        Args:
            timeblocks: List of timeblock numbers to validate
            
        Returns:
            Dict[str, Any]: Validation result with valid/invalid timeblocks
        """
        try:
            return self.timeblock_repository.validate_timeblock_list(timeblocks)
        except Exception as e:
            logger.error("Error validating timeblock list: %s", e)
            return {
                "is_valid": False,
                "valid_timeblocks": [],
                "invalid_timeblocks": timeblocks,
                "valid_range": (1, 96),
                "total_valid": 0,
                "total_invalid": len(timeblocks)
            }
    
    def get_validation_rule_for_timeblock(self) -> Dict[str, Any]:
        """
        Get validation rule for timeblock field based on database data
        
        Returns:
            Dict[str, Any]: Validation rule configuration
        """
        try:
            min_timeblock, max_timeblock = self.get_timeblock_range()
            valid_timeblocks = self.get_valid_timeblocks()
            
            return {
                "rule": "custom",
                "value": {
                    "min_value": min_timeblock,
                    "max_value": max_timeblock,
                    "valid_values": sorted(list(valid_timeblocks))
                },
                "message": f"Timeblock must be between {min_timeblock} and {max_timeblock} and exist in master timeblock collection"
            }
        except Exception as e:
            logger.error("Error getting validation rule for timeblock: %s", e)
            return {
                "rule": "custom",
                "value": {
                    "min_value": 1,
                    "max_value": 96,
                    "valid_values": list(range(1, 97))
                },
                "message": "Timeblock must be between 1 and 96 and exist in master timeblock collection"
            }
