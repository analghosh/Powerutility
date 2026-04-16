"""
Audit Model for storing audit logs in database
"""

from datetime import datetime
from typing import Optional, Dict, Any
from bson import ObjectId
from ..core.config import settings
import logging

logger = logging.getLogger(__name__)


class AuditLog:
    """Audit log model for database storage"""

    def __init__(self):
        # Use the singleton database connection instead of creating new ones
        from ..core.database import get_audit_database
        self.db = get_audit_database()
        self.collection = self.db[settings.MONGODB_AUDIT_COLLECTION]
        # Index creation is deferred to async initialize()

    async def initialize(self):
        """Initialize async resources — call once at startup from async context."""
        await self._ensure_indexes()

    async def _ensure_indexes(self):
        """Ensure database indexes for optimal performance"""
        try:
            # Index for timestamp-based queries (most common)
            await self.collection.create_index([("timestamp", -1)])

            # Index for user audit lookups
            await self.collection.create_index("user_id")

            # Index for event type filtering
            await self.collection.create_index("event_type")

            # Index for service filtering
            await self.collection.create_index("service")

            # Compound index for user + timestamp queries (very common)
            await self.collection.create_index([
                ("user_id", 1),
                ("timestamp", -1)
            ])

            # Compound index for event type + timestamp
            await self.collection.create_index([
                ("event_type", 1),
                ("timestamp", -1)
            ])

            # Index for success/failure analysis
            await self.collection.create_index("success")

            logger.debug("Audit collection indexes ensured")

        except Exception as e:
            logger.warning(f"Failed to create audit indexes: {e}")

    async def log_event(self,
                  event_type: str,
                  user_id: Optional[str] = None,
                  username: Optional[str] = None,
                  ip_address: Optional[str] = None,
                  user_agent: Optional[str] = None,
                  details: Optional[Dict[str, Any]] = None,
                  success: bool = True,
                  error_message: Optional[str] = None) -> bool:
        """
        Log an audit event to database

        Args:
            event_type: Type of event (login, logout, password_change, etc.)
            user_id: ID of the user performing the action (if authenticated)
            username: Username of the user (if authenticated)
            ip_address: IP address of the request
            user_agent: User agent string
            details: Additional event details
            success: Whether the action was successful
            error_message: Error message if action failed

        Returns:
            bool: True if logged successfully, False otherwise
        """
        try:
            audit_entry = {
                "timestamp": datetime.now(),
                "event_type": event_type,
                "user_id": user_id,
                "username": username,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "success": success,
                "error_message": error_message,
                "service": "auth",
                "details": details or {}
            }

            # Remove None values
            audit_entry = {k: v for k, v in audit_entry.items() if v is not None}

            result = await self.collection.insert_one(audit_entry)
            return result.inserted_id is not None

        except Exception as e:
            # Fallback to console logging if database fails
            print(f"Audit log failed: {e}")
            return False

    async def get_user_audit_logs(self, user_id: str, limit: int = 100) -> list:
        """Get audit logs for a specific user"""
        try:
            return await self.collection.find(
                {"user_id": user_id},
                {"_id": 0}  # Exclude _id field
            ).sort("timestamp", -1).limit(limit).to_list(limit)
        except Exception:
            return []

    async def get_audit_logs(self,
                       event_type: Optional[str] = None,
                       start_date: Optional[datetime] = None,
                       end_date: Optional[datetime] = None,
                       limit: int = 100) -> list:
        """Get audit logs with filters"""
        try:
            query = {}

            if event_type:
                query["event_type"] = event_type

            if start_date or end_date:
                query["timestamp"] = {}
                if start_date:
                    query["timestamp"]["$gte"] = start_date
                if end_date:
                    query["timestamp"]["$lte"] = end_date

            return await self.collection.find(
                query,
                {"_id": 0}  # Exclude _id field
            ).sort("timestamp", -1).limit(limit).to_list(limit)
        except Exception:
            return []

    async def get_failed_attempts(self, user_id: str, hours: int = 24) -> list:
        """Get failed login attempts for a user in the last N hours"""
        try:
            from datetime import timedelta
            cutoff_time = datetime.now() - timedelta(hours=hours)

            return await self.collection.find({
                "user_id": user_id,
                "event_type": "login",
                "success": False,
                "timestamp": {"$gte": cutoff_time}
            }).sort("timestamp", -1).to_list(10000)
        except Exception:
            return []

    def close(self):
        """Close database connection"""
        pass  # Connection managed by MongoClientFactory singleton


# Create global audit log instance
audit_log = AuditLog()
