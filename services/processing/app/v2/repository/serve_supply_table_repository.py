from .base_repository import GenericRepository
from ...core.config import settings


class SupplyTableRepository:
    """Generic serve-table repo bound to a specific collection."""

    def __init__(self, collection_name: str):
        self._collection_name = collection_name
        self._repo = GenericRepository()

    async def aggregate(self, pipeline, **kwargs):
        return await self._repo.aggregate(self._collection_name, pipeline, **kwargs)


class serveSupplyTableRepository:
    """Supply serve table repository used by RTM/DAM bidding services."""

    def __init__(self):
        self.repository = GenericRepository()
        self.supply_serve = settings.MONGODB_SUPPLY_SERVE_COLLECTION
