"""The only file that talks to Mongo for analytics reads.

It exists because of two decisions that cannot be made anywhere else:

1. **It raises.** `GenericRepository.aggregate()` catches every exception, logs it
   and returns `[]`. For the bidding screens that is a survivable choice; for
   analytics it is a lie, because `[]` is also what a legitimately empty date range
   returns. The dashboard has to tell "no data" apart from "the query failed", so
   `run_pipeline` lets the exception out and the executor turns it into a
   partial-data banner. Nothing here may call the inherited `aggregate()`.

2. **It gets its own Motor client.** `MongoClientFactory.get_motor_client()` keys one
   `AsyncIOMotorClient` per name, so asking for `"processing_service"` would put a
   five-month analytics sweep in the same connection pool as the bid screens.
   Bidding is the money path: a report must never be able to exhaust it. This
   repository asks for `"processing_analytics"` with its own, smaller `maxPoolSize`.

Documents are returned exactly as Mongo produced them — no `_serialize_value` pass.
Analytics documents are one small `_id` plus a float per dataset, and at
individual-block granularity there are hundreds of thousands of them; walking every
value to stringify BSON types that this pipeline cannot produce would cost real time
and would turn a float into a string if it ever did.
"""
import logging
from typing import Any, Dict, List, Optional, Sequence

from ...core.config import settings
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

#: Motor client name. Must differ from `"processing_service"` — see the docstring.
ANALYTICS_CLIENT_NAME = "processing_analytics"

#: Pool for that client. Deliberately modest: analytics runs a handful of
#: concurrent aggregations (`ANALYTICS_MAX_CONCURRENT` x `ANALYTICS_GROUP_CONCURRENCY`
#: at the very worst) and every connection it holds is one the database is not
#: keeping for the bidding path.
DEFAULT_MAX_POOL_SIZE = 10
DEFAULT_MIN_POOL_SIZE = 1

DEFAULT_MAX_TIME_MS = 20_000
DEFAULT_BATCH_SIZE = 1_000


class ResultTooLargeError(RuntimeError):
    """A pipeline returned more documents than the caller allowed.

    Raised rather than truncating: a short table that looks complete is the exact
    failure mode the cell budget exists to prevent, and a caller that asked for a
    cap wants to know it was hit.
    """


class AnalyticsRepository(GenericRepository):
    """Raw aggregation execution for the analytics dashboard."""

    def __init__(self):
        super().__init__()
        self._db_override = None
        logger.info(
            "AnalyticsRepository initialized (lazy Motor client '%s')",
            ANALYTICS_CLIENT_NAME,
        )

    # ------------------------------------------------------------ plumbing

    @property
    def client(self):
        """Motor client dedicated to analytics.

        Lazy, like every v2 repository: building a client in `__init__` is what
        binds Motor to the wrong event loop, and that failure returns `[]` from
        every query instead of raising.
        """
        if self._client is None:
            from libs.database.mongo_client_factory import get_motor_client
            self._client = get_motor_client(
                settings.MONGODB_URI,
                client_name=ANALYTICS_CLIENT_NAME,
                maxPoolSize=getattr(
                    settings, "ANALYTICS_MONGO_MAX_POOL_SIZE", DEFAULT_MAX_POOL_SIZE),
                minPoolSize=getattr(
                    settings, "ANALYTICS_MONGO_MIN_POOL_SIZE", DEFAULT_MIN_POOL_SIZE),
            )
            logger.info(
                "Analytics Motor client '%s' obtained from factory",
                ANALYTICS_CLIENT_NAME,
            )
        return self._client

    @property
    def db(self):
        # Settable so unit tests can hand in a mapping of fake collections, the
        # house convention.
        if self._db_override is not None:
            return self._db_override
        if self._db is None:
            self.db_name = settings.MONGODB_DATABASE
            self._db = self.client[self.db_name]
            logger.info("Analytics repository connected to database: %s", self.db_name)
        return self._db

    @db.setter
    def db(self, value):
        self._db_override = value

    # --------------------------------------------------------------- reads

    async def get_parameters(self, keys: Sequence[str],
                             collection: str = "parameters") -> Dict[str, Any]:
        """`para_key` → `para_value` for the keys present.

        The collection carries duplicate keys in places (seven `rtm_bid_price`
        rows locally), so the newest `updated_at` wins for each key — the same
        row the Masters screen edits last.
        """
        keys = [str(k) for k in keys]
        if not keys:
            return {}
        cursor = self.db[collection].find(
            {"para_key": {"$in": keys}},
            {"_id": 0, "para_key": 1, "para_value": 1, "updated_at": 1},
        ).sort("updated_at", -1)
        values: Dict[str, Any] = {}
        async for doc in cursor:
            values.setdefault(doc.get("para_key"), doc.get("para_value"))
        return values

    async def run_pipeline(self, collection: str, pipeline: List[Dict[str, Any]],
                           max_time_ms: Optional[int] = None, *,
                           allow_disk_use: bool = True,
                           batch_size: int = DEFAULT_BATCH_SIZE,
                           max_docs: Optional[int] = None) -> List[Dict[str, Any]]:
        """Execute one analytics aggregation and return its documents.

        Raises whatever Mongo raises. A `maxTimeMS` is always sent, so a pipeline
        that finds an unexpected collection scan is killed server-side instead of
        occupying a connection until the HTTP timeout; that ceiling is the second
        half of the cell budget's protection, the first half being arithmetic done
        before this is ever called.
        """
        if max_time_ms is None:
            max_time_ms = getattr(
                settings, "ANALYTICS_QUERY_MAX_TIME_MS", DEFAULT_MAX_TIME_MS)

        cursor = self.db[collection].aggregate(
            pipeline,
            allowDiskUse=allow_disk_use,
            batchSize=batch_size,
            maxTimeMS=max_time_ms,
        )

        results: List[Dict[str, Any]] = []
        try:
            async for document in cursor:
                results.append(document)
                if max_docs is not None and len(results) > max_docs:
                    raise ResultTooLargeError(
                        f"Aggregation on '{collection}' returned more than "
                        f"{max_docs} documents; narrow the range rather than "
                        f"rendering a partial table as if it were complete"
                    )
        except ResultTooLargeError:
            logger.error(
                "Analytics aggregation on '%s' exceeded max_docs=%s", collection, max_docs)
            raise
        except Exception as exc:
            # Logged here, re-raised for the executor: analytics must never report
            # a failed query as an empty result.
            logger.error(
                "Analytics aggregation failed on '%s' (%d stages, maxTimeMS=%s): %s",
                collection, len(pipeline), max_time_ms, exc,
            )
            raise

        logger.info(
            "Analytics aggregation on '%s' returned %d documents",
            collection, len(results),
        )
        return results
