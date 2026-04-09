"""
Timeblock Helper Service
Provides utility functions for timeblock operations using timeblock_master collection
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from services.processing.app.v2.repository.base_repository import GenericRepository

logger = logging.getLogger(__name__)

# Time utilities
IST_OFFSET = timedelta(hours=5, minutes=30)
TOTAL_BLOCKS = 96
BLOCK_MINUTES = 15

def ist_now() -> datetime:
    """Get current IST time"""
    return datetime.now(timezone.utc) + IST_OFFSET

def block_from_hhmm(hhmm: str) -> int:
    """Calculate timeblock from HH:MM format (fallback method)"""
    hour, minute = map(int, hhmm.split(":"))
    return min((hour * 60 + minute) // BLOCK_MINUTES + 1, TOTAL_BLOCKS)

def block_label(block_id: int) -> str:
    """Generate timeblock label from block ID (fallback method)"""
    if block_id < 1 or block_id > TOTAL_BLOCKS:
        return "Invalid block"
    start = (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=(block_id - 1) * BLOCK_MINUTES)).time()
    end_raw = (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=block_id * BLOCK_MINUTES)).time()
    end = "00:00" if block_id == TOTAL_BLOCKS else end_raw.strftime("%H:%M")
    return f"{start.strftime('%H:%M')}-{end}"

class TimeblockHelper:
    """
    Helper service for timeblock operations using timeblock_master collection
    """
    
    def __init__(self, repository: Optional[GenericRepository] = None):
        """
        Initialize TimeblockHelper
        
        Args:
            repository: Optional GenericRepository instance. If None, creates a new one.
        """
        self.repository = repository or GenericRepository()
    
    async def get_current_timeblock(self) -> int:
        """
        Get current timeblock from timeblock_master collection
        
        Returns:
            Current timeblock number (1-96)
        """
        try:
            now = ist_now()
            current_time = now.strftime("%H:%M")
            
            # Find current timeblock from database
            current_timeblock_doc = await self.repository.find_one("timeblock_master", {
                "$and": [
                    {"starttime": {"$lte": current_time}},
                    {"$or": [
                        {"endtime": {"$gt": current_time}},
                        {"endtime": "00:00"}  # Handle midnight case
                    ]}
                ]
            })
            
            if current_timeblock_doc:
                return current_timeblock_doc.get('timeblock', 1)
            else:
                # Fallback to calculation if not found in DB
                logger.warning("Could not find current timeblock in database, using calculation")
                return block_from_hhmm(current_time)
                
        except Exception as e:
            logger.error(f"Error getting current timeblock from DB: {e}")
            # Fallback to calculation
            now = ist_now()
            current_time = now.strftime("%H:%M")
            return block_from_hhmm(current_time)
    
    async def get_timeblock_info(self, block_id: int) -> Dict[str, Any]:
        """
        Get timeblock information from timeblock_master collection
        
        Args:
            block_id: Timeblock number (1-96)
            
        Returns:
            Dictionary containing timeblock information
        """
        try:
            timeblock_doc = await self.repository.find_one("timeblock_master", {"timeblock": block_id})
            if timeblock_doc:
                return {
                    "timeblock": timeblock_doc.get('timeblock'),
                    "starttime": timeblock_doc.get('starttime'),
                    "endtime": timeblock_doc.get('endtime'),
                    "fulltime": timeblock_doc.get('fulltime'),
                    "label": f"{timeblock_doc.get('starttime')}-{timeblock_doc.get('endtime')}"
                }
            else:
                # Fallback to calculation
                return {
                    "timeblock": block_id,
                    "starttime": (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=(block_id - 1) * BLOCK_MINUTES)).strftime("%H:%M"),
                    "endtime": "00:00" if block_id == TOTAL_BLOCKS else (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=block_id * BLOCK_MINUTES)).strftime("%H:%M"),
                    "fulltime": ((block_id - 1) // 4) + 1,
                    "label": block_label(block_id)
                }
        except Exception as e:
            logger.error(f"Error getting timeblock info from DB for block {block_id}: {e}")
            # Fallback to calculation
            return {
                "timeblock": block_id,
                "starttime": (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=(block_id - 1) * BLOCK_MINUTES)).strftime("%H:%M"),
                "endtime": "00:00" if block_id == TOTAL_BLOCKS else (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=block_id * BLOCK_MINUTES)).strftime("%H:%M"),
                "fulltime": ((block_id - 1) // 4) + 1,
                "label": block_label(block_id)
            }
    
    async def get_timeblock_label(self, block_id: int) -> str:
        """
        Get timeblock label (starttime-endtime) for a given block ID
        
        Args:
            block_id: Timeblock number (1-96)
            
        Returns:
            Timeblock label string (e.g., "16:45-17:00")
        """
        timeblock_info = await self.get_timeblock_info(block_id)
        return timeblock_info.get('label', 'Invalid block')
    
    def window_blocks(self, current_block: int, before: int = 12, after: int = 11) -> List[Dict[str, Any]]:
        """
        Generate window of blocks around current block
        
        Args:
            current_block: Current timeblock number
            before: Number of blocks before current
            after: Number of blocks after current
            
        Returns:
            List of block dictionaries with block_id and full_time
        """
        res: List[Dict[str, Any]] = []
        for offset in range(-before, after + 1):
            bid = ((current_block - 1 + offset) % TOTAL_BLOCKS) + 1
            res.append({"block_id": bid, "full_time": block_label(bid)})
        return res
    
    async def get_all_timeblocks(self) -> List[Dict[str, Any]]:
        """
        Get all timeblocks from timeblock_master collection
        
        Returns:
            List of all timeblock documents sorted by timeblock number
        """
        try:
            return await self.repository.get_all("timeblock_master", {}, sort=[("timeblock", 1)])
        except Exception as e:
            logger.error(f"Error getting all timeblocks from DB: {e}")
            return []
    
    async def get_timeblocks_by_hour(self, hour: int) -> List[Dict[str, Any]]:
        """
        Get all timeblocks for a specific hour
        
        Args:
            hour: Hour (0-23)
            
        Returns:
            List of timeblock documents for the specified hour
        """
        try:
            hour_str = f"{hour:02d}:"
            return await self.repository.get_all("timeblock_master", {
                "starttime": {"$regex": f"^{hour_str}"}
            }, sort=[("timeblock", 1)])
        except Exception as e:
            logger.error(f"Error getting timeblocks for hour {hour}: {e}")
            return []
    
    async def validate_timeblock(self, block_id: int) -> bool:
        """
        Validate if a timeblock ID exists in the database
        
        Args:
            block_id: Timeblock number to validate
            
        Returns:
            True if timeblock exists, False otherwise
        """
        try:
            timeblock_doc = await self.repository.find_one("timeblock_master", {"timeblock": block_id})
            return timeblock_doc is not None
        except Exception as e:
            logger.error(f"Error validating timeblock {block_id}: {e}")
            return False

# Create a default instance for easy importing
timeblock_helper = TimeblockHelper()
