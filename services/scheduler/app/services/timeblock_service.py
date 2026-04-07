"""
Timeblock Service
Service for timeblock lookups and management
"""

import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class TimeblockService:
    """
    Service for timeblock operations and lookups
    """
    
    def __init__(self, timeblock_repository=None):
        """
        Initialize timeblock service
        
        Args:
            timeblock_repository: Repository for timeblock data access
        """
        self.repository = timeblock_repository or self._get_default_repository()
    
    def _get_default_repository(self):
        """Get default timeblock repository"""
        try:
            from ..repositories.timeblock_repository import TimeblockRepository
            return TimeblockRepository()
        except ImportError:
            logger.warning("Timeblock repository not available, using mock")
            return MockTimeblockRepository()
    
    def get_timeblock_by_time_range(self, time_start: str, time_end: str) -> Optional[int]:
        """
        Get timeblock number for given time range
        
        Args:
            time_start: Start time (HH:MM)
            time_end: End time (HH:MM)
            
        Returns:
            Optional[int]: Timeblock number or None if not found
        """
        try:
            if self.repository:
                return self.repository.find_timeblock_by_time_range(time_start, time_end)
            else:
                # Fallback logic - basic 15-minute timeblock calculation
                return self._calculate_timeblock_fallback(time_start, time_end)
                
        except Exception as e:
            logger.error("Error in timeblock lookup: %s", str(e))
            return None
    
    def _calculate_timeblock_fallback(self, time_start: str, time_end: str) -> Optional[int]:
        """
        Fallback timeblock calculation for 15-minute intervals
        
        Args:
            time_start: Start time (HH:MM)
            time_end: End time (HH:MM)
            
        Returns:
            Optional[int]: Calculated timeblock number
        """
        try:
            # Parse start time
            start_parts = time_start.split(':')
            start_hour = int(start_parts[0])
            start_minute = int(start_parts[1])
            
            # Calculate timeblock (assuming 15-minute intervals starting from 00:00)
            # Timeblock 1: 00:00-00:15, Timeblock 2: 00:15-00:30, etc.
            total_minutes = start_hour * 60 + start_minute
            timeblock = (total_minutes // 15) + 1
            
            # Validate timeblock range (1-96 for 24 hours in 15-minute intervals)
            if 1 <= timeblock <= 96:
                logger.debug("Calculated timeblock %d for time %s", timeblock, time_start)
                return timeblock
            else:
                logger.warning("Calculated timeblock %d is out of range for time %s", timeblock, time_start)
                return None
                
        except (ValueError, IndexError) as e:
            logger.error("Error calculating fallback timeblock for %s: %s", time_start, str(e))
            return None


class MockTimeblockRepository:
    """
    Mock timeblock repository for testing/development
    """
    
    def __init__(self):
        """Initialize mock repository with standard 15-minute timeblocks"""
        # Generate standard 15-minute timeblocks (1-96 for 24 hours)
        self.timeblocks = {}
        
        for i in range(96):
            timeblock_num = i + 1
            start_hour = (i * 15) // 60
            start_minute = (i * 15) % 60
            end_hour = ((i + 1) * 15) // 60
            end_minute = ((i + 1) * 15) % 60
            
            # Handle day rollover
            if end_hour >= 24:
                end_hour = 0
                end_minute = 0
            
            start_time = f"{start_hour:02d}:{start_minute:02d}"
            end_time = f"{end_hour:02d}:{end_minute:02d}" if end_hour != 0 or end_minute != 0 else "24:00"
            
            self.timeblocks[f"{start_time}-{end_time}"] = {
                'timeblock_number': timeblock_num,
                'start_time': start_time,
                'end_time': end_time,
                'description': f"Timeblock {timeblock_num}: {start_time}-{end_time}"
            }
        
        logger.info("Initialized mock timeblock repository with %d timeblocks", len(self.timeblocks))
    
    def find_timeblock_by_time_range(self, time_start: str, time_end: str) -> Optional[int]:
        """Find timeblock by exact time range match"""
        key = f"{time_start}-{time_end}"
        
        if key in self.timeblocks:
            timeblock_num = self.timeblocks[key]['timeblock_number']
            logger.debug("Mock: Found timeblock %d for %s", timeblock_num, key)
            return timeblock_num
        else:
            logger.warning("Mock: No timeblock found for %s", key)
            return None