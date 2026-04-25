"""
MongoDB-backed Queue Manager for Audit Logging with Threading
Uses Motor (AsyncIOMotorClient) with a dedicated persistent event loop thread.
All Motor coroutines are scheduled via asyncio.run_coroutine_threadsafe() onto
a single long-lived loop, avoiding the "Event loop is closed" error that occurs
when asyncio.run() creates and immediately destroys a loop per call.
"""

import uuid
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
import threading
from queue import Queue
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
import queue

from libs.audit_queue.config import config
from libs.audit_queue.models import AuditJob, JobStatus, LogPriority

logger = logging.getLogger(__name__)


class MongoAuditQueue:
    """MongoDB-backed audit queue manager with threading, using async Motor."""

    def __init__(
        self,
        mongodb_uri: str,
        audit_db_name: str,
        audit_collection_name: str,
        queue_collection_name: str,
        max_workers: int = 3,
    ):
        self.mongodb_uri = mongodb_uri
        self.audit_db_name = audit_db_name
        self.audit_collection_name = audit_collection_name
        self.queue_collection_name = queue_collection_name
        self.max_workers = max_workers
        self.mongo_client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None
        self.jobs_collection: Optional[AsyncIOMotorCollection] = None

        # Dedicated persistent event loop for all Motor operations.
        # Motor client is created ON this loop (see _async_initialize) so all
        # internal Motor futures are bound to it — no cross-loop errors.
        self._motor_loop = asyncio.new_event_loop()
        self._motor_loop_ready = threading.Event()
        self._motor_loop_thread = threading.Thread(
            target=self._run_motor_loop,
            daemon=True,
            name="AuditQueueMotorLoop",
        )
        self._motor_loop_thread.start()
        # Block until the loop is actually running before scheduling work on it.
        self._motor_loop_ready.wait(timeout=10.0)

        # Threading setup
        self.service_queues: Dict[str, Queue] = {}
        self.service_workers: Dict[str, List[threading.Thread]] = {}
        self.shutdown_event = threading.Event()

        # Metrics
        self.metrics = {
            "jobs_processed": 0,
            "jobs_failed": 0,
            "jobs_retried": 0,
            "last_processed": None,
        }

        self._initialize_connection()
        logger.info("MongoAuditQueue (async Motor) initialized")

    def _run_motor_loop(self):
        """Run the dedicated Motor event loop forever in its own thread."""
        asyncio.set_event_loop(self._motor_loop)
        self._motor_loop.call_soon(self._motor_loop_ready.set)
        self._motor_loop.run_forever()

    def _run_async(self, coro, timeout: float = 30.0):
        """Schedule a Motor coroutine on the dedicated loop and block until done.

        Safe to call from any thread (including asyncio handler threads) because
        the coroutine always runs on the dedicated Motor loop, not the caller's loop.

        Wraps `coro` in a native async function to satisfy run_coroutine_threadsafe's
        strict coroutine check (Motor 2.x may return wrapper objects, not native coros).
        """
        async def _wrap():
            return await coro

        future = asyncio.run_coroutine_threadsafe(_wrap(), self._motor_loop)
        return future.result(timeout=timeout)

    def _initialize_connection(self):
        """Initialize Motor connection on the dedicated loop."""
        try:
            self._run_async(self._async_initialize())
            logger.info("MongoDB async (Motor) connection established successfully")
        except Exception as e:
            logger.error("Unexpected error initializing MongoDB connection: %s", e)
            raise

    async def _async_initialize(self):
        """Create the Motor client on _motor_loop so all its futures are loop-local."""
        self.mongo_client = AsyncIOMotorClient(self.mongodb_uri, maxPoolSize=50, minPoolSize=5)
        self.db = self.mongo_client[self.audit_db_name]
        self.jobs_collection = self.db[self.queue_collection_name]
        await self._create_indexes()

    async def _create_indexes(self):
        """Create indexes for the jobs collection and audit_logs collection"""
        try:
            if self.jobs_collection is None:
                return

            await self.jobs_collection.create_index("status")
            await self.jobs_collection.create_index("created_at")
            await self.jobs_collection.create_index("source_service")
            await self.jobs_collection.create_index("job_type")
            await self.jobs_collection.create_index("priority")
            await self.jobs_collection.create_index([("status", 1), ("created_at", 1)])
            await self.jobs_collection.create_index(
                "updated_at",
                expireAfterSeconds=config.MAX_RETRY_COUNT * config.RETRY_DELAY_SECONDS * 2,
            )

            await self._create_audit_logs_ttl_indexes()
            logger.info("MongoDB indexes created successfully")

        except Exception as e:
            logger.warning("Could not create indexes: %s", e)

    async def _create_audit_logs_ttl_indexes(self):
        """Create TTL indexes for audit_logs collection based on priority levels"""
        try:
            audit_collection = self.db[self.audit_collection_name]

            await audit_collection.create_index(
                [("ttl_expiry_date", 1)],
                partialFilterExpression={"priority": "high"},
                expireAfterSeconds=0,
                name="ttl_high_priority",
            )
            await audit_collection.create_index(
                [("ttl_expiry_date", 1)],
                partialFilterExpression={"priority": "medium"},
                expireAfterSeconds=0,
                name="ttl_medium_priority",
            )
            await audit_collection.create_index(
                [("ttl_expiry_date", 1)],
                partialFilterExpression={"priority": "low"},
                expireAfterSeconds=0,
                name="ttl_low_priority",
            )
            await audit_collection.create_index([("timestamp", -1)])
            await audit_collection.create_index([("service", 1)])
            await audit_collection.create_index([("record_type", 1)])
            await audit_collection.create_index([("priority", 1)])

            logger.info("TTL indexes created for audit_logs collection")

        except Exception as e:
            logger.warning("Could not create audit_logs TTL indexes: %s", e)

    def add_to_queue(
        self,
        log_data: Dict[str, Any],
        source_service: str,
        destination: str = "mongo",
        job_type: str = "audit",
        priority: LogPriority = LogPriority.MEDIUM,
    ) -> str:
        """Add audit log or message to MongoDB queue for background processing"""
        if self.jobs_collection is None:
            logger.error("MongoDB collection not initialized")
            return ""

        try:
            job_id = str(uuid.uuid4())

            audit_job = AuditJob(
                job_id=job_id,
                log_data=log_data,
                destination=destination,
                source_service=source_service,
                job_type=job_type,
                priority=priority,
                status=JobStatus.SAVED,
                max_retries=config.MAX_RETRY_COUNT,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            self._run_async(self.jobs_collection.insert_one(audit_job.model_dump()))

            self._ensure_service_queue(source_service)
            self.service_queues[source_service].put(job_id)

            logger.debug("Job %s added to service queue for %s", job_id, source_service)
            return job_id

        except Exception as e:
            logger.error("Failed to add job to queue: %s", e)
            return ""

    def _ensure_service_queue(self, service_name: str):
        """Ensure service queue and workers exist for sequential processing"""
        if service_name not in self.service_queues:
            self.service_queues[service_name] = Queue()
            self.service_workers[service_name] = []

            worker_thread = threading.Thread(
                target=self._service_worker_loop,
                args=(service_name,),
                daemon=True,
                name=f"ServiceWorker-{service_name}",
            )
            worker_thread.start()
            self.service_workers[service_name].append(worker_thread)
            logger.info("Created sequential worker for service: %s", service_name)

    def _service_worker_loop(self, service_name: str):
        """Worker loop for specific service - ensures sequential processing"""
        worker_name = threading.current_thread().name
        service_queue = self.service_queues[service_name]
        logger.info("Service worker %s started for %s", worker_name, service_name)

        while not self.shutdown_event.is_set():
            try:
                job_id = service_queue.get(timeout=1.0)
                if job_id is None:
                    break
                self._process_job_directly(job_id)
                service_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                if not self.shutdown_event.is_set():
                    logger.error("Service worker %s error: %s", worker_name, e)
                continue

        logger.info("Service worker %s stopped", worker_name)

    def _process_job_directly(self, job_id: str):
        """Process job directly in background thread"""
        try:
            job_doc = self.get_job_status(job_id)
            if not job_doc:
                logger.error("Job %s not found", job_id)
                return

            audit_job = AuditJob(**job_doc)
            self.update_job_status(job_id, JobStatus.PROCESSING)

            success = self._log_audit_data(
                audit_job.log_data,
                audit_job.destination,
                audit_job.created_at,
                audit_job.job_type,
                audit_job.priority,
            )

            if success:
                self.update_job_status(job_id, JobStatus.COMPLETED)
                self.metrics["jobs_processed"] += 1
                self.metrics["last_processed"] = datetime.now(timezone.utc)
                logger.debug("Audit job %s completed successfully", job_id)
            else:
                self.update_job_status(job_id, JobStatus.FAILED, "Failed to log data")
                self.metrics["jobs_failed"] += 1

        except Exception as e:
            self._handle_job_retry(job_id, str(e))

    def _log_audit_data(
        self,
        log_data: Dict[str, Any],
        destination: str,
        timestamp: datetime,
        job_type: str = "audit",
        priority: LogPriority = LogPriority.MEDIUM,
    ) -> bool:
        """Log audit data or messages using the logger service"""
        try:
            if job_type == "message":
                return self._log_message_data(log_data, destination, timestamp, priority)
            else:
                return self._log_audit_data_direct(log_data, destination, timestamp, priority)
        except Exception as e:
            logger.error("Failed to log data: %s", e)
            return False

    def _log_message_data(
        self,
        message_data: Dict[str, Any],
        destination: str,
        timestamp: datetime,
        priority: LogPriority = LogPriority.MEDIUM,
    ) -> bool:
        """Log message data to audit_logs collection"""
        try:
            if destination != "mongo":
                logger.error("Only MongoDB destination supported, got: %s", destination)
                return False

            audit_collection = self.db[self.audit_collection_name]
            if "timestamp" not in message_data:
                message_data["timestamp"] = timestamp
            message_data["created_at"] = timestamp
            if "message_type" not in message_data:
                message_data["message_type"] = "general"
            message_data["record_type"] = "message"
            message_data["priority"] = priority.value
            message_data["ttl_expiry_date"] = self._calculate_ttl_expiry_date(
                timestamp, priority
            )

            self._run_async(audit_collection.insert_one(message_data))
            logger.debug("Message logged to audit_logs")
            return True

        except Exception as e:
            logger.error("Failed to log message data: %s", e)
            return False

    def _log_audit_data_direct(
        self,
        audit_data: Dict[str, Any],
        destination: str,
        timestamp: datetime,
        priority: LogPriority = LogPriority.MEDIUM,
    ) -> bool:
        """Log audit data directly to audit_logs collection"""
        try:
            if destination != "mongo":
                logger.error("Only MongoDB destination supported, got: %s", destination)
                return False

            audit_collection = self.db[self.audit_collection_name]
            if "timestamp" not in audit_data:
                audit_data["timestamp"] = timestamp
            audit_data["created_at"] = timestamp
            audit_data["priority"] = priority.value
            audit_data["ttl_expiry_date"] = self._calculate_ttl_expiry_date(
                timestamp, priority
            )

            self._run_async(audit_collection.insert_one(audit_data))
            logger.debug("Audit data logged to audit_logs")
            return True

        except Exception as e:
            logger.error("Failed to log audit data: %s", e)
            return False

    def _calculate_ttl_expiry_date(
        self, timestamp: datetime, priority: LogPriority
    ) -> Optional[datetime]:
        """Calculate TTL expiry date based on priority"""
        ttl_days = priority.ttl_days
        if ttl_days is None:
            return None
        return timestamp + timedelta(days=ttl_days)

    def _handle_job_retry(self, job_id: str, error_message: str):
        """Handle job retry logic"""
        try:
            current_job = self.get_job_status(job_id)
            current_retry_count = current_job.get("retry_count", 0) if current_job else 0
            new_retry_count = current_retry_count + 1

            if new_retry_count >= config.MAX_RETRY_COUNT:
                self.update_job_status(
                    job_id,
                    JobStatus.FAILED,
                    error_message=error_message,
                    retry_count=new_retry_count,
                )
                self.metrics["jobs_failed"] += 1
                logger.error(
                    "Job %s failed after %s retries: %s",
                    job_id,
                    config.MAX_RETRY_COUNT,
                    error_message,
                )
            else:
                self.update_job_status(
                    job_id,
                    JobStatus.SAVED,
                    error_message=error_message,
                    retry_count=new_retry_count,
                )
                job_doc = self._run_async(
                    self.jobs_collection.find_one({"job_id": job_id})
                )
                if job_doc:
                    service_name = job_doc.get("source_service")
                    if service_name and service_name in self.service_queues:
                        threading.Timer(
                            config.RETRY_DELAY_SECONDS,
                            lambda: self.service_queues[service_name].put(job_id),
                        ).start()
                self.metrics["jobs_retried"] += 1
                logger.warning(
                    "Job %s retry %s/%s scheduled",
                    job_id,
                    new_retry_count,
                    config.MAX_RETRY_COUNT,
                )

        except Exception as e:
            logger.error("Error handling retry for job %s: %s", job_id, e)

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job status from MongoDB"""
        if self.jobs_collection is None:
            return None
        try:
            return self._run_async(self.jobs_collection.find_one({"job_id": job_id}))
        except Exception as e:
            logger.error("Failed to get job status for %s: %s", job_id, e)
            return None

    def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        error_message: Optional[str] = None,
        retry_count: Optional[int] = None,
    ) -> bool:
        """Update job status in MongoDB"""
        if self.jobs_collection is None:
            return False
        try:
            update_data = {"status": status.value, "updated_at": datetime.now(timezone.utc)}
            if error_message:
                update_data["error_message"] = error_message
            if retry_count is not None:
                update_data["retry_count"] = retry_count

            result = self._run_async(
                self.jobs_collection.update_one(
                    {"job_id": job_id}, {"$set": update_data}
                )
            )
            return result.modified_count > 0

        except Exception as e:
            logger.error("Failed to update job status for %s: %s", job_id, e)
            return False

    def get_pending_jobs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get pending jobs that need to be processed"""
        if self.jobs_collection is None:
            return []
        try:
            cursor = self.jobs_collection.find(
                {"status": {"$in": [JobStatus.SAVED, JobStatus.PROCESSING]}},
            ).sort("created_at", 1).limit(limit)
            return self._run_async(cursor.to_list(length=10000))
        except Exception as e:
            logger.error("Failed to get pending jobs: %s", e)
            return []

    def restart_failed_jobs(self) -> int:
        """Restart jobs that were processing when system crashed"""
        if self.jobs_collection is None:
            return 0
        try:
            result = self._run_async(
                self.jobs_collection.update_many(
                    {"status": JobStatus.PROCESSING},
                    {
                        "$set": {
                            "status": JobStatus.SAVED,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    },
                )
            )
            logger.info("Restarted %s interrupted jobs", result.modified_count)
            return result.modified_count
        except Exception as e:
            logger.error("Failed to restart failed jobs: %s", e)
            return 0

    def requeue_pending_jobs(self) -> int:
        """Requeue all pending jobs after system restart"""
        pending_jobs = self.get_pending_jobs()
        requeued_count = 0

        for job in pending_jobs:
            try:
                service_name = job.get("source_service")
                if service_name and service_name in self.service_queues:
                    self.service_queues[service_name].put(job["job_id"])
                requeued_count += 1
            except Exception as e:
                logger.error("Error requeuing job %s: %s", job["job_id"], e)

        logger.info("Requeued %s pending jobs", requeued_count)
        return requeued_count

    def cleanup_old_completed_jobs(self, days: int = 30) -> int:
        """Clean up old completed jobs"""
        if self.jobs_collection is None:
            return 0
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            result = self._run_async(
                self.jobs_collection.delete_many(
                    {
                        "status": JobStatus.COMPLETED,
                        "updated_at": {"$lt": cutoff_date},
                    }
                )
            )
            logger.info("Cleaned up %s old completed jobs", result.deleted_count)
            return result.deleted_count
        except Exception as e:
            logger.error("Failed to cleanup old completed jobs: %s", e)
            return 0

    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        try:
            stats = {}

            if self.jobs_collection is not None:
                for status in JobStatus:
                    count = self._run_async(
                        self.jobs_collection.count_documents({"status": status.value})
                    )
                    stats[status.value] = count
            else:
                for status in JobStatus:
                    stats[status.value] = 0

            stats["queue_size"] = sum(q.qsize() for q in self.service_queues.values())
            stats["worker_count"] = sum(
                len(workers) for workers in self.service_workers.values()
            )
            stats["workers_alive"] = sum(
                sum(1 for thread in workers if thread.is_alive())
                for workers in self.service_workers.values()
            )
            stats["metrics"] = self.metrics.copy()
            return stats

        except Exception as e:
            logger.error("Failed to get queue stats: %s", e)
            return {"error": str(e)}

    def shutdown(self):
        """Gracefully shutdown the queue"""
        logger.info("Shutting down audit queue...")

        self.shutdown_event.set()

        for service_name, service_queue in self.service_queues.items():
            for _ in range(len(self.service_workers[service_name])):
                try:
                    service_queue.put(None, timeout=1.0)
                except queue.Full:
                    pass

        for service_name, workers in self.service_workers.items():
            for worker in workers:
                try:
                    if worker.is_alive():
                        worker.join(timeout=5.0)
                except Exception as e:
                    logger.warning("Service worker %s shutdown timeout: %s", service_name, e)

        if self.mongo_client:
            self.mongo_client.close()

        self._motor_loop.call_soon_threadsafe(self._motor_loop.stop)
        self._motor_loop_thread.join(timeout=5.0)

        logger.info("Audit queue shutdown complete")


def initialize_queue_after_restart() -> Dict[str, Any]:
    """Initialize queue after system restart - recover pending jobs"""
    try:
        return {
            "restarted": 0,
            "requeued": 0,
            "status": "success",
            "message": "Queue initialization completed",
        }
    except Exception as e:
        logger.error("Failed to initialize queue after restart: %s", e)
        return {"restarted": 0, "requeued": 0, "status": "error", "error": str(e)}
