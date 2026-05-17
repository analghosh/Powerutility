"""
Parameter Repository (shared)
Read/write parameters collection for scheduler and processing services.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from libs.database.base_repository import BaseRepository
from libs.constants.idf_forecast import IDF_FORECAST_SWITCH_KEY

logger = logging.getLogger(__name__)

DEFAULT_PARAMETERS_COLLECTION = "parameters"


class ParameterRepository(BaseRepository):
    """Repository for parameters collection."""

    def __init__(
        self,
        mongodb_uri: str,
        database_name: str,
        collection_name: str = DEFAULT_PARAMETERS_COLLECTION,
    ):
        super().__init__(collection_name, mongodb_uri, database_name)

    async def get_by_key(self, para_key: str) -> Optional[Dict[str, Any]]:
        try:
            return await self.collection.find_one({"para_key": para_key})
        except Exception as e:
            logger.error("Error getting parameter %s: %s", para_key, e)
            return None

    async def upsert_by_key(
        self,
        para_key: str,
        para_value: str,
        extra_set: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = datetime.now()
        set_doc: Dict[str, Any] = {
            "para_value": para_value,
            "updated_at": now,
        }
        if extra_set:
            set_doc.update(extra_set)
        update_doc = {
            "$set": set_doc,
            "$setOnInsert": {
                "para_key": para_key,
                "created_at": now,
            },
        }
        await self.collection.update_one({"para_key": para_key}, update_doc, upsert=True)
        doc = await self.get_by_key(para_key)
        return doc or {}

    async def ensure_idf_forecast_switch_default(self) -> None:
        existing = await self.get_by_key(IDF_FORECAST_SWITCH_KEY)
        if not existing:
            await self.upsert_by_key(IDF_FORECAST_SWITCH_KEY, "true")
