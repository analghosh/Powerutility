"""
Timeblock Repository for Ingestion Service
Handles database operations for timeblock master collection
"""

import logging
from typing import Dict, Any, List, Optional, Set
from datetime import datetime

from libs.database.base_repository import BaseRepository
from services.ingestion.app.core.config import settings


logger = logging.getLogger(__name__)


class TimeblockRepository(BaseRepository):
    """Repository for timeblock master collection in ingestion service"""

    def __init__(self, mongodb_uri: str, database_name: str):
        """
        Initialize the timeblock repository

        Args:
            mongodb_uri: MongoDB connection URI
            database_name: Database name
        """
        self.collection_name = settings.MONGODB_TIMEBLOCK_MASTER_COLLECTION
        super().__init__(self.collection_name, mongodb_uri, database_name)

    async def initialize(self):
        """Initialize the repository — call once at startup from async context."""
        await self._ensure_collection()

    async def _ensure_collection(self):
        """Ensure the timeblock collection exists with proper indexes."""
        try:
            if self.collection_name not in await self.db.list_collection_names():
                await self.db.create_collection(self.collection_name)
                logger.info("Created timeblock master collection")

            collection = self.db[self.collection_name]
            await collection.create_index([("timeblock", 1)])
            await collection.create_index([("fulltime", 1)])

            logger.info("Created indexes for timeblock master collection")

        except Exception as e:
            logger.error("Error ensuring timeblock collection: %s", e)
            raise

    async def get_all_timeblocks(self) -> List[Dict[str, Any]]:
        """
        Get all timeblock records from database

        Returns:
            List[Dict[str, Any]]: List of timeblock records
        """
        try:
            timeblocks = await self.collection.find({}).sort("timeblock", 1).to_list(None)

            # Convert ObjectId to string for JSON serialization
            for timeblock in timeblocks:
                timeblock["_id"] = str(timeblock["_id"])

            logger.info("Retrieved %d timeblock records", len(timeblocks))
            return timeblocks

        except Exception as e:
            logger.error("Error getting all timeblocks: %s", e)
            raise

    async def get_timeblock_by_number(self, timeblock: int) -> Optional[Dict[str, Any]]:
        """
        Get timeblock record by timeblock number

        Args:
            timeblock: Timeblock number

        Returns:
            Optional[Dict[str, Any]]: Timeblock record or None
        """
        try:
            timeblock_doc = await self.collection.find_one({"timeblock": timeblock})

            if timeblock_doc:
                timeblock_doc["_id"] = str(timeblock_doc["_id"])
                return timeblock_doc

            return None

        except Exception as e:
            logger.error("Error getting timeblock by number: %s", e)
            raise

    async def get_valid_timeblock_numbers(self) -> Set[int]:
        """
        Get set of valid timeblock numbers

        Returns:
            Set[int]: Set of valid timeblock numbers
        """
        try:
            timeblocks = await self.collection.find({}, {"timeblock": 1, "_id": 0}).to_list(None)
            valid_numbers = {doc["timeblock"] for doc in timeblocks if "timeblock" in doc}

            logger.info("Retrieved %d valid timeblock numbers", len(valid_numbers))
            return valid_numbers

        except Exception as e:
            logger.error("Error getting valid timeblock numbers: %s", e)
            raise

    async def get_timeblock_range(self) -> tuple:
        """
        Get the valid timeblock range

        Returns:
            tuple[int, int]: (min_timeblock, max_timeblock)
        """
        try:
            # Get min and max timeblock numbers
            min_doc = await self.collection.find_one({}, sort=[("timeblock", 1)])
            max_doc = await self.collection.find_one({}, sort=[("timeblock", -1)])

            if min_doc and max_doc:
                min_timeblock = min_doc.get("timeblock", 1)
                max_timeblock = max_doc.get("timeblock", 96)
                return (min_timeblock, max_timeblock)

            # Fallback range if no data
            return (1, 96)

        except Exception as e:
            logger.error("Error getting timeblock range: %s", e)
            return (1, 96)  # Fallback range

    async def is_valid_timeblock(self, timeblock: int) -> bool:
        """
        Check if timeblock number exists in database

        Args:
            timeblock: Timeblock number to check

        Returns:
            bool: True if valid, False otherwise
        """
        try:
            count = await self.collection.count_documents({"timeblock": timeblock})
            return count > 0

        except Exception as e:
            logger.error("Error checking timeblock validity: %s", e)
            return False

    async def get_timeblocks_by_fulltime(self, fulltime: int) -> List[Dict[str, Any]]:
        """
        Get timeblocks by fulltime number

        Args:
            fulltime: Fulltime number (1-24)

        Returns:
            List[Dict[str, Any]]: List of timeblock records
        """
        try:
            timeblocks = await self.collection.find({"fulltime": fulltime}).sort("timeblock", 1).to_list(None)

            # Convert ObjectId to string for JSON serialization
            for timeblock in timeblocks:
                timeblock["_id"] = str(timeblock["_id"])

            logger.info("Retrieved %d timeblocks for fulltime %d", len(timeblocks), fulltime)
            return timeblocks

        except Exception as e:
            logger.error("Error getting timeblocks by fulltime: %s", e)
            raise

    async def get_timeblock_info(self, timeblock: int) -> Optional[Dict[str, Any]]:
        """
        Get timeblock information

        Args:
            timeblock: Timeblock number

        Returns:
            Optional[Dict[str, Any]]: Timeblock info or None
        """
        try:
            timeblock_doc = await self.get_timeblock_by_number(timeblock)

            if timeblock_doc:
                return {
                    "timeblock": timeblock_doc.get("timeblock"),
                    "starttime": timeblock_doc.get("starttime"),
                    "endtime": timeblock_doc.get("endtime"),
                    "fulltime": timeblock_doc.get("fulltime")
                }

            return None

        except Exception as e:
            logger.error("Error getting timeblock info: %s", e)
            raise

    async def validate_timeblock_list(self, timeblocks: List[int]) -> Dict[str, Any]:
        """
        Validate a list of timeblock numbers

        Args:
            timeblocks: List of timeblock numbers to validate

        Returns:
            Dict[str, Any]: Validation result
        """
        try:
            valid_timeblocks = []
            invalid_timeblocks = []

            for timeblock in timeblocks:
                if await self.is_valid_timeblock(timeblock):
                    valid_timeblocks.append(timeblock)
                else:
                    invalid_timeblocks.append(timeblock)

            min_timeblock, max_timeblock = await self.get_timeblock_range()

            return {
                "is_valid": len(invalid_timeblocks) == 0,
                "valid_timeblocks": valid_timeblocks,
                "invalid_timeblocks": invalid_timeblocks,
                "valid_range": (min_timeblock, max_timeblock),
                "total_valid": len(valid_timeblocks),
                "total_invalid": len(invalid_timeblocks)
            }

        except Exception as e:
            logger.error("Error validating timeblock list: %s", e)
            raise
