# from typing import List, Dict, Optional, Any
# from .base_repository import BaseRepository
# import logging 
# from datetime import datetime, timedelta
# from pymongo import DESCENDING

# logger = logging.getLogger(__name__)

# class MnagementSnapshotRepository(BaseRepository):
#     def __init__(self, collection_name: str = "iex_market_snapshot"):
#         super().__init__(collection_name=collection_name)
#         logger.info(f"MnagementSnapshotRepository initialized with collection: {collection_name}")
    
#     def get_first_recode(self, schedule_date: str) -> Optional[Dict[str, Any]]:
#         return await self.collection.find_one(
#         {"inserted_date": date},
#         sort=[("_id", DESCENDING)]
#     )

#     def get_market_snapshot_by_date(self, date, cronjob):
#         return await self.collection.find({
#             "inserted_date": date,
#             "cronJob_time": cronjob
#         }).to_list(length=10000)


# services/processing/app/repository/serve_exchange_table_repository.py

import os
import logging
from typing import Any, Dict, Iterable, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


class ServeExchangeTableRepository:
    """
    Async repository for the `serve_exchange_table` collection using Motor.

    Env vars (with sensible defaults):
      - MONGO_URI       -> "mongodb://localhost:27017"
      - MONGO_DB_NAME   -> "epms"
      - MONGO_COLL_EXCH -> "serve_exchange_table"

    Usage:
        repo = ServeExchangeTableRepository()
        rows = await repo.aggregate(pipeline)
    """

    def __init__(
        self,
        client: Optional[AsyncIOMotorClient] = None,
        db_name: Optional[str] = None,
        collection_name: Optional[str] = None,
    ) -> None:
        if client is None:
            from libs.database.mongo_client_factory import get_processing_client
            uri = os.getenv("MONGO_URI", os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
            self._client: AsyncIOMotorClient = get_processing_client(uri)
            logger.debug("Using centralized AsyncIOMotorClient from factory")
        else:
            self._client: AsyncIOMotorClient = client
            logger.debug("Using injected AsyncIOMotorClient")

        dbn = db_name or os.getenv("MONGO_DB_NAME", "epms")
        self._db: AsyncIOMotorDatabase = self._client[dbn]

        coll_name = collection_name or os.getenv("MONGO_COLL_EXCH", "serve_exchange_table")
        self._coll: AsyncIOMotorCollection = self._db[coll_name]

        logger.debug(
            "ServeExchangeTableRepository initialized (db=%s, coll=%s)",
            dbn, coll_name,
        )

    # ------------- basic accessors -------------
    @property
    def db(self) -> AsyncIOMotorDatabase:
        return self._db

    @property
    def collection(self) -> AsyncIOMotorCollection:
        return self._coll

    # ------------- read helpers -------------
    async def aggregate(
        self,
        pipeline: Iterable[Dict[str, Any]],
        *,
        allow_disk_use: bool = True,
        collation: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Run an aggregation and return a list."""
        logger.debug("Aggregate on %s", self._coll.full_name)
        kwargs = {"allowDiskUse": allow_disk_use}
        if collation:
            kwargs["collation"] = collation
        cursor = self._coll.aggregate(list(pipeline), **kwargs)
        result = await cursor.to_list(length=10000)
        logger.debug("Aggregate returned %d documents", len(result))
        return result

    async def find(
        self,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, Any]] = None,
        sort: Optional[List] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Async find returning a list."""
        filter = filter or {}
        logger.debug("Find on %s filter=%s", self._coll.full_name, filter)
        cursor = self._coll.find(filter, projection)
        if sort:
            cursor = cursor.sort(sort)
        if limit:
            cursor = cursor.limit(limit)
        return await cursor.to_list(length=10000)

    async def find_one(
        self,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        filter = filter or {}
        logger.debug("Find_one on %s filter=%s", self._coll.full_name, filter)
        return await self._coll.find_one(filter, projection)

    async def count_documents(self, filter: Optional[Dict[str, Any]] = None) -> int:
        filter = filter or {}
        count = await self._coll.count_documents(filter)
        logger.debug("count_documents on %s -> %d", self._coll.full_name, count)
        return count

    # ------------- utilities -------------
    async def ping(self) -> bool:
        """Quick connectivity check."""
        try:
            await self._client.admin.command("ping")
            return True
        except Exception as exc:
            logger.warning("Mongo ping failed: %s", exc)
            return False
