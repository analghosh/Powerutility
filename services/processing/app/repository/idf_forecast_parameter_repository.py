"""
Processing IDF Forecast Parameter Repository
"""

from libs.repository.parameter_repository import (
    ParameterRepository as LibParameterRepository,
)

from ..core.config import settings


class IdfForecastParameterRepository(LibParameterRepository):
    """parameters collection for IDF forecast switch (processing service)."""

    def __init__(self):
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
        )


idf_forecast_parameter_repository = IdfForecastParameterRepository()
