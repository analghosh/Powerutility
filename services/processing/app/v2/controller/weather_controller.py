# In /controller/weather_controller.py

import logging
from typing import Dict, Any

from ..services.weather_services import weather_service

logger = logging.getLogger(__name__)

class WeatherController:
    def __init__(self, service=weather_service):
        self.service = service

    async def get_forecast(
        self,
        forecast_days: int,
        timezone: str,
        weather_type: str,
        city: str,
        date:str| None
    ) -> Dict[str, Any]:
        try:
            logger.info(f"Controller received forecast request for city={city}")
            return await self.service.get_weather_forecast(
                forecast_days=forecast_days,
                tz=timezone,
                weather_type=weather_type,
                city=city,
                date=date
            )
        except Exception:
            logger.exception("get_forecast in controller failed")
            raise

weather_controller = WeatherController()