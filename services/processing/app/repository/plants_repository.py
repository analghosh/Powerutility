from typing import Any, Dict, List, Optional, Tuple, Callable
from bson import ObjectId, Decimal128
from pymongo import ReturnDocument
import logging
from datetime import datetime


from libs.database.base_repository import BaseRepository
from libs.models.plant_entity import PlantEntity
from ..core.config import settings

logger = logging.getLogger(__name__)

# ---- BSON → JSON-safe helpers ----
def _coerce_bson(val: Any) -> Any:
    if isinstance(val, ObjectId):
        return str(val)
    if isinstance(val, Decimal128):
        # choose float or str per your needs; float shown
        return float(val.to_decimal())
    if isinstance(val, list):
        return [_coerce_bson(v) for v in val]
    if isinstance(val, dict):
        return {k: _coerce_bson(v) for k, v in val.items()}
    return val

def _serialize_doc(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return _coerce_bson(doc) if doc is not None else None

def _serialize_many(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [doc for doc in [_serialize_doc(d) for d in items] if doc is not None]

class ServeMasterPlantsRepository(BaseRepository):


    def __init__(self):
        """Initialize plant repository"""
        super().__init__(
            collection_name=settings.MONGODB_PLANTS_COLLECTION,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )
        # Index creation is async; skipped here to avoid RuntimeWarning
    # ---------- Read ----------
    async def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 50,
        sort: Optional[List[Tuple[str, int]]] = None,
        projection: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        q = filters or {}
        cursor = self.collection.find(q, projection or {})
        if sort:
            cursor = cursor.sort(sort)
        total = await self.collection.count_documents(q)
        page = max(page, 1)
        page_size = max(min(page_size, 200), 1)
        cursor = cursor.skip((page - 1) * page_size).limit(page_size)
        items = await cursor.to_list(None)
        return {
            "items": _serialize_many(items),  # <—— stringify _id & other BSON
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def find_by_id(self, id: str) -> Optional[Dict[str, Any]]:
        """Find document by ID with BSON serialization"""
        try:
            if not ObjectId.is_valid(id):
                return None
            doc = await self.collection.find_one({"_id": ObjectId(id)})
            return _serialize_doc(doc)  # Apply BSON serialization
        except Exception as e:
            logger.error(f"Error finding document by ID in {self.collection_name}: {e}")
            return None

    async def update(self, id: str, data: Dict[str, Any], add_timestamps: bool = True) -> Optional[Dict[str, Any]]:
        """Update document by ID and return the updated document with BSON serialization"""
        try:
            if not ObjectId.is_valid(id):
                return None
            if add_timestamps:
                data["updated_at"] = datetime.now()
            
            # Use findOneAndUpdate to get the updated document
            result = await self.collection.find_one_and_update(
                {"_id": ObjectId(id)},
                {"$set": data},
                return_document=ReturnDocument.AFTER
            )
            
            if result:
                logger.info(f"Updated document in {self.collection_name} with ID: {id}")
            return _serialize_doc(result)  # Apply BSON serialization
        except Exception as e:
            logger.error(f"Error updating document in {self.collection_name}: {e}")
            return None

    def _create_unique_index(self):
        """Create unique compound index on sector, state_ut, and plant_name"""
        try:
            # Create compound unique index
            index_spec = [
                ("category", 1),
                ("c_s", 1), 
                ("plant_name", 1)
            ]
            self.create_index(index_spec, unique=True)
        except Exception as e:
            # Log error but don't fail initialization
            print(f"Warning: Could not create unique index: {e}")
    
    def bulk_upsert_plants(self, plants: List[Dict[str, Any]], filter_criteria: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, int]:
        """
        Bulk upsert plants using custom filter criteria provided by the service
        
        Args:
            plants: List of plant dictionaries to upsert
            filter_criteria: Function that takes a plant document and returns the filter query for finding existing plants
            
        Returns:
            Dictionary with counts of inserted and updated documents
        """
        return self.bulk_upsert(plants, filter_criteria=filter_criteria)
    


serve_master_plants_repository = ServeMasterPlantsRepository()
