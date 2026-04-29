from .base_repository import BaseRepository
from ..core.config import settings

class PermissionRepository(BaseRepository):
    def __init__(self):
        super().__init__(settings.MONGODB_PERMISSION_COLLECTION, settings.AUTH_DB_NAME)

    async def get_permission(self, permission_key: str) -> dict:
        """Return a single permission document"""
        return await self.find_one({"key": permission_key, "is_deleted": {"$ne": True}})

    async def list_permissions(self, module: str = None) -> list:
        query = {"is_deleted": {"$ne": True}}
        if module:
            query["module"] = module
        return await self.find_many(query)
