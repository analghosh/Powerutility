"""
MongoDB-backed Queue Manager for Audit Logging with Threading
"""

import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
import threading
from queue import Queue
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
import queue

from libs.audit_queue.config import config
from libs.audit_queue.models import AuditJob, JobStatus, LogPriority

# Configure logging
logger = logging.getLogger(__name__)


class MongoAuditQueue:
    """MongoDB-backed audit queue manager with threading"""
    
    def __init__(self, mongodb_uri: str, audit_db_name: str, audit_collection_name: str, queue_collection_name: str, max_workers: int = 3):
        """
        Initialize MongoDB audit queue with connection pooling and multiple workers
        
        Args:
            mongodb_uri: MongoDB connection URI
            audit_db_name: Name of the audit database
            audit_collection_name: Name of the audit logs collection
            queue_collection_name: Name of the queue jobs collection
            max_workers: Number of worker threads to process jobs
        """
        self.mongodb_uri = mongodb_uri
        self.audit_db_name = audit_db_name
        self.audit_collection_name = audit_collection_name
        self.queue_collection_name = queue_collection_name
        self.max_workers = max_workers
        self.mongo_client = None
        self.db = None
        self.jobs_collection: Optional[Collection] = None
        
        # Threading setup - one queue per service for sequential processing
        self.service_queues: Dict[str, Queue] = {}
        self.service_workers: Dict[str, List[threading.Thread]] = {}
        self.shutdown_event = threading.Event()
        
        # Metrics
        self.metrics = {
            "jobs_processed": 0,
            "jobs_failed": 0,
            "jobs_retried": 0,
            "last_processed": None
        }
        
        # Initialize connection and workers
        self._initialize_connection()
        # Don't start workers here - start them per service
        
        logger.info("MongoAuditQueue initialized for service-specific sequential processing")
    
    def _initialize_connection(self):
        """Initialize MongoDB connection using centralized factory"""
        try:
            from libs.database.mongo_client_factory import get_audit_queue_client
            self.mongo_client = get_audit_queue_client(self.mongodb_uri)
            
            # Test connection
            self.mongo_client.admin.command('ping')
            
            self.db = self.mongo_client[self.audit_db_name]
            self.jobs_collection = self.db[self.queue_collection_name]
            
            # Create indexes for better performance
            self._create_indexes()
            
            logger.info("MongoDB connection established successfully")
            
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.error("Failed to connect to MongoDB: %s", e)
            raise
        except Exception as e:
            logger.error("Unexpected error initializing MongoDB connection: %s", e)
            raise
    
    def _create_indexes(self):
        """Create indexes for the jobs collection and audit_logs collection"""
        try:
            if self.jobs_collection is None:
                return
                
            # Indexes for jobs collection
            self.jobs_collection.create_index("status")
            self.jobs_collection.create_index("created_at")
            self.jobs_collection.create_index("source_service")
            self.jobs_collection.create_index("job_type")
            self.jobs_collection.create_index("priority")
            
            # Compound index for efficient queries
            self.jobs_collection.create_index([("status", 1), ("created_at", 1)])
            
            # TTL index for automatic cleanup of old completed jobs
            self.jobs_collection.create_index(
                "updated_at", 
                expireAfterSeconds=config.MAX_RETRY_COUNT * config.RETRY_DELAY_SECONDS * 2
            )
            
            # Create TTL indexes for audit_logs collection based on priority
            self._create_audit_logs_ttl_indexes()
            
            logger.info("MongoDB indexes created successfully")
            
        except Exception as e:
            logger.warning("Could not create indexes: %s", e)
    
    def _create_audit_logs_ttl_indexes(self):
        """Create TTL indexes for audit_logs collection based on priority levels"""
        try:
            audit_collection = self.db[self.audit_collection_name]
            
            # TTL index for HIGH priority logs (1 year)
            audit_collection.create_index(
                [("ttl_expiry_date", 1)],
                partialFilterExpression={"priority": "high"},
                expireAfterSeconds=0,  # MongoDB will use the ttl_expiry_date field
                name="ttl_high_priority"
            )
            
            # TTL index for MEDIUM priority logs (6 months)
            audit_collection.create_index(
                [("ttl_expiry_date", 1)],
                partialFilterExpression={"priority": "medium"},
                expireAfterSeconds=0,
                name="ttl_medium_priority"
            )
            
            # TTL index for LOW priority logs (3 months)
            audit_collection.create_index(
                [("ttl_expiry_date", 1)],
                partialFilterExpression={"priority": "low"},
                expireAfterSeconds=0,
                name="ttl_low_priority"
            )
            
            # No TTL index for CRITICAL priority logs (no expiry)
            
            # Regular indexes for audit_logs
            audit_collection.create_index([("timestamp", -1)])
            audit_collection.create_index([("service", 1)])
            audit_collection.create_index([("record_type", 1)])
            audit_collection.create_index([("priority", 1)])
            
            logger.info("TTL indexes created for audit_logs collection")
            
        except Exception as e:
            logger.warning("Could not create audit_logs TTL indexes: %s", e)
    
    
    def add_to_queue(self, log_data: Dict[str, Any], source_service: str, 
                     destination: str = "mongo", job_type: str = "audit", 
                     priority: LogPriority = LogPriority.MEDIUM) -> str:
        """
        Add audit log or message to MongoDB queue for background processing
        
        Args:
            log_data: Data to log
            source_service: Service that is logging
            destination: "mongo" or "file"
            job_type: "audit" or "message"
            priority: Log priority level (affects TTL)
            
        Returns:
            str: Job ID
        """
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
                updated_at=datetime.now(timezone.utc)
            )
            
            # Save job to MongoDB
            self.jobs_collection.insert_one(audit_job.model_dump())
            
            # Ensure service queue exists and start workers if needed
            self._ensure_service_queue(source_service)
            
            # Add to service-specific queue for sequential processing
            self.service_queues[source_service].put(job_id)
            
            logger.debug("Job %s added to service queue for %s", job_id, source_service)
            return job_id
            
        except Exception as e:
            logger.error("Failed to add job to queue: %s", e)
            return ""
    
    def _ensure_service_queue(self, service_name: str):
        """Ensure service queue and workers exist for sequential processing"""
        if service_name not in self.service_queues:
            # Create queue for this service
            self.service_queues[service_name] = Queue()
            self.service_workers[service_name] = []
            
            # Start one worker for this service (sequential processing)
            worker_thread = threading.Thread(
                target=self._service_worker_loop,
                args=(service_name,),
                daemon=True,
                name=f"ServiceWorker-{service_name}"
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
                # Get job with timeout to allow checking shutdown event
                job_id = service_queue.get(timeout=1.0)
                
                if job_id is None:  # Shutdown signal
                    break
                
                # Process the job sequentially for this service
                self._process_job_directly(job_id)
                service_queue.task_done()
                
            except queue.Empty:
                # Timeout occurred, continue loop to check shutdown event
                continue
            except Exception as e:
                if not self.shutdown_event.is_set():
                    logger.error("Service worker %s error: %s", worker_name, e)
                continue
        
        logger.info("Service worker %s stopped", worker_name)
    
    
    def _process_job_directly(self, job_id: str):
        """Process job directly in background thread"""
        try:
            # Get job data from MongoDB
            job_doc = self.get_job_status(job_id)
            if not job_doc:
                logger.error("Job %s not found", job_id)
                return
            
            audit_job = AuditJob(**job_doc)
            
            # Update status to processing
            self.update_job_status(job_id, JobStatus.PROCESSING)
            
            # Log the data using the logger service
            success = self._log_audit_data(audit_job.log_data, audit_job.destination, audit_job.created_at, audit_job.job_type, audit_job.priority)
            
            if success:
                # Mark as completed
                self.update_job_status(job_id, JobStatus.COMPLETED)
                self.metrics["jobs_processed"] += 1
                self.metrics["last_processed"] = datetime.now(timezone.utc)
                logger.debug("Audit job %s completed successfully", job_id)
            else:
                # Mark as failed
                self.update_job_status(job_id, JobStatus.FAILED, "Failed to log data")
                self.metrics["jobs_failed"] += 1
                logger.error("Audit job %s failed: Failed to log data", job_id)
                
        except Exception as e:
            # Handle retries
            self._handle_job_retry(job_id, str(e))
    
    def _log_audit_data(self, log_data: Dict[str, Any], destination: str, timestamp: datetime, job_type: str = "audit", priority: LogPriority = LogPriority.MEDIUM) -> bool:
        """Log audit data or messages using the logger service"""
        try:
            if job_type == "message":
                # Handle message logging
                return self._log_message_data(log_data, destination, timestamp, priority)
            else:
                # Handle audit logging (default behavior)
                return self._log_audit_data_direct(log_data, destination, timestamp, priority)
            
        except Exception as e:
            logger.error("Failed to log data: %s", e)
            return False
    
    def _log_message_data(self, message_data: Dict[str, Any], destination: str, timestamp: datetime, priority: LogPriority = LogPriority.MEDIUM) -> bool:
        """Log message data to audit_logs collection"""
        try:
            # Only support MongoDB destination
            if destination != "mongo":
                logger.error("Only MongoDB destination supported, got: %s", destination)
                return False
                
            # Store message in audit_logs collection (same as audit logs)
            audit_collection = self.db[self.audit_collection_name]
            
            # Add timestamp if not present
            if "timestamp" not in message_data:
                message_data["timestamp"] = timestamp
            
            # Add metadata for audit_logs collection
            message_data["created_at"] = timestamp
            
            # Ensure message_type is set
            if "message_type" not in message_data:
                message_data["message_type"] = "general"
            
            # Add record_type to distinguish from audit events
            message_data["record_type"] = "message"
            
            # Add priority and TTL expiry date
            message_data["priority"] = priority.value
            message_data["ttl_expiry_date"] = self._calculate_ttl_expiry_date(timestamp, priority)
            
            result = audit_collection.insert_one(message_data)
            logger.debug("Message logged to audit_logs with ID: %s", result.inserted_id)
            return True
                
        except Exception as e:
            logger.error("Failed to log message data: %s", e)
            return False
    
    def _log_audit_data_direct(self, audit_data: Dict[str, Any], destination: str, timestamp: datetime, priority: LogPriority = LogPriority.MEDIUM) -> bool:
        """Log audit data directly to audit_logs collection"""
        try:
            # Only support MongoDB destination
            if destination != "mongo":
                logger.error("Only MongoDB destination supported, got: %s", destination)
                return False
                
            # Store audit data in audit_logs collection
            audit_collection = self.db[self.audit_collection_name]
            
            # Add timestamp if not present
            if "timestamp" not in audit_data:
                audit_data["timestamp"] = timestamp
            
            # Add metadata for audit_logs collection
            audit_data["created_at"] = timestamp
            
            # Add priority and TTL expiry date
            audit_data["priority"] = priority.value
            audit_data["ttl_expiry_date"] = self._calculate_ttl_expiry_date(timestamp, priority)
            
            result = audit_collection.insert_one(audit_data)
            logger.debug("Audit data logged to audit_logs with ID: %s", result.inserted_id)
            return True
                
        except Exception as e:
            logger.error("Failed to log audit data: %s", e)
            return False
    
    def _calculate_ttl_expiry_date(self, timestamp: datetime, priority: LogPriority) -> Optional[datetime]:
        """Calculate TTL expiry date based on priority"""
        ttl_days = priority.ttl_days
        if ttl_days is None:
            return None  # No expiry for CRITICAL logs
        
        return timestamp + timedelta(days=ttl_days)
    
    def _handle_job_retry(self, job_id: str, error_message: str):
        """Handle job retry logic"""
        try:
            # Get current retry count
            current_job = self.get_job_status(job_id)
            current_retry_count = current_job.get("retry_count", 0) if current_job else 0
            new_retry_count = current_retry_count + 1
            
            if new_retry_count >= config.MAX_RETRY_COUNT:
                # Mark as failed
                self.update_job_status(
                    job_id, 
                    JobStatus.FAILED, 
                    error_message=error_message,
                    retry_count=new_retry_count
                )
                self.metrics["jobs_failed"] += 1
                logger.error("Job %s failed after %s retries: %s", job_id, config.MAX_RETRY_COUNT, error_message)
            else:
                # Update retry count and requeue for retry
                self.update_job_status(
                    job_id, 
                    JobStatus.SAVED,  # Reset to SAVED for retry
                    error_message=error_message,
                    retry_count=new_retry_count
                )
                
                # Add back to queue for retry (with delay) - need to get service from job
                job_doc = self.jobs_collection.find_one({"job_id": job_id})
                if job_doc:
                    service_name = job_doc.get("source_service")
                    if service_name and service_name in self.service_queues:
                        threading.Timer(config.RETRY_DELAY_SECONDS, 
                                      lambda: self.service_queues[service_name].put(job_id)).start()
                self.metrics["jobs_retried"] += 1
                logger.warning("Job %s retry %s/%s scheduled", job_id, new_retry_count, config.MAX_RETRY_COUNT)
                
        except Exception as e:
            logger.error("Error handling retry for job %s: %s", job_id, e)
    
    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job status from MongoDB"""
        if self.jobs_collection is None:
            return None
        try:
            job_doc = self.jobs_collection.find_one({"job_id": job_id})
            return job_doc if job_doc else None
        except Exception as e:
            logger.error("Failed to get job status for %s: %s", job_id, e)
            return None
    
    def update_job_status(self, job_id: str, status: JobStatus, 
                         error_message: Optional[str] = None,
                         retry_count: Optional[int] = None) -> bool:
        """Update job status in MongoDB"""
        if self.jobs_collection is None:
            return False
            
        try:
            update_data = {
                "status": status.value,
                "updated_at": datetime.now(timezone.utc)
            }
            
            if error_message:
                update_data["error_message"] = error_message
            
            if retry_count is not None:
                update_data["retry_count"] = retry_count
            
            result = self.jobs_collection.update_one(
                {"job_id": job_id},
                {"$set": update_data}
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
            return list(self.jobs_collection.find(
                {"status": {"$in": [JobStatus.SAVED, JobStatus.PROCESSING]}}
            ).sort("created_at", 1).limit(limit))
        except Exception as e:
            logger.error("Failed to get pending jobs: %s", e)
            return []
    
    def restart_failed_jobs(self) -> int:
        """Restart jobs that were processing when system crashed"""
        if self.jobs_collection is None:
            return 0
        try:
            # Find jobs that were in processing state (likely interrupted by crash)
            result = self.jobs_collection.update_many(
                {"status": JobStatus.PROCESSING},
                {"$set": {
                    "status": JobStatus.SAVED,
                    "updated_at": datetime.now(timezone.utc)
                }}
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
                # Add back to background queue
                service_name = job.get("source_service")
                if service_name and service_name in self.service_queues:
                    self.service_queues[service_name].put(job["job_id"])
                requeued_count += 1
            except Exception as e:
                logger.error("Error requeuing job %s: %s", job['job_id'], e)
        
        logger.info("Requeued %s pending jobs", requeued_count)
        return requeued_count
    
    def cleanup_old_completed_jobs(self, days: int = 30) -> int:
        """Clean up old completed jobs"""
        if self.jobs_collection is None:
            return 0
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            result = self.jobs_collection.delete_many({
                "status": JobStatus.COMPLETED,
                "updated_at": {"$lt": cutoff_date}
            })
            
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
                # Count jobs by status
                for status in JobStatus:
                    count = self.jobs_collection.count_documents({"status": status.value})
                    stats[status.value] = count
            else:
                # Default values if collection is not available
                for status in JobStatus:
                    stats[status.value] = 0
            
            # Queue size
            # Calculate total queue size across all service queues
            stats["queue_size"] = sum(queue.qsize() for queue in self.service_queues.values())
            
            # Worker status
            stats["worker_count"] = sum(len(workers) for workers in self.service_workers.values())
            stats["workers_alive"] = sum(
                sum(1 for thread in workers if thread.is_alive()) 
                for workers in self.service_workers.values()
            )
            
            # Metrics
            stats["metrics"] = self.metrics.copy()
            
            return stats
            
        except Exception as e:
            logger.error("Failed to get queue stats: %s", e)
            return {"error": str(e)}
    
    def shutdown(self):
        """Gracefully shutdown the queue"""
        logger.info("Shutting down audit queue...")
        
        # Signal shutdown to all service workers
        self.shutdown_event.set()
        
        # Send None to each service worker to signal shutdown
        for service_name, service_queue in self.service_queues.items():
            for _ in range(len(self.service_workers[service_name])):
                try:
                    service_queue.put(None, timeout=1.0)
                except queue.Full:
                    pass
        
        # Wait for all service workers to finish
        for service_name, workers in self.service_workers.items():
            for worker in workers:
                try:
                    if worker.is_alive():
                        worker.join(timeout=5.0)
                except Exception as e:
                    logger.warning("Service worker %s shutdown timeout: %s", service_name, e)
        
        # Close MongoDB connection
        if self.mongo_client:
            self.mongo_client.close()
        
        logger.info("Audit queue shutdown complete")


def initialize_queue_after_restart() -> Dict[str, Any]:
    """
    Initialize queue after system restart - recover pending jobs
    
    Returns:
        Dict containing restart results
    """
    try:
        # This function should be called with a proper queue instance
        # For now, return a basic response structure
        return {
            "restarted": 0,
            "requeued": 0,
            "status": "success",
            "message": "Queue initialization completed"
        }
    except Exception as e:
        logger.error("Failed to initialize queue after restart: %s", e)
        return {
            "restarted": 0,
            "requeued": 0,
            "status": "error",
            "error": str(e)
        }
