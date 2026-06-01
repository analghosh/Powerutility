"""
Master Plants Repository for Scheduler Service
Handles plant_master collection access
"""
from typing import List, Dict, Any, Optional
import logging

from services.processing.app.v2.repository.base_repository import GenericRepository
from ..core.config import settings

logger = logging.getLogger(__name__)


class MasterPlantsRepository(GenericRepository):
    """
    Repository for plant_master collection.
    Provides access to plant master data including scheduling requirements.
    """

    def __init__(self, collection_name: str = "plant_master"):
        super().__init__()
        self.collection_name = collection_name
        logger.info(f"MasterPlantsRepository initialized with collection: {self.collection_name}")

    async def create_indexes(self):
        """Create indexes for plant_master collection"""
        try:
            collection = self.db[self.collection_name]

            # Drop existing indexes first to ensure cleaner state if schema changes
            to_drop = [
                "plant_id_1",
                "plant_id_1_approval_no_1",
                "unique_plant_id_no_approval",
                "unique_plant_id_with_approval",
                "idx_plant_id",
            ]
            for index_name in to_drop:
                try:
                    await collection.drop_index(index_name)
                except Exception:
                    pass

            # Unique index on plant_id
            await collection.create_index(
                [("plant_id", 1)],
                unique=True,
                background=True,
                name="idx_plant_id"
            )

            # Create Compound Unique Index on (plant_id, approval_no)
            await collection.create_index(
                [("plant_id", 1), ("approval_no", 1)],
                unique=True,
                background=True,
                name="unique_plant_id_approval"
            )

            logger.info("Indexes created/verified for plant_master collection")
        except Exception as e:
            logger.error(f"Failed to create indexes for plant_master: {str(e)}")

    async def get_all_plants(self) -> List[Dict[str, Any]]:
        """
        Get all plants from plant_master collection asynchronously.
        """
        try:
            result = await self.get_all(self.collection_name, {})
            logger.info(f"Retrieved {len(result)} plant master records")
            return result
        except Exception as e:
            logger.error(f"Failed to retrieve plant master data: {str(e)}")
            return []

    async def get_plant_by_id(self, plant_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a single plant by plant_id.
        """
        try:
            result = await self.find_one(self.collection_name, {"plant_id": plant_id})
            return result
        except Exception as e:
            logger.error(f"Failed to get plant by ID {plant_id}: {str(e)}")
            return None

    async def get_plants_by_category(self, scheduling_req_category: str) -> List[Dict[str, Any]]:
        """
        Get plants filtered by scheduling requirement category.
        """
        try:
            result = await self.get_all(
                self.collection_name,
                {"scheduling_req_category": scheduling_req_category},
            )
            return result
        except Exception as e:
            logger.error(f"Failed to get plants by category {scheduling_req_category}: {str(e)}")
            return []
