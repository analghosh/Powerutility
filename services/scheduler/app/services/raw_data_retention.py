"""
Raw ingestion data retention.

Every fetch and upload is stored in a *_raw_data collection, with one row in
*_raw_data_processing_status. Nothing removed them, and reading pending data
slowed down with every day kept. Once processed, the raw copy is only needed
for a few days of troubleshooting, like *_integration_apilog (7-day TTL).
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from bson import ObjectId
from pymongo.errors import OperationFailure, PyMongoError

logger = logging.getLogger(__name__)

RAW_COLLECTION = re.compile(r"^(?:[a-z0-9_]+_)?raw_data(?:_processing_status)?$")


async def enforce_raw_data_retention(db, days: int) -> Dict[str, Dict[str, Any]]:
    """
    Keep `days` of raw data in every *_raw_data / *_raw_data_processing_status collection.

    Time-series raw collections get a TTL, which drops whole buckets. A delete on
    their time field unpacks every bucket, so it is only the fallback when the TTL
    cannot be set. Plain collections, including status rows (stored without
    created_at), are trimmed by _id time, which is indexed.
    """
    ttl_seconds = days * 24 * 3600
    cutoff_local = datetime.now() - timedelta(days=days)  # created_at is the host's naive local clock
    cutoff_id = ObjectId.from_datetime(datetime.now(timezone.utc) - timedelta(days=days))

    report: Dict[str, Dict[str, Any]] = {}
    for info in await (await db.list_collections()).to_list(length=None):
        name = info["name"]
        if not RAW_COLLECTION.match(name):
            continue
        try:
            report[name] = await _retain(db, info, days, ttl_seconds, cutoff_local, cutoff_id)
        except PyMongoError as e:
            logger.error("Raw data retention failed for %s: %s", name, e)
            report[name] = {"error": str(e)}

    return report


async def _retain(db, info: Dict[str, Any], days: int, ttl_seconds: int,
                  cutoff_local: datetime, cutoff_id: ObjectId) -> Dict[str, Any]:
    name = info["name"]
    if info.get("type") != "timeseries":
        result = await db[name].delete_many({"_id": {"$lt": cutoff_id}})
        return {"deleted": result.deleted_count}

    if info.get("options", {}).get("expireAfterSeconds") == ttl_seconds:
        return {"ttl_days": days}

    try:
        await db.command("collMod", name, expireAfterSeconds=ttl_seconds)
        logger.info("Set a %d-day TTL on %s", days, name)
        return {"ttl_days": days, "ttl_set": True}
    except OperationFailure as e:
        logger.warning("Could not set a TTL on %s (%s); deleting old raw data instead", name, e)
        result = await db[name].delete_many({"created_at": {"$lt": cutoff_local}})
        return {"ttl_error": str(e), "deleted": result.deleted_count}
