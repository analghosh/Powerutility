from typing import Dict, Any, List, Optional
from bson import ObjectId
from datetime import datetime
import logging
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

class IexApiDetailRepository(GenericRepository):
    def __init__(self):
        super().__init__()
        self.collection_name = "iex_api_detail_list"

    async def get_all_details(self, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """
        Get all IEX API details, excluding soft-deleted records by default
        
        Args:
            include_deleted: If True, include soft-deleted records
            
        Returns:
            List of IEX API detail documents
        """
        filter_query = {}
        if not include_deleted:
            filter_query["is_deleted"] = {"$ne": True}
        
        return await self.get_all_async(
            self.collection_name, 
            filter=filter_query,
            sort=[("_id", -1)]
        )

    async def create_detail(self, data: Dict[str, Any]) -> Any:
        return await self.insert_one_async(self.collection_name, data)

    async def update_detail(self, id: str, data: Dict[str, Any]) -> Any:
        return await self.update_one_async(self.collection_name, {"_id": ObjectId(id)}, {"$set": data})

    async def delete_detail(self, id: str, deleted_by: Optional[str] = None) -> Any:
        """
        Soft delete a detail by setting is_deleted flag
        
        Args:
            id: Document ID to soft delete
            deleted_by: User ID or username who performed the deletion
            
        Returns:
            Update result from MongoDB
        """
        update_data = {
            "is_deleted": True,
            "deleted_at": datetime.now(),
            "updated_at": datetime.now()
        }
        
        if deleted_by:
            update_data["deleted_by"] = deleted_by
        
        return await self.update_one_async(
            self.collection_name,
            {"_id": ObjectId(id)},
            {"$set": update_data}
        )

    async def find_by_id(self, id: str, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
        """
        Find a detail by ID, excluding soft-deleted records by default
        
        Args:
            id: Document ID
            include_deleted: If True, include soft-deleted records
            
        Returns:
            Document if found, None otherwise
        """
        query = {"_id": ObjectId(id)}
        if not include_deleted:
            query["is_deleted"] = {"$ne": True}
        
        return await self.find_one_async(self.collection_name, query)

    async def check_duplicate(self, data: Dict[str, Any], exclude_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Check if a duplicate record exists based on unique fields.
        
        A record is considered duplicate if it has the same:
        - asset_id
        - bid_area_id
        - user_id
        - participant_id
        - portfolio_id
        - server_type
        - server_url
        
        Args:
            data: Data dictionary to check for duplicates
            exclude_id: Optional document ID to exclude from check (for updates)
            
        Returns:
            Existing document if duplicate found, None otherwise
        """
        try:
            # Build query for duplicate check
            query = {
                "asset_id": data.get("asset_id"),
                "bid_area_id": data.get("bid_area_id"),
                "user_id": data.get("user_id"),
                "participant_id": data.get("participant_id"),
                "portfolio_id": data.get("portfolio_id"),
                "server_type": data.get("server_type"),
                "server_url": data.get("server_url")
            }
            
            # Remove None values from query
            query = {k: v for k, v in query.items() if v is not None}
            
            # Exclude soft-deleted records
            query["is_deleted"] = {"$ne": True}
            
            # Exclude current document if updating
            if exclude_id:
                query["_id"] = {"$ne": ObjectId(exclude_id)}
            
            # Check if duplicate exists
            existing = await self.find_one_async(self.collection_name, query)
            return existing
            
        except Exception as e:
            logger.error(f"Error checking for duplicate: {e}")
            raise

    async def get_exchange_config(self, exchange: str) -> Optional[Dict[str, Any]]:
        """
        Fetch the configuration for a specific exchange.
        Prioritizes an active configuration if one exists.
        Otherwise, returns the most recently created configuration.
        """
        query = {
            "exchange": {"$in": [exchange, None]},
            "is_deleted": {"$ne": True}
        }
        
        results = await self.get_all_async(
            self.collection_name, 
            filter=query,
            sort=[("_id", -1)]
        )
        
        if not results:
            return None
            
        active_config = next((r for r in results if str(r.get("status", "")).strip().lower() == "active"), None)
        return active_config if active_config else results[0]

    async def get_active_base_url(self) -> str:
        """Fetch base_url from active details or return default."""
        # get_all_details already excludes soft-deleted records by default
        details = await self.get_all_details()
        active_detail = next((d for d in details if d.get("status") == "Active"), None)
        
        default_url = "http://0.0.0.0:8009"
        
        if active_detail and active_detail.get("base_url"):
            return active_detail.get("base_url").rstrip("/")
            
        return default_url

    
    
    async def deactivate_other_environment(
        self,
        exchange: str,
        exclude_id: str = None
    ):
        query = {
            "exchange": exchange,
            "status": "Active",
            "is_deleted": {"$ne": True}
        }

        if exclude_id:
            query["_id"] = {"$ne": ObjectId(exclude_id)}

        return await self.update_many_async(
            self.collection_name,
            query,
            {
                "$set": {
                    "status": "Inactive",
                    "updated_at": datetime.now()
                }
            }
        )