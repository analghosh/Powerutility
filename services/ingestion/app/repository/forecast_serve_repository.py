"""
Ingestion Forecast Serve Repository
"""

from libs.repository.forecast_serve_repository import (
    ForecastServeRepository as LibForecastServeRepository,
)

from ..core.config import settings


class ForecastServeRepository(LibForecastServeRepository):
    """forecast_serve access using ingestion service database settings."""

    def __init__(self):
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
        )


forecast_serve_repository = ForecastServeRepository()
