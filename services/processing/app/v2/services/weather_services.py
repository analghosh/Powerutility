# In /services/weather_service.py

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
from zoneinfo import ZoneInfo
from collections import defaultdict
from ..repository.weather_repository import weather_repository

logger = logging.getLogger(__name__)

class WeatherService:
    UNITS_MAP = {
        "time": "iso8601",
        "temperature": "°F",
        "temperature_feels_like": "°C",
        "temperature_dew_point": "°C",
        "temperature_wind_chill": "°C",
        "temperature_heat_index": "°C",
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

    TEMP_FIELDS = [
        "temperature",
        "temperature_feels_like",
        "temperature_dew_point",
        "temperature_wind_chill",
        "temperature_heat_index"
    ]

    def __init__(self, repository=weather_repository):
        self.repository = repository

    # Fahrenheit → Celsius conversion
    def _f_to_c(self, value):
        try:
            if value is None:
                return None
            return round((float(value) - 32) * 5 / 9, 2)
        except Exception:
            return value

    async def get_weather_forecast(
        self,
        forecast_days: int,
        tz: str,
        weather_type: str,
        city: str,
        date: str |None
    ) -> Dict[str, Any]:

        
        try:
                tz_obj = ZoneInfo(tz) if tz and tz.lower() != "auto" else ZoneInfo("Asia/Kolkata")
        except Exception:
                logger.warning("Invalid timezone %r; fallback to IST", tz)
                tz_obj = ZoneInfo("Asia/Kolkata")
        now = datetime.now(tz_obj)      
        if date:
            try :
                selected_date = datetime.strptime(date, "%Y-%m-%d")                 
                start_local_dt  = selected_date.replace(hour=0, minute=0, second=0, microsecond=0,tzinfo=tz_obj)
                end_local_dt = start_local_dt + timedelta(days=1)      
            except Exception:
                 logger.error("Invalid date format, fallback to default logic")
                 start_local_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
                 end_local_dt = start_local_dt + timedelta(days=forecast_days)

        else : 
             start_local_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
             end_local_dt = start_local_dt + timedelta(days=forecast_days)

             
        start_local = start_local_dt.isoformat()
        end_local = end_local_dt.isoformat()


           
        if not weather_type:
            weather_type = "IBM"

        hourly_docs = await self.repository.get_hourly_data_for_range(
            start_local, end_local, weather_type,city
        )

        hourly_data = defaultdict(list)
        all_keys = set()

        if not hourly_docs:
            logger.warning("No weather documents found for the specified range.")
        else:

            # Collect all keys
            for doc in hourly_docs:
                all_keys.update(doc.keys())

            # Build column data
            for doc in hourly_docs:
                for key in all_keys:
                    hourly_data[key].append(doc.get(key))

        # ✅ Temperature conversion (after building hourly data)
        for field in self.TEMP_FIELDS:
            if field in hourly_data:
                converted = []
                for v in hourly_data[field]:
                    try:
                        if v is None:
                            converted.append(None)
                        else:
                            celsius = (float(v) - 32) * 5 / 9
                            converted.append(round(celsius, 2))
                    except Exception:
                        converted.append(v)

                hourly_data[field] = converted

        # Units mapping
        hourly_units = {key: self.UNITS_MAP.get(key, "") for key in all_keys}

        if "time" not in hourly_units:
            hourly_units["time"] = "iso8601"
            hourly_data["time"] = []

        # Rename field
        if "valid_time_local" in hourly_data:
            hourly_data["time"] = hourly_data.pop("valid_time_local")

        response = {
            "generationtime_ms": 0.5,
            "utc_offset_seconds": 19800,
            "timezone": "Asia/Kolkata",
            "timezone_abbreviation": "GMT+5:30",
            "elevation": 7.0,
            "hourly_units": hourly_units,
            "hourly": dict(hourly_data)
        }

        return response


weather_service = WeatherService()