
from typing import List, Dict, Optional, Any
from .base_repository import BaseRepository
import logging

logger = logging.getLogger(__name__)


class SupplyTableRepository(BaseRepository):
    def __init__(self, collection_name: str = "supply_table_api"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DashboardRepository initialized with collection: {collection_name}")