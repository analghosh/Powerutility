"""
Audit repository for authentication service
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from bson import ObjectId
import logging
from .base_repository import BaseRepository
from ..core.config import settings

logger = logging.getLogger(__name__)


class AuditRepository(BaseRepository):
    """Audit repository for audit operations"""
    
    async def __init__(self):
        super().__init__(settings.MONGODB_AUDIT_COLLECTION, settings.AUDIT_DB_NAME)
    
    async def log_event(self, 
                  event_type: str, 
                  user_id: Optional[str] = None,
                  username: Optional[str] = None,
                  ip_address: Optional[str] = None,
                  user_agent: Optional[str] = None,
                  details: Optional[Dict[str, Any]] = None,
                  success: bool = True,
                  error_message: Optional[str] = None) -> str:
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
            str: ID of the created audit log entry
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
            
            return self.create(audit_entry)
            
        except Exception as e:
            logger.error(f"Error logging audit event: {e}")
            raise
    
    async def get_user_audit_logs(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get audit logs for a specific user"""
        try:
            return self.find_many({"user_id": user_id}, limit=limit)
        except Exception as e:
            logger.error(f"Error getting audit logs for user {user_id}: {e}")
            return []
    
    async def get_audit_logs(self, 
                       event_type: Optional[str] = None,
                       start_date: Optional[datetime] = None,
                       end_date: Optional[datetime] = None,
                       limit: int = 100) -> List[Dict[str, Any]]:
        """Get audit logs with filters"""
        try:
            query = {}
            
            if event_type:
                query["event_type"] = event_type
            
            if start_date or end_date:
                date_filter = {}
                if start_date:
                    date_filter["$gte"] = start_date
                if end_date:
                    date_filter["$lte"] = end_date
                query["timestamp"] = date_filter
            
            return self.find_many(query, limit=limit)
        except Exception as e:
            logger.error(f"Error getting audit logs: {e}")
            return []
    
    async def get_failed_attempts(self, user_id: str, hours: int = 24) -> List[Dict[str, Any]]:
        """Get failed login attempts for a user in the last N hours"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            return self.find_many({
                "user_id": user_id,
                "event_type": "login",
                "success": False,
                "timestamp": {"$gte": cutoff_time}
            })
        except Exception as e:
            logger.error(f"Error getting failed attempts for user {user_id}: {e}")
            return []
    
    async def get_audit_stats(self, days: int = 30) -> Dict[str, Any]:
        """Get audit statistics for the last N days"""
        try:
            cutoff_time = datetime.now() - timedelta(days=days)
            
            # Total events
            total_events = self.count({"timestamp": {"$gte": cutoff_time}})
            
            # Successful events
            successful_events = self.count({
                "timestamp": {"$gte": cutoff_time},
                "success": True
            })
            
            # Failed events
            failed_events = self.count({
                "timestamp": {"$gte": cutoff_time},
                "success": False
            })
            
            # Events by type
            pipeline = [
                {"$match": {"timestamp": {"$gte": cutoff_time}}},
                {"$group": {"_id": "$event_type", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}}
            ]
            events_by_type = await self.collection.aggregate(pipeline).to_list(length=10000)

            return {
                "total_events": total_events,
                "successful_events": successful_events,
                "failed_events": failed_events,
                "success_rate": (successful_events / total_events * 100) if total_events > 0 else 0,
                "events_by_type": events_by_type,
                "period_days": days,
                "timestamp": datetime.now()
            }
        except Exception as e:
            logger.error(f"Error getting audit stats: {e}")
            return {}
    
    def cleanup_old_audit_logs(self, days: int = 365) -> int:
        """Clean up audit logs older than N days"""
        try:
            cutoff_time = datetime.now() - timedelta(days=days)
            result = self.collection.delete_many({
                "timestamp": {"$lt": cutoff_time}
            })
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error cleaning up old audit logs: {e}")
            return 0 