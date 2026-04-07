# In /controller/weather_controller.py

import logging
from typing import Dict, Any

from ..services.weather_services import weather_service

logger = logging.getLogger(__name__)

class WeatherController:
    def __init__(self, service=weather_service):
        self.service = service

    def get_forecast(
        self,
        latitude: float,
        longitude: float,
        forecast_days: int,
        timezone: str,
        type: str
    ) -> Dict[str, Any]:
        try:
            logger.info(f"Controller received forecast request for lat={latitude}, lon={longitude}")
            return self.service.get_weather_forecast(
                latitude=latitude,
                longitude=longitude,
                forecast_days=forecast_days,
                tz=timezone,
                type=type
            )
        except Exception:
            logger.exception("get_forecast in controller failed")
            raise

weather_controller = WeatherController()