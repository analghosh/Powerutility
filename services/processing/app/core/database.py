"""
Database connection management for Processing Service
"""

import logging
from pymongo import MongoClient
from pymongo.database import Database
from .config import settings

logger = logging.getLogger(__name__)

# Global database connection
_client: MongoClient = None
_db: Database = None
_audit_db: Database = None
_auth_db: Database = None

def get_database_client() -> MongoClient:
    """Get or create MongoDB client (singleton pattern)"""
    global _client
    if _client is None:
        try:
            _client = MongoClient(
                settings.MONGODB_URI,
                maxPoolSize=20,  # Increased for better performance
                minPoolSize=5,   # Keep more connections ready
                maxIdleTimeMS=60000,  # Keep connections longer
                serverSelectionTimeoutMS=3000,  # Faster timeout
                socketTimeoutMS=10000,  # Reduced socket timeout
                connectTimeoutMS=5000,   # Faster connection timeout
                retryWrites=True
            )
            # Test connection
            _client.admin.command('ping')
            logger.info("✅ MongoDB connection established")
            
            # Register cleanup on exit to prevent FD leaks
            import atexit
            atexit.register(close_database_connections)
        except Exception as e:
            logger.error(f"❌ Failed to connect to MongoDB: {e}")
            raise
    return _client

def get_database() -> Database:
    """Get database"""
    global _db
    if _db is None:
        client = get_database_client()
        _db = client[settings.MONGODB_DATABASE]
    return _db

def get_audit_database() -> Database:
    """Get audit database"""
    global _audit_db
    if _audit_db is None:
        client = get_database_client()
        _audit_db = client[settings.AUDIT_DB_NAME]
    return _audit_db

def get_auth_database() -> Database:
    """Get auth database"""
    global _auth_db
    if _auth_db is None:
        client = get_database_client()
        # Get AUTH_DB_NAME from environment or use default
        import os
        auth_db_name = os.getenv("AUTH_DB_NAME", "dev_auth_db")
        _auth_db = client[auth_db_name]
    return _auth_db

def close_database_connections():
    """Close all database connections"""
    global _client, _db, _audit_db, _auth_db
    if _client:
        _client.close()
        _client = None
        _db = None
        _audit_db = None
        _auth_db = None
        logger.info("🔒 Database connections closed")

def get_database_stats() -> dict:
    """Get database connection statistics"""
    try:
        client = get_database_client()
        server_info = client.server_info()
        
        return {
            "connected": True,
            "server_version": server_info.get("version"),
            "connection_pool_size": getattr(client, 'max_pool_size', 'unknown'),
            "database": settings.MONGODB_DATABASE
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e)
        }