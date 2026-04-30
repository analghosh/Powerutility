"""
Timeblock Seeder
Populates the database with timeblock master data
Each timeblock represents a 15-minute interval
1 fulltime (hour) contains 4 timeblocks
"""

import logging
from datetime import time
from typing import List, Dict, Any

from ..repository.timeblock_repository import TimeblockRepository

logger = logging.getLogger(__name__)


class TimeblockSeeder:
    """Seeder for timeblock master data"""
    
    def __init__(self):
        """Initialize the seeder"""
        self.repository = TimeblockRepository()
    
    def generate_timeblocks(self) -> List[Dict[str, Any]]:
        """Generate timeblock data for 24 hours"""
        return [
            {
                "timeblock": hour * 4 + block + 1,
                "starttime": time(hour, block * 15).strftime("%H:%M"),
                "endtime": self._format_endtime(
                    (hour + 1) % 24 if block * 15 + 15 >= 60 else hour,
                    (block * 15 + 15) % 60
                ),
                "fulltime": (hour * 4 + block) // 4 + 1
            }
            for hour in range(24)
            for block in range(4)
        ]
    
    def _format_endtime(self, hour: int, minute: int) -> str:
        """Format endtime, converting 00:00 to 24:00"""
        time_str = time(hour, minute).strftime("%H:%M")
        return "24:00" if time_str == "00:00" else time_str
    
    def seed(self) -> Dict[str, Any]:
        """Seed the database with timeblock data"""
        try:
            # Check if data already exists
            existing_count = self.repository.count({})
            if existing_count == 96:
                return {
                    "status": "skipped",
                    "message": f"Timeblock data already exists ({existing_count} records)",
                    "count": existing_count
                }
            
            if existing_count < 96:
                # Clear existing incomplete data
                clear_result = self.clear()
                if clear_result['status'] != 'success':
                    return clear_result
                # Continue with seeding (don't return here)
            
            # Generate timeblock data
            timeblocks = self.generate_timeblocks()
            
            # Insert data using the repository
            try:
                inserted_count = 0
                for timeblock in timeblocks:
                    inserted_id = self.repository.create(timeblock, add_timestamps=False)
                    if inserted_id:
                        inserted_count += 1
                
                logger.info("Successfully seeded %d timeblocks", inserted_count)
                
                return {
                    "status": "success",
                    "message": f"Successfully seeded {inserted_count} timeblocks",
                    "count": inserted_count
                }
                
            except Exception as e:
                logger.error("Error during timeblock insertion: %s", str(e))
                return {
                    "status": "error",
                    "message": f"Error during timeblock insertion: {str(e)}",
                    "count": 0
                }
            
        except Exception as e:
            error_msg = f"Error during timeblock seeding: {str(e)}"
            logger.error(error_msg)
            return {
                "status": "error",
                "message": error_msg,
                "count": 0
            }

    def clear(self) -> Dict[str, Any]:
        """Clear the database of timeblock data"""
        try:
            # Delete all documents in the collection
            result = self.repository.collection.delete_many({})
            deleted_count = result.deleted_count
            
            logger.info("Cleared %d timeblocks", deleted_count)
            
            return {
                "status": "success",
                "message": f"Successfully cleared {deleted_count} timeblocks",
                "count": deleted_count
            }
        except Exception as e:
            logger.error("Error during timeblock clearing: %s", str(e))
            return {
                "status": "error",
                "message": f"Error during timeblock clearing: {str(e)}",
                "count": 0
            }
    
    def get_existing_timeblocks(self) -> List[Dict[str, Any]]:
        """Get existing timeblock data"""
        try:
            return self.repository.find_many({})
        except Exception as e:
            logger.error("Error getting existing timeblocks: %s", str(e))
            return []
    
    def validate_timeblock_data(self, timeblock_data: Dict[str, Any]) -> bool:
        """Validate timeblock data structure"""
        required_fields = ["timeblock", "starttime", "endtime", "fulltime"]
        
        # Check if all required fields are present
        if not all(field in timeblock_data for field in required_fields):
            return False
        
        # Validate timeblock number range (1-96)
        if not (1 <= timeblock_data.get("timeblock", 0) <= 96):
            return False
        
        # Validate fulltime range (1-24)
        if not (1 <= timeblock_data.get("fulltime", 0) <= 24):
            return False
        
        # Validate time format (HH:MM)
        import re
        time_pattern = r'^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$'
        
        if not re.match(time_pattern, timeblock_data.get("starttime", "")):
            return False
        
        if not re.match(time_pattern, timeblock_data.get("endtime", "")):
            return False
        
        return True