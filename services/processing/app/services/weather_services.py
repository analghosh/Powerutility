# In /services/weather_service.py

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
from collections import defaultdict
from ..repository.weather_repository import weather_repository

logger = logging.getLogger(__name__)

class WeatherService:
    # A more comprehensive map for units. Default is "" if a key is not found.
    UNITS_MAP = {
        "time": "iso8601",
        "temperature": "°F",
        "temperature_feels_like": "°F",
        "temperature_dew_point": "°F",
        "temperature_wind_chill": "°F",
        "temperature_heat_index": "°F",
        "wind_speed": "km/h",
        "wind_gust": "km/h",
        "wind_direction": "°",
        "pressure_mean_sea_level": "hPa",
        "relative_humidity": "%",
        "precip_chance": "%",
        "qpf": "mm",
        "cloud_cover": "%",
        "uv_index": "",
        "visibility": "km"
    }
    
    def __init__(self, repository=weather_repository):
        self.repository = repository

    def get_weather_forecast(
        self,
        latitude: float,
        longitude: float,
        forecast_days: int,
        tz: str,
        type: str
    ) -> Dict[str, Any]:
        
        # 1. Calculate the date range. The logic ensures it fetches full days (00:00 to 23:59).
        start_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=forecast_days)
        start_utc_ts = int(start_date.timestamp())
        end_utc_ts = int(end_date.timestamp())

        # 2. Call repository to get the list of hourly documents
        if not type:
            type = "IBM" 
        hourly_docs = self.repository.get_hourly_data_for_range(start_utc_ts, end_utc_ts, type)

        # 3. Transform (pivot) the list of documents into a columnar dictionary
        hourly_data = defaultdict(list)
        all_keys = set()
        
        if not hourly_docs:
            logger.warning("No weather documents found for the specified range.")
        else:
            # First, find all possible keys from all documents
            for doc in hourly_docs:
                all_keys.update(doc.keys())
                
            # Now, iterate and build the columnar lists, ensuring no missing values
            for doc in hourly_docs:
                for key in all_keys:
                    hourly_data[key].append(doc.get(key)) # Use .get() to handle missing keys in any doc

        # 4. Dynamically create the hourly_units dictionary
        hourly_units = {key: self.UNITS_MAP.get(key, "") for key in all_keys}
        # Ensure 'time' is always present in units, even if no data
        if "time" not in hourly_units:
            hourly_units["time"] = "iso8601"
            hourly_data["time"] = [] # Ensure time array exists
        
        # Rename valid_time_local to 'time' for the final response, which is more standard
        if 'valid_time_local' in hourly_data:
            hourly_data['time'] = hourly_data.pop('valid_time_local')


        # 5. Assemble the final response
        response = {
            "latitude": latitude,
            "longitude": longitude,
            "generationtime_ms": 0.5,
            "utc_offset_seconds": 19800,
            "timezone": "Asia/Kolkata",
            "timezone_abbreviation": "GMT+5:30",
            "elevation": 7.0,
            "hourly_units": hourly_units,
            "hourly": dict(hourly_data) # Convert defaultdict to regular dict
        }
        
        return response

weather_service = WeatherService()