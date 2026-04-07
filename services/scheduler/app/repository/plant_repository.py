"""
Plant Repository for Scheduler Service
Handles database operations for plant entities
"""

from libs.database.base_repository import BaseRepository
from libs.models.plant_entity import PlantEntity
from services.scheduler.app.core.config import settings
from typing import List, Dict, Any


class PlantRepository(BaseRepository):
    """Repository for plant entities in scheduler service"""
    
    def __init__(self):
        """Initialize plant repository"""
        super().__init__(
            collection_name=settings.MONGODB_PLANTS_COLLECTION,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )
    # Note: All CRUD operations are available directly from BaseRepository:
    # - find_one(filter) - for finding by any criteria
    # - find_many(filter) - for finding multiple records
    # - find_with_pagination(filter, skip, limit) - for paginated results
    # - create(data) - for creating new records
    # - update(id, data) - for updating records
    # - delete(id) - for deleting records
    # - bulk_upsert(documents, filter_field) - for bulk operations
    # - etc.

plant_repository = PlantRepository()