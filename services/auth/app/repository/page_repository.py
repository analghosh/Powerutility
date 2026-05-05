"""
Page repository for managing pages in the database
"""

from typing import Optional, List
from datetime import datetime
from bson import ObjectId
import logging
from .base_repository import BaseRepository
from ..models.pages import PageInDB, PageCreate, PageUpdate
from ..core.config import settings

logger = logging.getLogger(__name__)


class PageRepository(BaseRepository):
    """Page repository with page-specific database operations"""
    
    def __init__(self):
        super().__init__("pages", settings.AUTH_DB_NAME)
    
    async def create_page(self, page_data: PageCreate) -> PageInDB:
        """Create a new page"""
        try:
            # Check if page already exists
            if await self.get_page_by_name(page_data.name):
                raise ValueError("Page already exists")
            
            # Create page document
            page_doc = {
                "name": page_data.name,
                "module_id": page_data.module_id,
                "guard_name": page_data.guard_name,
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
            
            page_id = await self.create(page_doc)
            page_doc["_id"] = str(page_id)
            
            return PageInDB(**page_doc)
        except Exception as e:
            logger.error(f"Error creating page: {e}")
            raise
    
    async def get_page_by_id(self, page_id: str) -> Optional[PageInDB]:
        """Get page by ID"""
        try:
            page_doc = await self.find_one({
                "_id": ObjectId(page_id),
                "is_active": True
            })
            return PageInDB(**page_doc) if page_doc else None
        except Exception as e:
            logger.error(f"Error getting page by ID {page_id}: {e}")
            return None
    
    async def get_page_by_name(self, page_name: str) -> Optional[PageInDB]:
        """Get page by name"""
        try:
            page_doc = await self.find_one({
                "name": page_name,
                "is_active": True
            })
            return PageInDB(**page_doc) if page_doc else None
        except Exception as e:
            logger.error(f"Error getting page by name {page_name}: {e}")
            return None
    
    async def get_pages_by_service(self, service: str) -> List[PageInDB]:
        """Get all pages for a specific service"""
        try:
            page_docs = await self.find_many({
                "service": service,
                "is_active": True
            })
            return [PageInDB(**doc) for doc in page_docs]
        except Exception as e:
            logger.error(f"Error getting pages for service {service}: {e}")
            return []
    
    async def get_all_active_pages(self) -> List[PageInDB]:
        """Get all active pages"""
        try:
            page_docs = await self.find_many({"is_active": True})
            return [PageInDB(**doc) for doc in page_docs]
        except Exception as e:
            logger.error(f"Error getting all active pages: {e}")
            return []
    
    async def update_page(self, page_id: str, update_data: PageUpdate) -> bool:
        """Update page information"""
        try:
            # Convert update data to dict, excluding None values
            update_dict = update_data.dict(exclude_unset=True)
            update_dict["updated_at"] = datetime.now().isoformat()
            
            # Convert endpoints to dict if present
            if "endpoints" in update_dict:
                update_dict["endpoints"] = [endpoint.dict() for endpoint in update_dict["endpoints"]]
            
            return await self.update(page_id, update_dict)
        except Exception as e:
            logger.error(f"Error updating page {page_id}: {e}")
            return False
    
    async def delete_page(self, page_id: str) -> bool:
        """Soft delete page"""
        try:
            return await self.update(page_id, {
                "is_active": False,
                "updated_at": datetime.now().isoformat()
            })
        except Exception as e:
            logger.error(f"Error deleting page {page_id}: {e}")
            return False
    
    async def get_page_endpoints(self, page_name: str) -> Optional[List[dict]]:
        """Get endpoints for a specific page"""
        try:
            page = await self.get_page_by_name(page_name)
            return page.endpoints if page else None
        except Exception as e:
            logger.error(f"Error getting endpoints for page {page_name}: {e}")
            return None
