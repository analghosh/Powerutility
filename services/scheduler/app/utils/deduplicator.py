import asyncio
from datetime import datetime, timedelta
import logging
import traceback
from typing import List, Dict, Any
from services.scheduler.app.core.config import settings
from libs.database.mongo_client_factory import get_motor_database, get_motor_client

logger = logging.getLogger(__name__)

async def deletion_worker(worker_id, queue, db, coll_name, stats):
    """
    Worker task that pulls batches from the queue and performs deletions in parallel.
    Uses Motor async delete_many directly.
    """
    while True:
        batch = await queue.get()
        if batch is None:
            queue.task_done()
            break

        try:
            res = await db[coll_name].delete_many({"_id": {"$in": batch}})
            stats['deleted'] += res.deleted_count
            if stats['deleted'] % 50000 == 0 or stats['deleted'] < 50000:
                logger.info(f"Worker-{worker_id}: Progressed. Total removed so far: {stats['deleted']}")
        except Exception as e:
            logger.error(f"Worker-{worker_id} Error: {str(e)}")
        finally:
            queue.task_done()

async def id_producer_async(db, coll_name, date_field, keys, display_name, batch_size, chunk_days, start_date, end_date, queue, num_workers):
    """
    Async ID producer — iterates Motor cursor asynchronously and puts batches in queue.
    """
    current_start = start_date
    try:
        while current_start <= end_date:
            current_end = current_start + timedelta(days=chunk_days)
            logger.info(f"Aggregating {display_name} chunk: {current_start.date()} to {current_end.date()}...")

            group_id = {key: f"${key}" for key in keys}
            pipeline = [
                {"$match": {date_field: {"$gte": current_start, "$lt": current_end}}},
                {"$sort": {"archived_at": -1, "archived_timestamp": -1}},
                {"$group": {
                    "_id": group_id,
                    "keep_id": {"$first": "$_id"},
                    "all_ids": {"$push": "$_id"},
                    "count": {"$sum": 1}
                }},
                {"$match": {"count": {"$gt": 1}}},
                {"$unwind": "$all_ids"},
                {"$project": {"_id": "$all_ids", "is_keep": {"$eq": ["$all_ids", "$keep_id"]}}},
                {"$match": {"is_keep": False}},
                {"$project": {"_id": 1}}
            ]

            try:
                current_batch = []
                async for doc in db[coll_name].aggregate(pipeline, allowDiskUse=True):
                    current_batch.append(doc["_id"])
                    if len(current_batch) >= batch_size:
                        await queue.put(list(current_batch))
                        current_batch = []
                if current_batch:
                    await queue.put(current_batch)
            except Exception as e:
                logger.error(f"Producer Error in chunk {current_start.date()}: {str(e)}")

            current_start = current_end
    finally:
        # Close workers
        for _ in range(num_workers):
            await queue.put(None)

async def run_historical_deduplication():
    """
    Deduplicates records in historical collections using an async streaming ID producer
    and parallel deletion workers to prevent memory stalls and hangs.
    """
    logger.info("Starting STREAMING historical deduplication...")

    try:
        # High-performance client configuration
        client = get_motor_client(settings.MONGODB_URI, "deduplicator_streaming_client",
                                 socketTimeoutMS=3600000,
                                 maxPoolSize=100)
        db = client[settings.MONGODB_DATABASE]

        dedup_configs = [
            {
                "name": "Supply Serve Historical",
                "collection": settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION,
                "keys": ["scheduled_at", "time_block", "dc_sg", "plant_id"],
                "date_field": "scheduled_at"
            },
            {
                "name": "Demand Serve Historical",
                "collection": settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION,
                "keys": ["scheduled_at", "type", "time_block"],
                "date_field": "scheduled_at"
            },
            {
                "name": "Renewable Serve Historical",
                "collection": settings.MONGODB_RENEWABLE_SERVE_HISTORICAL_COLLECTION,
                "keys": ["scheduled_at", "time_block", "plant_id", "approval_no", "type"],
                "date_field": "scheduled_at"
            },
            {
                "name": "Exchange Serve Historical",
                "collection": settings.MONGODB_EXCHANGE_SERVE_HISTORICAL_COLLECTION,
                "keys": ["scheduled_at", "time_block", "market_type", "px_name"],
                "date_field": "scheduled_at"
            }
        ]

        batch_size = 10000
        num_workers = 3
        chunk_days = 5  # Small chunks to keep MongoDB aggregation fast and lean

        for config in dedup_configs:
            coll_name = config["collection"]
            keys = config["keys"]
            date_field = config["date_field"]
            display_name = config["name"]

            if not coll_name:
                continue

            logger.info(f"Processing {display_name} | Workers: {num_workers} | Chunk: {chunk_days} days")

            # 1. Get Date Range
            range_res = await db[coll_name].aggregate([
                {"$group": {"_id": None, "min": {"$min": f"${date_field}"}, "max": {"$max": f"${date_field}"}}}
            ]).to_list(length=10000)

            if not range_res or not range_res[0]['min']:
                continue

            start_date, end_date = range_res[0]['min'], range_res[0]['max']

            # 2. Setup Workers and Queue
            queue = asyncio.Queue(maxsize=15)
            stats = {'deleted': 0}
            workers = [asyncio.create_task(deletion_worker(i, queue, db, coll_name, stats)) for i in range(num_workers)]

            # 3. Run async producer alongside workers
            await asyncio.gather(
                id_producer_async(db, coll_name, date_field, keys, display_name, batch_size, chunk_days, start_date, end_date, queue, num_workers),
                *workers
            )
            logger.info(f"{display_name} Finished. Total deleted: {stats['deleted']}")

        logger.info("All collections deduplicated.")

    except Exception as e:
        logger.error(f"Deduplicator Failure: {str(e)}")
        logger.error(traceback.format_exc())
    finally:
        from libs.database.mongo_client_factory import close_mongo_client
        close_mongo_client("deduplicator_streaming_client")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}')
    asyncio.run(run_historical_deduplication())
