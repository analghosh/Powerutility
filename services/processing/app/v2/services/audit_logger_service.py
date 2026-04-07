"""
Audit Logger Service
Service layer for audit log data retrieval and user enrichment
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from bson import ObjectId

from ...core.database import get_audit_database, get_auth_database

logger = logging.getLogger(__name__)


class AuditLoggerService:
    """Service for audit log operations"""

    def __init__(self):
        self.audit_db = None
        self.auth_db = None

    def _get_audit_db(self):
        """Get audit database connection"""
        if self.audit_db is None:
            self.audit_db = get_audit_database()
        return self.audit_db

    def _get_auth_db(self):
        """Get auth database connection"""
        if self.auth_db is None:
            self.auth_db = get_auth_database()
        return self.auth_db

    async def fetch_audit_logs(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch audit logs from audit_db and enrich with user data from auth_db
        
        Args:
            filters: Dictionary containing filter parameters
                - type: str - Filter type ("authentication", "roles", or "data")
                - skip: int - Number of records to skip
                - limit: int - Maximum number of records to return
                
        Returns:
            Dictionary containing:
                - logs: List of audit log entries with enriched user data
                - total: Total count of matching records
        """
        try:
            audit_db = self._get_audit_db()
            auth_db = self._get_auth_db()
            
            audit_logs_collection = audit_db["audit_logs"]
            users_collection = auth_db["users"]
            
            filter_type = filters.get("type")

            # If type not provided, set default to authentication
            if not filter_type:
                filter_type = "authentication"
                filters["type"] = "authentication"

            # Build MongoDB query based on filters
            query = self._build_query(filters)
            
            logger.info(f"MongoDB query: {query}")
            
            # Get pagination parameters
            skip = filters.get("skip", 0)
            limit = filters.get("limit", 100)
            
            # Fetch audit logs with pagination
            cursor = audit_logs_collection.find(query).skip(skip).limit(limit).sort("timestamp", -1)
            try:
                audit_logs = list(cursor)
            finally:
                cursor.close()
            
            # Get total count for pagination
            total = audit_logs_collection.count_documents(query)
            
            logger.info(f"Found {len(audit_logs)} audit logs (total: {total})")
            
            # Enrich logs with user data
            enriched_logs = await self._enrich_with_user_data(audit_logs, users_collection)
            
            return {
                "logs": enriched_logs,
                "total": total
            }
            
        except Exception as e:
            logger.error(f"Error fetching audit logs: {e}")
            raise

    async def _fetch_latest_roles(
        self,
        audit_logs_collection,
        users_collection,
        filters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Fetch latest role assignment for each user using MongoDB aggregation
        
        Args:
            audit_logs_collection: MongoDB audit logs collection
            users_collection: MongoDB users collection
            filters: Dictionary containing filter parameters
            
        Returns:
            Dictionary containing:
                - logs: List of latest role assignments per user with enriched data
                - total: Total count of unique users with role assignments
        """
        try:
            skip = filters.get("skip", 0)
            limit = filters.get("limit", 100)
            
            # MongoDB aggregation pipeline to get latest role assignment per user
            pipeline = [
                # Match role-related events
                {
                    "$match": {
                        "service": "auth",
                        "data.action_type": {"$in": ["create_user", "role_update"]}
                    }
                },
                # Sort by timestamp descending to get latest first
                {
                    "$sort": {"timestamp": -1}
                },
                # Group by user_id and take the first (latest) document
                {
                    "$group": {
                        "_id": "$user_id",
                        "latest_log": {"$first": "$$ROOT"}
                    }
                },
                # Replace root with the latest log document
                {
                    "$replaceRoot": {"newRoot": "$latest_log"}
                },
                # Sort again by timestamp for consistent ordering
                {
                    "$sort": {"timestamp": -1}
                }
            ]
            
            # Get total count of unique users with role assignments
            count_pipeline = pipeline[:-1] + [{"$count": "total"}]
            count_result = list(audit_logs_collection.aggregate(count_pipeline))
            total = count_result[0]["total"] if count_result else 0
            
            # Add pagination to the main pipeline
            pipeline.extend([
                {"$skip": skip},
                {"$limit": limit}
            ])
            
            # Execute aggregation
            audit_logs = list(audit_logs_collection.aggregate(pipeline))
            
            logger.info(f"Found {len(audit_logs)} latest role assignments (total unique users: {total})")
            
            # Enrich logs with user data
            enriched_logs = await self._enrich_with_user_data(audit_logs, users_collection)
            
            return {
                "logs": enriched_logs,
                "total": total
            }
            
        except Exception as e:
            logger.error(f"Error fetching latest roles: {e}")
            raise

    def _build_query(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build MongoDB query based on filter parameters
        
        Args:
            filters: Dictionary containing filter parameters
            
        Returns:
            MongoDB query dictionary
        """
        filter_type = filters.get("type")
        
        # No filter type provided, return all logs
        if not filter_type:
            return {}
        
        # Authentication filter: service=auth AND data.action_type=login
        if filter_type == "authentication":
            return {
                "service": "auth",
                "data.action_type": {"$in":["login","logout"]},
                "status": "success"
            }
        
        # Roles filter: service=auth AND data.action_type IN [create_user, update, delete, assign_roles]
        elif filter_type == "roles":
            return {
                "service": "auth",
                "data.action_type": {"$in": ["create_user", "update", "delete","assign_roles"]}
            }
        
        # Data filter: (service=ingestion AND data.action_type=upload) OR 
        #              (service=processing AND data.action_type=download)
        elif filter_type == "data":
            return {
                        "service": {"$in": ["processing", "DAM", "RTM", "Limit Values"]},
                        "data.action_type": {"$in": ["approval", "approved", "rejected"]}
                    }
        
        # Unknown filter type, return all logs
        return {}

    async def _enrich_with_user_data(
        self, 
        audit_logs: List[Dict[str, Any]], 
        users_collection
    ) -> List[Dict[str, Any]]:
        """
        Enrich audit logs with username and email from auth_db.users
        
        Args:
            audit_logs: List of audit log entries
            users_collection: MongoDB users collection
            
        Returns:
            List of enriched audit log entries
        """
        try:
            # Extract unique user IDs from audit logs
            user_ids = set()
            for log in audit_logs:
                # Check for user_id, prioritizing data.user_id as requested
                user_id = log.get("data", {}).get("user_id") or log.get("user_id")
                if user_id:
                    user_ids.add(user_id)
            
            logger.info(f"Enriching logs for {len(user_ids)} unique users")
            
            # Fetch user data in bulk
            user_data_map = {}
            if user_ids:
                # Convert string IDs to ObjectId if needed
                object_ids = []
                for uid in user_ids:
                    try:
                        if isinstance(uid, str) and len(uid) == 24:
                            object_ids.append(ObjectId(uid))
                        else:
                            object_ids.append(uid)
                    except Exception:
                        object_ids.append(uid)
                
                cursor = users_collection.find(
                    {"_id": {"$in": object_ids}},
                    {"_id": 1, "username": 1, "email": 1, "profile": 1}
                )
                
                try:
                    users = list(cursor)
                finally:
                    cursor.close()
                
                for user in users:
                    user_id_str = str(user["_id"])
                    
                    # Construct full name from profile
                    profile = user.get("profile", {})
                    first_name = profile.get("first_name", "")
                    last_name = profile.get("last_name", "")
                    name = f"{first_name} {last_name}".strip()
                    
                    user_data_map[user_id_str] = {
                        "username": user.get("username"),
                        "email": user.get("email"),
                        "name": name if name else user.get("username")
                    }
            
            # Enrich audit logs with user data
            enriched_logs = []
            for log in audit_logs:
                # Convert ObjectId to string for JSON serialization
                if "_id" in log:
                    log["_id"] = str(log["_id"])
                
                # Get user_id from log (prioritizing data.user_id)
                user_id = log.get("data", {}).get("user_id") or log.get("user_id")
                
                if user_id:
                    user_id_str = str(user_id)
                    user_data = user_data_map.get(user_id_str, {})
                    
                    # Add username, email and name to log
                    log["username"] = user_data.get("username")
                    log["email"] = user_data.get("email")
                    log["name"] = user_data.get("name")
                else:
                    log["username"] = None
                    log["email"] = None
                    log["name"] = None
                
                # Map fields from raw record to API schema
                data = log.get("data") or {}
                
                if not log.get("action_type"):
                    log["action_type"] = data.get("action_type")
                
                if "status" in log and "success" not in log:
                    log["success"] = log["status"] == "success"
                
                if not log.get("error_message"):
                    log["error_message"] = log.get("error")

                # Convert datetime objects to ISO format strings
                if "timestamp" in log and isinstance(log["timestamp"], datetime):
                    log["timestamp"] = log["timestamp"].isoformat()
                
                enriched_logs.append(log)
            
            return enriched_logs
            
        except Exception as e:
            logger.error(f"Error enriching audit logs with user data: {e}")
            # Return logs without enrichment if error occurs
            return audit_logs


# Create singleton instance
audit_logger_service = AuditLoggerService()
