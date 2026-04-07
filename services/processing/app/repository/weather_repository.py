# In /repository/weather_repository.py

from typing import List, Dict, Any
from .base_repository import BaseRepository
import logging
from ..core.config import settings

logger = logging.getLogger(__name__)

class WeatherRepository(BaseRepository):
    def __init__(self, collection_name: str = settings.MONGODB_WEATHER_SERVE ):
        super().__init__(collection_name=collection_name)
        logger.info(f"WeatherRepository initialized with collection: {collection_name}")

    def get_hourly_data_for_range(self, start_utc: int, end_utc: int, type: str) -> List[Dict[str, Any]]:
        """
        Fetches a raw list of hourly weather documents for a given UTC timestamp range.

        Args:
            start_utc: The start of the time range (Unix timestamp).
            end_utc: The end of the time range (Unix timestamp).

        Returns:
            A list of weather data documents, sorted chronologically.
        """
        pipeline = [
            # 1. Filter documents within the requested time range
            {
                "$match": {
                    "valid_time_utc": {
                        "$gte": start_utc,
                        "$lt": end_utc
                    }
                }
            },
            # 2. Exclude the fields you don't want in the final response
            {
                "$project": {
                    "_id": 0,
                    "inserted_at": 0,
                    "created_at": 0,
                    "updated_at": 0,
                    "updated_flag": 0, # Example of another field to exclude
                    "execution_id": 0,
                    "type": 0
                }
            },
            # 3. Sort by time to ensure the list is in chronological order
            {
                "$sort": {
                    "valid_time_utc": 1
                }
            }
        ]
        
        logger.info(f"Executing weather fetch pipeline for UTC range {start_utc}-{end_utc}")
        return self.aggregate(pipeline)

weather_repository = WeatherRepository()