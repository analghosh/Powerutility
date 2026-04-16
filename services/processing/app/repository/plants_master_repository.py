# repositories/plants_master_repository.py
from typing import List, Dict, Any, Optional
import logging

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class PlantsMasterRepository(BaseRepository):
    """
    Repository for the 'plant_master' collection.
    Handles plant master data operations.
    """

    def __init__(self, collection_name: str = "plant_master"):
        super().__init__(collection_name=collection_name)
        logger.info(f"PlantsMasterRepository initialized with collection: {collection_name}")

    # ---- Generic passthrough used by the service layer ----
    async def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run an aggregation pipeline and return the list of documents.
        """
        logger.debug("PlantsMasterRepository.aggregate | pipeline=%s", pipeline)
        cursor = self.collection.aggregate(pipeline)
        try:
            return await cursor.to_list(10000)
        finally:
            cursor.close()

    async def find_by_plant_id(self, plant_id: str) -> List[Dict[str, Any]]:
        """
        Find plant data by plant_id.
        """
        logger.debug("PlantsMasterRepository.find_by_plant_id | plant_id=%s", plant_id)
        cursor = self.collection.find({"plant_id": plant_id})
        try:
            return await cursor.to_list(10000)
        finally:
            cursor.close()

    async def get_all_plants(self) -> List[Dict[str, Any]]:
        """
        Get all plants from the collection.
        """
        logger.debug("PlantsMasterRepository.get_all_plants")
        cursor = self.collection.find({})
        try:
            return await cursor.to_list(10000)
        finally:
            cursor.close()

    async def get_Data(self):
        """
        Get all plant data.
        Returns list of plant documents.
        """
        try:    
            pipeline = [
                {
                   "$project": {
                        "_id": 0,
                    }
                },
            ]
            cursor = self.collection.aggregate(pipeline)
            try:
                result = await cursor.to_list(10000)
            finally:
                cursor.close()

            logger.info("Get plant data successful")
            return result
        except Exception as e:
            logger.error("Failed to get plant data: %s", str(e))
            return str(e) # type: ignore
