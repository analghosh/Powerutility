"""
Menu-Role repository for managing menu-role relationships in the database
"""

from typing import Optional, List
from datetime import datetime
from bson import ObjectId
import logging
from .base_repository import BaseRepository
from ..models.menu_role import MenuRoleInDB, MenuRoleCreate, MenuRoleUpdate
from ..core.config import settings

logger = logging.getLogger(__name__)


class MenuRoleRepository(BaseRepository):
    """Menu-Role repository with relationship-specific database operations"""
    
    def __init__(self):
        super().__init__("menu_roles", settings.AUTH_DB_NAME)
    
    async def create_menu_role(self, menu_role_data: MenuRoleCreate) -> MenuRoleInDB:
        """Create a new menu-role relationship"""
        try:
            # Check if relationship already exists (await the async method)
            existing = await self.find_one({
                "menu_id": menu_role_data.menu_id,
                "role_id": menu_role_data.role_id,
                "is_active": True
            })
            
            if existing:
                raise ValueError("Menu-role relationship already exists")
            
            # Create menu-role document
            menu_role_doc = {
                "menu_id": menu_role_data.menu_id,
                "role_id": menu_role_data.role_id,
                "permissions": menu_role_data.permissions,
                "is_active": True,
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
            
            menu_role_id = await self.create(menu_role_doc)
            menu_role_doc["_id"] = str(menu_role_id)
            
            return MenuRoleInDB(**menu_role_doc)
        except Exception as e:
            logger.error(f"Error creating menu-role relationship: {e}")
            raise
    
    async def get_menu_role_by_id(self, menu_role_id: str) -> Optional[MenuRoleInDB]:
        """Get menu-role relationship by ID"""
        try:
            menu_role_doc = await self.find_one({
                "_id": ObjectId(menu_role_id),
                "is_active": True
            })
            if menu_role_doc:
                menu_role_doc["_id"] = str(menu_role_doc["_id"])
                return MenuRoleInDB(**menu_role_doc)
            return None
        except Exception as e:
            logger.error(f"Error getting menu-role by ID {menu_role_id}: {e}")
            return None
    
    async def get_menu_roles_by_menu_id(self, menu_id: str) -> List[MenuRoleInDB]:
        """Get all role relationships for a specific menu"""
        try:
            menu_role_docs = await self.find_many({
                "menu_id": menu_id,
                "is_active": True
            })
            for doc in menu_role_docs:
                doc["_id"] = str(doc["_id"])
            return [MenuRoleInDB(**doc) for doc in menu_role_docs]
        except Exception as e:
            logger.error(f"Error getting menu roles for menu {menu_id}: {e}")
            return []
    
    async def get_menu_roles_by_role_id(self, role_id: str) -> List[MenuRoleInDB]:
        """Get all menu relationships for a specific role"""
        try:
            menu_role_docs = await self.find_many({
                "role_id": role_id,
                "is_active": True
            })
            for doc in menu_role_docs:
                doc["_id"] = str(doc["_id"])
            return [MenuRoleInDB(**doc) for doc in menu_role_docs]
        except Exception as e:
            logger.error(f"Error getting role menus for role {role_id}: {e}")
            return []
    
    async def get_menu_role(self, menu_id: str, role_id: str) -> Optional[MenuRoleInDB]:
        """Get specific menu-role relationship"""
        try:
            menu_role_doc = await self.find_one({
                "menu_id": menu_id,
                "role_id": role_id,
                "is_active": True
            })
            if menu_role_doc:
                menu_role_doc["_id"] = str(menu_role_doc["_id"])
                return MenuRoleInDB(**menu_role_doc)
            return None
        except Exception as e:
            logger.error(f"Error getting menu-role relationship: {e}")
            return None
    
    async def update_menu_role(self, menu_role_id: str, update_data: MenuRoleUpdate) -> bool:
        """Update menu-role relationship"""
        try:
            update_dict = update_data.dict(exclude_unset=True)
            update_dict["updated_at"] = datetime.now()

            return await self.update(menu_role_id, update_dict)
        except Exception as e:
            logger.error(f"Error updating menu-role {menu_role_id}: {e}")
            return False
    
    async def delete_menu_role(self, menu_role_id: str) -> bool:
        """Soft delete menu-role relationship"""
        try:
            return await self.update(menu_role_id, {
                "is_active": False,
                "updated_at": datetime.now()
            })
        except Exception as e:
            logger.error(f"Error deleting menu-role {menu_role_id}: {e}")
            return False
    
    async def delete_menu_role_by_ids(self, menu_id: str, role_id: str) -> bool:
        """Delete menu-role relationship by menu and role IDs"""
        try:
            menu_role = await self.get_menu_role(menu_id, role_id)
            if menu_role:
                return await self.delete_menu_role(menu_role.id)
            return False
        except Exception as e:
            logger.error(f"Error deleting menu-role by IDs: {e}")
            return False
    
    async def get_roles_for_menu(self, menu_id: str) -> List[str]:
        """Get all role IDs that have access to a specific menu"""
        try:
            menu_roles = await self.get_menu_roles_by_menu_id(menu_id)
            return [mr.role_id for mr in menu_roles]
        except Exception as e:
            logger.error(f"Error getting roles for menu {menu_id}: {e}")
            return []
    
    async def get_menus_for_role(self, role_id: str) -> List[str]:
        """Get all menu IDs that a specific role has access to"""
        try:
            role_menus = await self.get_menu_roles_by_role_id(role_id)
            return [rm.menu_id for rm in role_menus]
        except Exception as e:
            logger.error(f"Error getting menus for role {role_id}: {e}")
            return []
    
    async def grant_role_menu_access(self, role_id: str, menu_id: str, permissions: List[str] = None) -> bool:
        """Grant a role access to a menu and sync role permissions"""
        try:
            if permissions is None:
                permissions = ["read"]

            # Atomic upsert — avoids race condition and duplicate key errors
            now = datetime.now()
            await self.collection.update_one(
                {"role_id": role_id, "menu_id": menu_id},
                {
                    "$set": {
                        "permissions": permissions,
                        "is_active": True,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "created_at": now,
                    },
                },
                upsert=True,
            )

            await self._sync_role_permissions(role_id)
            return True
        except Exception as e:
            logger.error(f"Error granting role menu access: {e}")
            return False
    
    async def revoke_role_menu_access(self, role_id: str, menu_id: str) -> bool:
        """Revoke a role's access to a menu and sync role permissions"""
        try:
            success = await self.delete_menu_role_by_ids(menu_id, role_id)
            
            # Sync role permissions after menu removal
            if success:
                await self._sync_role_permissions(role_id)
            
            return success
        except Exception as e:
            logger.error(f"Error revoking role menu access: {e}")
            return False
    
    async def get_user_accessible_menus(self, user_roles: List[str]) -> List[str]:
        """Get all menu IDs accessible to a user based on their roles"""
        try:
            accessible_menus = set()
            for role in user_roles:
                role_menus = await self.get_menus_for_role(role)
                accessible_menus.update(role_menus)
            return list(accessible_menus)
        except Exception as e:
            logger.error(f"Error getting user accessible menus: {e}")
            return []
    
    async def get_menu_permissions_for_role(self, role_id: str, menu_id: str) -> List[str]:
        """Get permissions for a specific role on a specific menu"""
        try:
            menu_role = await self.get_menu_role(menu_id, role_id)
            return menu_role.permissions if menu_role else []
        except Exception as e:
            logger.error(f"Error getting menu permissions for role: {e}")
            return []
    
    async def _sync_role_permissions(self, role_id: str) -> bool:
        """
        Sync role permissions based on assigned menu permissions.
        Uses consistent format compatible with centralized RBAC system.
        """
        try:
            from .role_repository import RoleRepository
            from .menu_repository import MenuRepository
            
            role_repo = RoleRepository()
            menu_repo = MenuRepository()
            
            # Get all menu assignments for this role
            menu_roles = await self.get_menu_roles_by_role_id(role_id)
            
            # Build consolidated permission list using centralized RBAC format
            all_permissions = []
            for menu_role in menu_roles:
                menu = await menu_repo.get_menu_by_id(menu_role.menu_id)
                if menu:
                    # Use consistent resource naming that matches route mapping
                    resource_name = self._get_resource_name_from_menu(menu)
                    
                    # Create permission strings in standard format "resource:action"
                    for permission in menu_role.permissions:
                        permission_string = f"{resource_name}:{permission}"
                        all_permissions.append(permission_string)
            
            # Remove duplicates while preserving order
            unique_permissions = list(dict.fromkeys(all_permissions))
            
            # Update role permissions
            return await role_repo.set_role_permissions(role_id, unique_permissions)
            
        except Exception as e:
            logger.error(f"Error syncing role permissions for role {role_id}: {e}")
            return False
    
    def _get_resource_name_from_menu(self, menu) -> str:
        """
        Get consistent resource name from menu that matches centralized RBAC route mapping.
        This ensures menu permissions align with automatic route-to-permission mapping.
        """
        # Special mappings for consistency with route mapping
        menu_to_resource_mapping = {
            "Users": "users",
            "Roles": "roles", 
            "Administration": "admin",
            "Permissions": "admin",
            "Audit Logs": "admin",
            "Setup": "admin",
            "Dashboard": "dashboard",
            "Overview": "overview",
            "Management Snapshot": "management_snapshot",
            "DSM Overview": "dsm_overview",
            "Market Integrator": "market_integrator",
            "Forecast": "forecast",
            "Forecast Analysis": "forecast_analysis",
            "Demand Forecast": "demand_forecast",
            "Supply": "supply",
            "Non-Renewable Supply": "non_renewable_supply",
            "Renewable Supply": "renewable_supply",
            "URS": "urs",
            "Day Ahead Marketing": "day_ahead_marketing",
            "DAM Scheduling": "dam_scheduling",
            "DAM Block Bid": "dam_block_bid",
            "DAM Single Bid": "dam_single_bid",
            "DAM Bid Status": "dam_bid_status",
            "Real Time Market": "real_time_market",
            "RTM Scheduling": "rtm_scheduling",
            "RTM Block Bid": "rtm_block_bid",
            "RTM Single Bid": "rtm_single_bid",
            "RTM Bid Status": "rtm_bid_status",
            "Weather": "weather",
            "Masters": "masters",
            "Plant Detail": "plant_detail",
            "DAM Limit Values": "dam_limit_values",
            "RTM Limit Values": "rtm_limit_values",
            "Reports": "reports",
            "Standard Reports": "standard_reports"
        }
        
        # Use mapping if available, otherwise convert menu name to resource format
        if menu.name in menu_to_resource_mapping:
            return menu_to_resource_mapping[menu.name]
        
        # Fallback: convert menu name to resource format
        return menu.name.lower().replace(' ', '_').replace('-', '_')
