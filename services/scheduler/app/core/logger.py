"""
Simple Logger Service for EPMS
Logs data to MongoDB or file based on parameter
"""

import logging
import json
import os
from datetime import datetime
from typing import Dict, Any, Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection


class LoggerService:
    """Simple logger service that can log to MongoDB or file"""
    
    def __init__(self):
        # MongoDB connection
        self.mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        self.audit_db_name = os.getenv("AUDIT_DB_NAME", "audit_db")
        self.audit_collection_name = os.getenv("AUDIT_COLLECTION_NAME", "audit_logs")
        
        # File logging setup
        self.log_file_path = os.getenv("LOG_FILE_PATH", "logs/audit.log")
        
        # Ensure logs directory exists
        os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
        
        # MongoDB client (lazy initialization)
        self._mongo_client: Optional[AsyncIOMotorClient] = None
        self._db: Optional[AsyncIOMotorDatabase] = None
        self._collection: Optional[AsyncIOMotorCollection] = None
    
    def _get_mongo_collection(self) -> AsyncIOMotorCollection:
        """Get MongoDB collection (lazy initialization)"""
        if self._collection is None:
            from .database import get_database_client
            self._mongo_client = get_database_client()
            self._db = self._mongo_client[self.audit_db_name]
            self._collection = self._db[self.audit_collection_name]
        return self._collection
    
    async def log_to_mongo(self, log_data: Dict[str, Any], timestamp: Optional[datetime] = None) -> bool:
        """Log data to MongoDB"""
        try:
            # Use provided timestamp or current time
            if timestamp:
                log_data["timestamp"] = timestamp
            elif "timestamp" not in log_data:
                log_data["timestamp"] = datetime.now()
            
            collection = self._get_mongo_collection()
            result = await collection.insert_one(log_data)
            return result.inserted_id is not None
            
        except Exception as e:
            print(f"Error logging to MongoDB: {e}")
            return False
    
    def log_to_file(self, log_data: Dict[str, Any], timestamp: Optional[datetime] = None) -> bool:
        """Log data to file"""
        try:
            # Use provided timestamp or current time
            if timestamp:
                log_data["timestamp"] = timestamp
            elif "timestamp" not in log_data:
                log_data["timestamp"] = datetime.now().isoformat()
            
            # Convert to JSON string
            log_line = json.dumps(log_data) + "\n"
            
            # Write to file
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(log_line)
            
            return True
            
        except Exception as e:
            print(f"Error logging to file: {e}")
            return False
    
    async def log(self, log_data: Dict[str, Any], destination: str = "mongo", timestamp: Optional[datetime] = None) -> bool:
        """
        Log data to specified destination
        
        Args:
            log_data: Data to log
            destination: "mongo" or "file"
            
        Returns:
            bool: True if successful, False otherwise
        """
        if destination.lower() == "mongo":
            return await self.log_to_mongo(log_data, timestamp)
        elif destination.lower() == "file":
            return self.log_to_file(log_data, timestamp)
        else:
            print(f"Unknown destination: {destination}")
            return False
    
    def close(self):
        """Close MongoDB connection"""
        if self._mongo_client:
            self._mongo_client.close()


# Create global logger service instance
logger_service = LoggerService()


async def log_audit_data(log_data: Dict[str, Any], destination: str = "mongo", timestamp: Optional[datetime] = None) -> bool:
    """
    Simple function to log audit data
    
    Args:
        log_data: Data to log
        destination: "mongo" or "file"
        
    Returns:
        bool: True if successful, False otherwise
    """
    return await logger_service.log(log_data, destination, timestamp)


# Export the logging function
__all__ = ['log_audit_data', 'logger_service', 'LoggerService'] 