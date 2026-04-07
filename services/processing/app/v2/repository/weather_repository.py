# In /repository/weather_repository.py

from typing import List, Dict, Any
from .base_repository import GenericRepository
import logging
from ...core.config import settings

logger = logging.getLogger(__name__)

class WeatherRepository():
    def __init__(self, collection_name: str = settings.MONGODB_WEATHER_SERVE):
        self.repo = GenericRepository()
        self.weather_collection = settings.MONGODB_WEATHER_SERVE
        logger.info(f"WeatherRepository initialized with collection: {collection_name}")

    async def get_hourly_data_for_range(self, start_local: str, end_local: str, weather_type: str, city:str) -> List[Dict[str, Any]]:
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
                    # Use valid_time_utc: service passes Unix seconds; valid_time_local is an ISO string in DB
                    "valid_time_local": {
                        "$gte": start_local,
                        "$lt": end_local,
                    },
                    "source": weather_type,
                    "city": city,
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
                    "source": 0
                }
            },
            # 3. Sort by time to ensure the list is in chronological order
            {
                "$sort": {
                    "valid_time_local": 1
                }
            }
        ]
        
        logger.info(f"Executing weather fetch pipeline for UTC range {start_local}-{end_local}")
        return await self.repo.aggregate_async(self.weather_collection,pipeline)

weather_repository = WeatherRepository()