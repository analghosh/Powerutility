import asyncio
from datetime import datetime, timedelta
import logging
import traceback
from typing import List, Dict, Any
from services.scheduler.app.core.config import settings
from libs.database.mongo_client_factory import get_mongo_database, get_mongo_client

logger = logging.getLogger(__name__)

async def deletion_worker(worker_id, queue, db, coll_name, stats):
    """
    Worker task that pulls batches from the queue and performs deletions in parallel.
    We use to_thread to avoid blocking the event loop with synchronous delete_many.
    """
    while True:
        batch = await queue.get()
        if batch is None:
            queue.task_done()
            break
        
        try:
            # pymongo is blocking, so we run in thread to allow parallel work
            res = await asyncio.to_thread(db[coll_name].delete_many, {"_id": {"$in": batch}})
            stats['deleted'] += res.deleted_count
            if stats['deleted'] % 50000 == 0 or stats['deleted'] < 50000:
                 logger.info(f"👷 Worker-{worker_id}: Progressed. Total removed so far: {stats['deleted']}")
        except Exception as e:
            logger.error(f"❌ Worker-{worker_id} Error: {str(e)}")
        finally:
            queue.task_done()

async def run_historical_deduplication():
    """
    Deduplicates records in historical collections using a Streaming ID Producer
    and Parallel Deletion Workers to prevent memory stalls and hangs.
    """
    logger.info("🚀 Starting STREAMING historical deduplication...")
    
    try:
        # High-performance client configuration
        client = get_mongo_client(settings.MONGODB_URI, "deduplicator_streaming_client", 
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
        chunk_days = 5      # Small chunks to keep MongoDB aggregation fast and lean
        
        loop = asyncio.get_running_loop()
        
        for config in dedup_configs:
            coll_name = config["collection"]
            keys = config["keys"]
            date_field = config["date_field"]
            display_name = config["name"]
            
            if not coll_name: continue
                
            logger.info(f"🛡️ Processing {display_name} | Workers: {num_workers} | Chunk: {chunk_days} days")
            
            # 1. Get Date Range
            def get_range():
                return list(db[coll_name].aggregate([
                    {"$group": {"_id": None, "min": {"$min": f"${date_field}"}, "max": {"$max": f"${date_field}"}}}
                ]))
            
            range_res = await asyncio.to_thread(get_range)
            if not range_res or not range_res[0]['min']:
                continue
                
            start_date, end_date = range_res[0]['min'], range_res[0]['max']
            
            # 2. Setup Workers and Queue
            queue = asyncio.Queue(maxsize=15)
            stats = {'deleted': 0}
            workers = [asyncio.create_task(deletion_worker(i, queue, db, coll_name, stats)) for i in range(num_workers)]
            
            # 3. Dedicated ID Producer Thread
            def id_producer_sync(sd, ed, q, lp):
                current_start = sd
                while current_start <= ed:
                    current_end = current_start + timedelta(days=chunk_days)
                    logger.info(f"🔍 Aggregating {display_name} chunk: {current_start.date()} to {current_end.date()}...")
                    
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
                        cursor = db[coll_name].aggregate(pipeline, allowDiskUse=True)
                        current_batch = []
                        for doc in cursor:
                            current_batch.append(doc["_id"])
                            if len(current_batch) >= batch_size:
                                # This blocks the producer thread until the queue has space
                                asyncio.run_coroutine_threadsafe(q.put(list(current_batch)), lp).result()
                                current_batch = []
                        if current_batch:
                            asyncio.run_coroutine_threadsafe(q.put(current_batch), lp).result()
                    except Exception as e:
                        logger.error(f"❌ Producer Error in chunk {current_start.date()}: {str(e)}")
                    
                    current_start = current_end
                
                # Close workers
                for _ in range(num_workers):
                    asyncio.run_coroutine_threadsafe(q.put(None), lp).result()

            # Start producer and wait
            await asyncio.gather(asyncio.to_thread(id_producer_sync, start_date, end_date, queue, loop), *workers)
            logger.info(f"🏆 {display_name} Finished. Total deleted: {stats['deleted']}")
            
        logger.info("🏁 All collections deduplicated.")

    except Exception as e:
        logger.error(f"❌ Deduplicator Failure: {str(e)}")
        logger.error(traceback.format_exc())
    finally:
        from libs.database.mongo_client_factory import close_mongo_client
        close_mongo_client("deduplicator_streaming_client")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}')
    asyncio.run(run_historical_deduplication())
