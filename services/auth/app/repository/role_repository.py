"""
Role repository for managing roles in the database
"""

from typing import Optional, List
from datetime import datetime
from bson import ObjectId
import logging
from .base_repository import BaseRepository
from ..models.roles import RoleInDB
from ..core.config import settings

logger = logging.getLogger(__name__)


class RoleRepository(BaseRepository):
    """Role repository with role-specific database operations"""

    def __init__(self):
        super().__init__("roles", settings.AUTH_DB_NAME)

    async def create_role(self, name: str, description: str = "", permissions: List[str] = None) -> RoleInDB:
        """Create a new role"""
        try:
            # Check if role already exists
            if await self.get_role_by_name(name):
                raise ValueError("Role already exists")

            if permissions is None:
                permissions = []

            # Create role document
            role_data = {
                "name": name,
                "description": description,
                "permissions": permissions,
                "is_active": True,
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }

            role_id = await self.create(role_data)
            role_data["_id"] = str(role_id)

            return RoleInDB(**role_data)
        except Exception as e:
            logger.error(f"Error creating role: {e}")
            raise

    async def get_role_by_id(self, role_id: str) -> Optional[RoleInDB]:
        """Get role by ID"""
        try:
            role_doc = await self.find_one({
                "_id": ObjectId(role_id),
                "is_active": True
            })
            if role_doc:
                role_doc["_id"] = str(role_doc["_id"])
                return RoleInDB(**role_doc)
            return None
        except Exception as e:
            logger.error(f"Error getting role by ID {role_id}: {e}")
            return None

    async def get_role_by_name(self, name: str) -> Optional[RoleInDB]:
        """Get role by name"""
        try:
            role_doc = await self.find_one({
                "name": name,
                "is_active": True
            })
            if role_doc:
                role_doc["_id"] = str(role_doc["_id"])
                return RoleInDB(**role_doc)
            return None
        except Exception as e:
            logger.error(f"Error getting role by name {name}: {e}")
            return None

    async def get_all_active_roles(self) -> List[RoleInDB]:
        """Get all active roles"""
        try:
            role_docs = await self.find_many({"is_active": True})
            for doc in role_docs:
                doc["_id"] = str(doc["_id"])
            return [RoleInDB(**doc) for doc in role_docs]
        except Exception as e:
            logger.error(f"Error getting all active roles: {e}")
            return []

    async def update_role(self, role_id: str, update_data: dict) -> bool:
        """Update role information"""
        try:
            # Handle permissions - ensure they're stored as a list
            if "permissions" in update_data:
                if not isinstance(update_data["permissions"], list):
                    update_data["permissions"] = []

            update_data["updated_at"] = datetime.now()
            return await self.update(role_id, update_data)
        except Exception as e:
            logger.error(f"Error updating role {role_id}: {e}")
            return False

    async def delete_role(self, role_id: str) -> bool:
        """Soft delete role"""
        try:
            return await self.update(role_id, {
                "is_active": False,
                "updated_at": datetime.now().isoformat()
            })
        except Exception as e:
            logger.error(f"Error deleting role {role_id}: {e}")
            return False

    async def assign_permissions_to_role(self, role_id: str, permissions: List[str]) -> bool:
        """Assign permissions to a role"""
        try:
            role = await self.get_role_by_id(role_id)
            if not role:
                logger.error(f"Role not found: {role_id}")
                return False

            # Merge new permissions with existing ones (avoid duplicates)
            existing_permissions = set(role.permissions)
            new_permissions = existing_permissions.union(set(permissions))

            return await self.update_role(role_id, {"permissions": list(new_permissions)})
        except Exception as e:
            logger.error(f"Error assigning permissions to role {role_id}: {e}")
            return False

    async def remove_permissions_from_role(self, role_id: str, permissions: List[str]) -> bool:
        """Remove permissions from a role"""
        try:
            role = await self.get_role_by_id(role_id)
            if not role:
                logger.error(f"Role not found: {role_id}")
                return False

            # Remove specified permissions
            existing_permissions = set(role.permissions)
            remaining_permissions = existing_permissions.difference(set(permissions))

            return await self.update_role(role_id, {"permissions": list(remaining_permissions)})
        except Exception as e:
            logger.error(f"Error removing permissions from role {role_id}: {e}")
            return False

    async def set_role_permissions(self, role_id: str, permissions: List[str]) -> bool:
        """Set exact permissions for a role (replaces all existing permissions)"""
        try:
            return await self.update_role(role_id, {"permissions": permissions})
        except Exception as e:
            logger.error(f"Error setting permissions for role {role_id}: {e}")
            return False

    # Note: Page permissions are now handled via page.role_ids instead of role.page_permissions
