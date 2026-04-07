"""
Menu repository for managing menu items in the database
"""

from typing import Optional, List
from datetime import datetime
from bson import ObjectId
import logging
from .base_repository import BaseRepository
from ..models.menu import MenuInDB, MenuCreate, MenuUpdate, MenuWithChildren
from ..core.config import settings

logger = logging.getLogger(__name__)


class MenuRepository(BaseRepository):
    """Menu repository with menu-specific database operations"""
    
    def __init__(self):
        super().__init__("menus", settings.AUTH_DB_NAME)
    
    def create_menu(self, menu_data: MenuCreate) -> MenuInDB:
        """Create a new menu item"""
        try:
            # Check if menu already exists
            if self.get_menu_by_name(menu_data.name):
                raise ValueError(f"Menu '{menu_data.name}' already exists")
            
            # Create menu document
            menu_doc = {
                "name": menu_data.name,
                "icon": menu_data.icon,
                "parent_id": menu_data.parent_id,
                "path": menu_data.path,
                "order": menu_data.order,
                "is_active": True,
                "is_deleted": False,
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
            
            menu_id = self.create(menu_doc)
            menu_doc["_id"] = str(menu_id)
            
            return MenuInDB(**menu_doc)
        except Exception as e:
            logger.error(f"Error creating menu: {e}")
            raise
    
    def get_menu_by_id(self, menu_id: str) -> Optional[MenuInDB]:
        """Get menu by ID"""
        try:
            menu_doc = self.find_one({
                "_id": ObjectId(menu_id),
                "is_deleted": {"$ne": True}
            })
            return MenuInDB(**menu_doc) if menu_doc else None
        except Exception as e:
            logger.error(f"Error getting menu by ID {menu_id}: {e}")
            return None
    
    def get_menu_by_name(self, menu_name: str) -> Optional[MenuInDB]:
        """Get menu by name"""
        try:
            menu_doc = self.find_one({
                "name": menu_name,
                "is_deleted": {"$ne": True}
            })
            return MenuInDB(**menu_doc) if menu_doc else None
        except Exception as e:
            logger.error(f"Error getting menu by name {menu_name}: {e}")
            return None
    
    def get_menus_by_parent_id(self, parent_id: Optional[str] = None) -> List[MenuInDB]:
        """Get all menu items by parent ID (None for root level)"""
        try:
            query = {"is_deleted": {"$ne": True}}
            if parent_id is None:
                query["parent_id"] = None
            else:
                query["parent_id"] = parent_id
            
            menu_docs = self.find_many(query, sort=[("order", 1)])
            return [MenuInDB(**doc) for doc in menu_docs]
        except Exception as e:
            logger.error(f"Error getting menus by parent_id {parent_id}: {e}")
            return []
    
    def get_all_active_menus(self) -> List[MenuInDB]:
        """Get all active menu items"""
        try:
            menu_docs = self.find_many({
                "is_active": True,
                "is_deleted": {"$ne": True}
            }, sort=[("order", 1)])
            return [MenuInDB(**doc) for doc in menu_docs]
        except Exception as e:
            logger.error(f"Error getting all active menus: {e}")
            return []
    
    def get_menu_tree(self) -> List[MenuWithChildren]:
        """Get complete menu tree with nested children"""
        try:
            all_menus = self.get_all_active_menus()
            return self.build_menu_tree(all_menus)
        except Exception as e:
            logger.error(f"Error getting menu tree: {e}")
            return []
    
    def build_menu_tree(self, menus: List[MenuInDB]) -> List[MenuWithChildren]:
        """Build menu tree from a list of menus"""
        try:
            if not menus:
                logger.warning("No menus provided for tree building")
                return []
            
            # Create menu dictionary with validation
            menu_dict = {}
            valid_menu_ids = set()
            
            for menu in menus:
                menu_dict[menu.id] = MenuWithChildren(
                    id=menu.id,
                    name=menu.name,
                    icon=menu.icon,
                    parent_id=menu.parent_id,
                    path=menu.path,
                    order=menu.order,
                    is_active=menu.is_active,
                    children=[]
                )
                valid_menu_ids.add(menu.id)
            
            # Build tree structure with validation
            root_menus = []
            orphaned_menus = []
            
            for menu in menus:
                menu_with_children = menu_dict[menu.id]
                
                if menu.parent_id is None:
                    root_menus.append(menu_with_children)
                else:
                    # Validate parent_id exists
                    if menu.parent_id in valid_menu_ids:
                        parent = menu_dict.get(menu.parent_id)
                        if parent:
                            parent.children.append(menu_with_children)
                        else:
                            logger.warning(f"Menu '{menu.name}' has invalid parent_id: {menu.parent_id}")
                            orphaned_menus.append(menu_with_children)
                    else:
                        logger.warning(f"Menu '{menu.name}' has non-existent parent_id: {menu.parent_id}")
                        orphaned_menus.append(menu_with_children)
            
            # Add orphaned menus to root level
            root_menus.extend(orphaned_menus)
            
            # Sort by order
            root_menus.sort(key=lambda x: x.order)
            for menu in menu_dict.values():
                menu.children.sort(key=lambda x: x.order)
            
            logger.info(f"Built menu tree with {len(root_menus)} root menus and {len(menus)} total menus")
            return root_menus
            
        except Exception as e:
            logger.error(f"Error building menu tree: {e}")
            return []
    
    def update_menu(self, menu_id: str, update_data: MenuUpdate) -> bool:
        """Update menu information"""
        try:
            update_dict = update_data.dict(exclude_unset=True)
            update_dict["updated_at"] = datetime.now()
            
            return self.update(menu_id, update_dict)
        except Exception as e:
            logger.error(f"Error updating menu {menu_id}: {e}")
            return False
    
    def delete_menu(self, menu_id: str) -> bool:
        """Soft delete menu"""
        try:
            return self.update(menu_id, {
                "is_deleted": True,
                "updated_at": datetime.now()
            })
        except Exception as e:
            logger.error(f"Error deleting menu {menu_id}: {e}")
            return False
    
    def get_menu_by_path(self, path: str) -> Optional[MenuInDB]:
        """Get menu by path"""
        try:
            menu_doc = self.find_one({
                "path": path,
                "is_deleted": {"$ne": True}
            })
            return MenuInDB(**menu_doc) if menu_doc else None
        except Exception as e:
            logger.error(f"Error getting menu by path {path}: {e}")
            return None
    
    def get_menus_by_ids(self, menu_ids: List[str]) -> List[MenuInDB]:
        """Get multiple menus by IDs"""
        try:
            object_ids = [ObjectId(menu_id) for menu_id in menu_ids]
            menu_docs = self.find_many({
                "_id": {"$in": object_ids},
                "is_deleted": {"$ne": True}
            })
            return [MenuInDB(**doc) for doc in menu_docs]
        except Exception as e:
            logger.error(f"Error getting menus by IDs: {e}")
            return []
