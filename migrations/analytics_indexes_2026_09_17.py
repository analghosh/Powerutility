"""Read-path indexes for the analytics dashboard.

Standalone and idempotent on purpose: the vendor's index bootstrap aborts on the
users.email unique conflict and has never run to completion, so nothing here may
depend on it.

Run directly:
    ./.venv/bin/python -m migrations.analytics_indexes_2026_09_17
"""
import asyncio
import logging
from typing import Any, Dict, List

from pymongo.errors import OperationFailure

logger = logging.getLogger(__name__)

ANALYTICS_INDEXES: List[Dict[str, Any]] = [
    {
        "collection": "exchange_serve",
        "keys": [("scheduled_at", 1), ("market_type", 1), ("time_block", 1), ("px_name", 1)],
        "name": "idx_analytics_exchange_serve",
        "unique": False,
    },
    {
        "collection": "exchange_serve_historical",
        "keys": [("scheduled_at", 1), ("market_type", 1), ("time_block", 1), ("px_name", 1)],
        "name": "idx_analytics_exchange_serve_hist",
        "unique": False,
    },
    {
        "collection": "bid_trade_serve",
        "keys": [("scheduled_at", 1), ("type", 1), ("time_block", 1), ("exchange", 1)],
        "name": "idx_analytics_bid_trade_serve",
        "unique": False,
    },
    {
        "collection": "bid_trade_serve_historical",
        "keys": [("scheduled_at", 1), ("type", 1), ("time_block", 1), ("exchange", 1)],
        "name": "idx_analytics_bid_trade_serve_hist",
        "unique": False,
    },
    # Demand is the most-used source group (18 datasets) and was a COLLSCAN on both
    # the serve and the historical side: serve's only index leads with `type`, which
    # the analytics $match deliberately does not filter on (discriminators live in the
    # accumulator so two datasets can share one pipeline). Range on scheduled_at first.
    {
        "collection": "demand_serve",
        "keys": [("scheduled_at", 1), ("time_block", 1)],
        "name": "idx_analytics_demand_serve",
        "unique": False,
    },
    {
        "collection": "demand_serve_historical",
        "keys": [("scheduled_at", 1), ("time_block", 1)],
        "name": "idx_analytics_demand_serve_hist",
        "unique": False,
    },
    {
        "collection": "supply_serve_historical",
        "keys": [("dc_sg", 1), ("scheduled_at", 1), ("time_block", 1)],
        "name": "idx_analytics_supply_serve_hist",
        "unique": False,
    },
    {
        "collection": "peak_demand_serve",
        "keys": [("scheduled_at", 1), ("time_block", 1)],
        "name": "idx_analytics_peak_serve",
        "unique": False,
    },
    {
        "collection": "peak_demand_serve_historical",
        "keys": [("scheduled_at", 1), ("time_block", 1)],
        "name": "idx_analytics_peak_serve_hist",
        "unique": False,
    },
    {
        "collection": "analytics_export_jobs",
        "keys": [("user_id", 1), ("created_at", -1)],
        "name": "idx_analytics_jobs_by_user",
        "unique": False,
    },
    {
        "collection": "analytics_export_jobs",
        "keys": [("status", 1), ("heartbeat_at", 1)],
        "name": "idx_analytics_jobs_reaper",
        "unique": False,
    },
    {
        "collection": "analytics_export_jobs",
        "keys": [("user_id", 1), ("request_hash", 1), ("status", 1)],
        "name": "idx_analytics_jobs_dedupe",
        "unique": False,
    },
    # One preference row per user. Unique because the row is written by an upsert:
    # two saves racing on a first-ever save would otherwise insert two documents and
    # the screen would then read whichever Mongo returned first.
    {
        "collection": "analytics_user_preferences",
        "keys": [("user_id", 1)],
        "name": "idx_analytics_prefs_by_user",
        "unique": True,
    },
]


async def apply(db) -> List[str]:
    """Create every analytics index. Safe to run repeatedly."""
    created: List[str] = []
    for spec in ANALYTICS_INDEXES:
        try:
            await db[spec["collection"]].create_index(
                spec["keys"],
                name=spec["name"],
                background=True,
                unique=spec.get("unique", False),
            )
            created.append(spec["name"])
            logger.info(f"Index ready: {spec['collection']}.{spec['name']}")
        except OperationFailure as e:
            logger.info(f"Index {spec['collection']}.{spec['name']} already present: {e}")
        except Exception as e:
            logger.error(f"Index {spec['collection']}.{spec['name']} failed: {e}")
    return created


async def _main():
    logging.basicConfig(level=logging.INFO)
    from services.processing.app.core.config import settings
    from libs.database.mongo_client_factory import get_processing_client

    client = get_processing_client(settings.MONGODB_URI)
    created = await apply(client[settings.MONGODB_DATABASE])
    print(f"Created or confirmed {len(created)} indexes: {created}")


if __name__ == "__main__":
    asyncio.run(_main())
