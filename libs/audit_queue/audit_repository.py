"""
Async Audit Repository for Structured Logging using Motor
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from pymongo import ASCENDING, DESCENDING

from libs.audit_queue.audit_models import AuditLogDoc, LogType, Priority

logger = logging.getLogger(__name__)


class AuditRepository:
    """Async repository for managing structured audit logs with TTL support"""

    def __init__(
        self, mongodb_uri: str, audit_db_name: str, audit_collection_name: str
    ):
        """Initialize audit repository"""
        self.mongodb_uri = mongodb_uri
        self.audit_db_name = audit_db_name
        self.audit_collection_name = audit_collection_name
        self.mongo_client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None
        self.collection: Optional[AsyncIOMotorCollection] = None
        self._initialize_connection()

    def _initialize_connection(self):
        """Initialize async Motor connection using centralized factory"""
        try:
            from libs.database.mongo_client_factory import get_audit_queue_client
            self.mongo_client = get_audit_queue_client(self.mongodb_uri)
            self.db = self.mongo_client[self.audit_db_name]
            self.collection = self.db[self.audit_collection_name]
            logger.info("Audit repository async Motor connection established")
        except Exception as e:
            logger.error("Unexpected error initializing audit repository: %s", e)
            raise

    async def initialize_indexes(self):
        """Create optimized indexes for audit logs (call on app startup)"""
        try:
            if self.collection is None:
                return

            await self.collection.create_index([("ts", DESCENDING)], background=True)

            await self.collection.create_index(
                [("ttl_expiry_date", ASCENDING)],
                partialFilterExpression={"priority": "low"},
                expireAfterSeconds=0,
                name="ttl_low_priority",
                background=True,
            )
            await self.collection.create_index(
                [("ttl_expiry_date", ASCENDING)],
                partialFilterExpression={"priority": "medium"},
                expireAfterSeconds=0,
                name="ttl_medium_priority",
                background=True,
            )
            await self.collection.create_index(
                [("ttl_expiry_date", ASCENDING)],
                partialFilterExpression={"priority": "high"},
                expireAfterSeconds=0,
                name="ttl_high_priority",
                background=True,
            )

            await self.collection.create_index(
                [("meta.service", ASCENDING), ("ts", DESCENDING)]
            )
            await self.collection.create_index(
                [("meta.type", ASCENDING), ("ts", DESCENDING)]
            )
            await self.collection.create_index(
                [("meta.priority", ASCENDING), ("ts", DESCENDING)]
            )
            await self.collection.create_index(
                [("status", ASCENDING), ("ts", DESCENDING)]
            )
            await self.collection.create_index([("trace.traceId", ASCENDING)])
            await self.collection.create_index([("trace.jobId", ASCENDING)])
            await self.collection.create_index([("trace.requestId", ASCENDING)])
            await self.collection.create_index([("meta.correlationId", ASCENDING)])
            await self.collection.create_index(
                [("actor.id", ASCENDING), ("ts", DESCENDING)]
            )
            await self.collection.create_index([("actor.email", ASCENDING)])
            await self.collection.create_index(
                [("approval.flow", ASCENDING), ("ts", DESCENDING)]
            )
            await self.collection.create_index([("bid.instrumentId", ASCENDING)])
            await self.collection.create_index([("trade.tradeId", ASCENDING)])
            await self.collection.create_index(
                [
                    ("meta.service", ASCENDING),
                    ("meta.type", ASCENDING),
                    ("ts", DESCENDING),
                ]
            )
            await self.collection.create_index(
                [
                    ("meta.priority", ASCENDING),
                    ("status", ASCENDING),
                    ("ts", DESCENDING),
                ]
            )

            try:
                await self.collection.drop_index("user_id_text")
                logger.info("Dropped conflicting text index 'user_id_text'")
            except Exception:
                pass

            await self.collection.create_index(
                [("message", "text")], name="message_text"
            )

            logger.info("Audit repository indexes created successfully")

        except Exception as e:
            logger.warning("Could not create audit repository indexes: %s", e)

    async def save_audit_log(self, audit_log: AuditLogDoc) -> str:
        """Save an audit log document"""
        try:
            result = await self.collection.insert_one(audit_log.model_dump())
            logger.debug("Audit log saved with ID: %s", result.inserted_id)
            return str(result.inserted_id)
        except Exception as e:
            logger.error("Failed to save audit log: %s", e)
            raise

    async def save_audit_logs_batch(self, audit_logs: List[AuditLogDoc]) -> List[str]:
        """Save multiple audit log documents in batch"""
        try:
            documents = [log.model_dump() for log in audit_logs]
            result = await self.collection.insert_many(documents)
            logger.debug("Batch saved %d audit logs", len(result.inserted_ids))
            return [str(id) for id in result.inserted_ids]
        except Exception as e:
            logger.error("Failed to save audit logs batch: %s", e)
            raise

    async def find_by_service(
        self,
        service: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Find audit logs by service with time range"""
        try:
            query = {"meta.service": service}
            if start_time or end_time:
                time_filter = {}
                if start_time:
                    time_filter["$gte"] = start_time
                if end_time:
                    time_filter["$lte"] = end_time
                query["ts"] = time_filter

            cursor = self.collection.find(query).sort("ts", DESCENDING).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error("Failed to find audit logs by service: %s", e)
            raise

    async def find_by_trace_id(self, trace_id: str) -> List[Dict[str, Any]]:
        """Find all audit logs for a trace ID"""
        try:
            cursor = self.collection.find({"trace.traceId": trace_id}).sort("ts", ASCENDING)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error("Failed to find audit logs by trace ID: %s", e)
            raise

    async def find_by_job_id(self, job_id: str) -> List[Dict[str, Any]]:
        """Find all audit logs for a job ID"""
        try:
            cursor = self.collection.find({"trace.jobId": job_id}).sort("ts", ASCENDING)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error("Failed to find audit logs by job ID: %s", e)
            raise

    async def find_by_actor(self, actor_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Find audit logs by actor ID"""
        try:
            cursor = (
                self.collection.find({"actor.id": actor_id})
                .sort("ts", DESCENDING)
                .limit(limit)
            )
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error("Failed to find audit logs by actor: %s", e)
            raise

    async def find_by_priority(
        self,
        priority: Priority,
        start_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Find audit logs by priority"""
        try:
            query = {"meta.priority": priority}
            if start_time:
                query["ts"] = {"$gte": start_time}
            cursor = self.collection.find(query).sort("ts", DESCENDING).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error("Failed to find audit logs by priority: %s", e)
            raise

    async def find_errors(
        self,
        service: Optional[str] = None,
        start_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Find error audit logs"""
        try:
            query = {"error": {"$exists": True}}
            if service:
                query["meta.service"] = service
            if start_time:
                query["ts"] = {"$gte": start_time}
            cursor = self.collection.find(query).sort("ts", DESCENDING).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error("Failed to find error audit logs: %s", e)
            raise

    async def find_approvals(
        self,
        flow: Optional[str] = None,
        action: Optional[str] = None,
        start_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Find approval audit logs"""
        try:
            query = {"approval": {"$exists": True}}
            if flow:
                query["approval.flow"] = flow
            if action:
                query["approval.action"] = action
            if start_time:
                query["ts"] = {"$gte": start_time}
            cursor = self.collection.find(query).sort("ts", DESCENDING).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error("Failed to find approval audit logs: %s", e)
            raise

    async def search_logs(
        self,
        search_text: str,
        service: Optional[str] = None,
        log_type: Optional[LogType] = None,
        start_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Full-text search in audit logs"""
        try:
            query = {"$text": {"$search": search_text}}
            if service:
                query["meta.service"] = service
            if log_type:
                query["meta.type"] = log_type
            if start_time:
                query["ts"] = {"$gte": start_time}

            cursor = (
                self.collection.find(query, {"score": {"$meta": "textScore"}})
                .sort(
                    [("score", {"$meta": "textScore"}), ("ts", DESCENDING)]
                )
                .limit(limit)
            )
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error("Failed to search audit logs: %s", e)
            raise

    async def get_log_statistics(
        self,
        service: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get audit log statistics"""
        try:
            match_stage = {}
            if service:
                match_stage["meta.service"] = service
            if start_time or end_time:
                time_filter = {}
                if start_time:
                    time_filter["$gte"] = start_time
                if end_time:
                    time_filter["$lte"] = end_time
                match_stage["ts"] = time_filter

            pipeline = [
                {"$match": match_stage},
                {
                    "$group": {
                        "_id": None,
                        "total_logs": {"$sum": 1},
                        "error_count": {
                            "$sum": {"$cond": [{"$ne": ["$error", None]}, 1, 0]}
                        },
                        "avg_duration": {"$avg": "$durationMs"},
                    }
                },
            ]

            cursor = self.collection.aggregate(pipeline)
            result = await cursor.to_list(length=10000)
            return result[0] if result else {}

        except Exception as e:
            logger.error("Failed to get audit log statistics: %s", e)
            raise

    async def cleanup_old_logs(self, older_than_days: int = 365) -> int:
        """Manually cleanup logs older than specified days (fallback for TTL)"""
        try:
            cutoff_date = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=older_than_days)

            result = await self.collection.delete_many(
                {
                    "ts": {"$lt": cutoff_date},
                    "meta.priority": {"$ne": "Critical"},
                }
            )
            logger.info("Cleaned up %d old audit logs", result.deleted_count)
            return result.deleted_count

        except Exception as e:
            logger.error("Failed to cleanup old audit logs: %s", e)
            raise

    def close(self):
        """Close MongoDB connection"""
        if self.mongo_client:
            self.mongo_client.close()
            logger.info("Audit repository connection closed")
