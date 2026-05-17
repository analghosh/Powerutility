"""
Scheduler Parameter Repository (IDF forecast switch and shared parameters).
"""

from libs.repository.parameter_repository import (
    ParameterRepository as LibParameterRepository,
)

from ..core.config import settings


class ParameterRepository(LibParameterRepository):
    """parameters collection access using scheduler service database settings."""

    def __init__(self):
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
        )


parameter_repository = ParameterRepository()
