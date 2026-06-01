"""
Database connection management for Scheduler Service

IMPORTANT: This module now uses the centralized shared client pattern.
All database operations share a single MongoDB client to prevent connection exhaustion.
"""

import logging
from pymongo import MongoClient
from pymongo.database import Database
from .config import settings

# Import centralized factory
from libs.database.mongo_client_factory import get_mongo_client

logger = logging.getLogger(__name__)

# Global database connection (uses shared client)
_client: MongoClient = None
_db: Database = None

def get_database_client() -> MongoClient:
    """
    Get or create MongoDB client using shared singleton pattern
    
    IMPORTANT: This now uses the same shared client as all repositories
    to prevent connection pool exhaustion.
    
    Returns:
        MongoClient: Shared MongoDB client instance
    """
    global _client
    if _client is None:
        try:
            # ✅ FIX: Use shared client name to reuse the same client as repositories
            # This ensures scheduler service operations use the same connection pool
            _client = get_mongo_client(
                settings.MONGODB_URI,
                "shared_repository_client",  # Same name as BaseRepository
                maxPoolSize=50,  # Increased from 5 to handle concurrent operations
                minPoolSize=10   # Increased from 1 to maintain ready connections
            )
            
            # Test connection
            _client.admin.command('ping')
            logger.info("✅ MongoDB connection established (using shared client)")
            
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

def close_database_connections():
    """Close all database connections"""
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
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