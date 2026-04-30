"""
Page-Role repository for managing page-role relationships in the database
"""

from typing import Optional, List
from datetime import datetime
from bson import ObjectId
import logging
from .base_repository import BaseRepository
from ..models.page_role import PageRoleInDB, PageRoleCreate, PageRoleUpdate
from ..core.config import settings

logger = logging.getLogger(__name__)


class PageRoleRepository(BaseRepository):
    """Page-Role repository with relationship-specific database operations"""
    
    def __init__(self):
        super().__init__("page_roles", settings.AUTH_DB_NAME)
    
    def create_page_role(self, page_role_data: PageRoleCreate) -> PageRoleInDB:
        """Create a new page-role relationship"""
        try:
            # Check if relationship already exists
            existing = self.find_one({
                "page_id": page_role_data.page_id,
                "role_id": page_role_data.role_id,
                "is_active": True
            })
            
            if existing:
                raise ValueError("Page-role relationship already exists")
            
            # Create page-role document
            page_role_doc = {
                "page_id": page_role_data.page_id,
                "role_id": page_role_data.role_id,
                "permissions": page_role_data.permissions,
                "is_active": True,
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
            
            page_role_id = self.create(page_role_doc)
            page_role_doc["_id"] = str(page_role_id)
            
            return PageRoleInDB(**page_role_doc)
        except Exception as e:
            logger.error(f"Error creating page-role relationship: {e}")
            raise
    
    def get_page_role_by_id(self, page_role_id: str) -> Optional[PageRoleInDB]:
        """Get page-role relationship by ID"""
        try:
            page_role_doc = self.find_one({
                "_id": ObjectId(page_role_id),
                "is_active": True
            })
            return PageRoleInDB(**page_role_doc) if page_role_doc else None
        except Exception as e:
            logger.error(f"Error getting page-role by ID {page_role_id}: {e}")
            return None
    
    def get_page_roles_by_page_id(self, page_id: str) -> List[PageRoleInDB]:
        """Get all role relationships for a specific page"""
        try:
            page_role_docs = self.find_many({
                "page_id": page_id,
                "is_active": True
            })
            return [PageRoleInDB(**doc) for doc in page_role_docs]
        except Exception as e:
            logger.error(f"Error getting page roles for page {page_id}: {e}")
            return []
    
    def get_page_roles_by_role_id(self, role_id: str) -> List[PageRoleInDB]:
        """Get all page relationships for a specific role"""
        try:
            page_role_docs = self.find_many({
                "role_id": role_id,
                "is_active": True
            })
            return [PageRoleInDB(**doc) for doc in page_role_docs]
        except Exception as e:
            logger.error(f"Error getting role pages for role {role_id}: {e}")
            return []
    
    def get_page_role(self, page_id: str, role_id: str) -> Optional[PageRoleInDB]:
        """Get specific page-role relationship"""
        try:
            page_role_doc = self.find_one({
                "page_id": page_id,
                "role_id": role_id,
                "is_active": True
            })
            return PageRoleInDB(**page_role_doc) if page_role_doc else None
        except Exception as e:
            logger.error(f"Error getting page-role relationship: {e}")
            return None
    
    def update_page_role(self, page_role_id: str, update_data: PageRoleUpdate) -> bool:
        """Update page-role relationship"""
        try:
            update_dict = update_data.dict(exclude_unset=True)
            update_dict["updated_at"] = datetime.now()
            
            return self.update(page_role_id, update_dict)
        except Exception as e:
            logger.error(f"Error updating page-role {page_role_id}: {e}")
            return False
    
    def delete_page_role(self, page_role_id: str) -> bool:
        """Soft delete page-role relationship"""
        try:
            return self.update(page_role_id, {
                "is_active": False,
                "updated_at": datetime.now()
            })
        except Exception as e:
            logger.error(f"Error deleting page-role {page_role_id}: {e}")
            return False
    
    def delete_page_role_by_ids(self, page_id: str, role_id: str) -> bool:
        """Delete page-role relationship by page and role IDs"""
        try:
            page_role = self.get_page_role(page_id, role_id)
            if page_role:
                return self.delete_page_role(page_role.id)
            return False
        except Exception as e:
            logger.error(f"Error deleting page-role by IDs: {e}")
            return False
    
    def get_roles_for_page(self, page_id: str) -> List[str]:
        """Get all role IDs that have access to a specific page"""
        try:
            page_roles = self.get_page_roles_by_page_id(page_id)
            return [pr.role_id for pr in page_roles]
        except Exception as e:
            logger.error(f"Error getting roles for page {page_id}: {e}")
            return []
    
    def get_pages_for_role(self, role_id: str) -> List[str]:
        """Get all page IDs that a specific role has access to"""
        try:
            role_pages = self.get_page_roles_by_role_id(role_id)
            return [rp.page_id for rp in role_pages]
        except Exception as e:
            logger.error(f"Error getting pages for role {role_id}: {e}")
            return []
    
    def grant_role_page_access(self, role_id: str, page_id: str, permissions: List[str] = None) -> bool:
        """Grant a role access to a page"""
        try:
            if permissions is None:
                permissions = ["read"]
            
            # Check if relationship already exists
            existing = self.get_page_role(page_id, role_id)
            if existing:
                # Update existing relationship
                return self.update_page_role(existing.id, PageRoleUpdate(permissions=permissions))
            else:
                # Create new relationship
                page_role_data = PageRoleCreate(
                    page_id=page_id,
                    role_id=role_id,
                    permissions=permissions
                )
                self.create_page_role(page_role_data)
                return True
        except Exception as e:
            logger.error(f"Error granting role page access: {e}")
            return False
    
    def revoke_role_page_access(self, role_id: str, page_id: str) -> bool:
        """Revoke a role's access to a page"""
        try:
            return self.delete_page_role_by_ids(page_id, role_id)
        except Exception as e:
            logger.error(f"Error revoking role page access: {e}")
            return False
