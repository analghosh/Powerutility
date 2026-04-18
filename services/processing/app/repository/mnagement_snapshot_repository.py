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
#         return self.collection.find_one(
#         {"inserted_date": date},
#         sort=[("_id", DESCENDING)]
#     )

#     def get_market_snapshot_by_date(self, date, cronjob):
#         return list(self.collection.find({
#             "inserted_date": date,
#             "cronJob_time": cronjob
#         }))


# services/processing/app/repository/serve_exchange_table_repository.py

import os
import logging
from typing import Any, Dict, Iterable, List, Optional

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

logger = logging.getLogger(__name__)


class ServeExchangeTableRepository:
    """
    Lightweight repository for the `serve_exchange_table` collection.

    Env vars (with sensible defaults):
      - MONGO_URI       -> "mongodb://localhost:27017"
      - MONGO_DB_NAME   -> "epms"
      - MONGO_COLL_EXCH -> "serve_exchange_table"

    Usage:
        repo = ServeExchangeTableRepository()
        rows = repo.aggregate(pipeline)
    """

    def __init__(
        self,
        client: Optional[MongoClient] = None,
        db_name: Optional[str] = None,
        collection_name: Optional[str] = None,
    ) -> None:
        # Use provided client or get from centralized factory
        if client is None:
            from libs.database.mongo_client_factory import get_processing_client
            uri = os.getenv("MONGO_URI", os.getenv("MONGODB_URI", "mongodb://localhost:27017"))
            self._client: MongoClient = get_processing_client(uri)
            logger.debug("Using centralized MongoClient from factory")
        else:
            self._client: MongoClient = client
            logger.debug("Using injected MongoClient")

        dbn = db_name or os.getenv("MONGO_DB_NAME", "epms")
        self._db: Database = self._client[dbn]

        coll_name = collection_name or os.getenv("MONGO_COLL_EXCH", "serve_exchange_table")
        self._coll: Collection = self._db[coll_name]

        logger.debug(
            "ServeExchangeTableRepository initialized (db=%s, coll=%s)",
            dbn, coll_name,
        )

    # ------------- basic accessors -------------
    @property
    def db(self) -> Database:
        return self._db

    @property
    def collection(self) -> Collection:
        return self._coll

    # ------------- read helpers -------------
    def aggregate(
        self,
        pipeline: Iterable[Dict[str, Any]],
        *,
        allow_disk_use: bool = True,
        collation: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run an aggregation and return a list. Designed for read-heavy operations.
        """
        logger.debug("Aggregate on %s with pipeline: %s", self._coll.full_name, pipeline)
        cursor = self._coll.aggregate(
            list(pipeline),
            allowDiskUse=allow_disk_use,
            collation=collation,
        )
        result = list(cursor)
        logger.debug("Aggregate returned %d documents", len(result))
        return result

    def find(
        self,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """
        Simple wrapper over find(). Returns a list for convenience.
        Extra kwargs are passed to pymongo find (e.g., sort, limit).
        """
        filter = filter or {}
        logger.debug("Find on %s filter=%s projection=%s kwargs=%s",
                     self._coll.full_name, filter, projection, kwargs)
        cursor = self._coll.find(filter, projection, **kwargs)
        return list(cursor)

    def find_one(
        self,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        filter = filter or {}
        logger.debug("Find_one on %s filter=%s projection=%s",
                     self._coll.full_name, filter, projection)
        return self._coll.find_one(filter, projection)

    def count_documents(self, filter: Optional[Dict[str, Any]] = None) -> int:
        filter = filter or {}
        count = self._coll.count_documents(filter)
        logger.debug("count_documents on %s filter=%s -> %d",
                     self._coll.full_name, filter, count)
        return count

    # ------------- utilities -------------
    def ping(self) -> bool:
        """Quick connectivity check."""
        try:
            self._client.admin.command("ping")
            return True
        except Exception as exc:
            logger.warning("Mongo ping failed: %s", exc)
            return False
