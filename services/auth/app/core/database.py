"""
Database connection management for Auth Service
"""

import logging
from pymongo import MongoClient
from pymongo.database import Database
from .config import settings

logger = logging.getLogger(__name__)

# Global database connection
_client: MongoClient = None
_auth_db: Database = None
_audit_db: Database = None

def get_database_client() -> MongoClient:
    """Get MongoDB client using centralized factory"""
    from libs.database.mongo_client_factory import get_auth_client
    return get_auth_client(settings.MONGODB_URI)

def get_auth_database() -> Database:
    """Get auth database"""
    global _auth_db
    if _auth_db is None:
        client = get_database_client()
        _auth_db = client[settings.AUTH_DB_NAME]
    return _auth_db

def get_audit_database() -> Database:
    """Get audit database"""
    global _audit_db
    if _audit_db is None:
        client = get_database_client()
        _audit_db = client[settings.AUDIT_DB_NAME]
    return _audit_db

def close_database_connections():
    """Close database connections using centralized factory"""
    global _client, _auth_db, _audit_db
    
    try:
        from libs.database.mongo_client_factory import close_mongo_client
        close_mongo_client("auth_service")
        
        # Reset global variables
        _client = None
        _auth_db = None
        _audit_db = None
        
        logger.info("🔒 Auth service database connections cleanup completed")
        
    except Exception as e:
        logger.error(f"❌ Error during database cleanup: {e}")

def get_database_stats() -> dict:
    """Get database connection statistics"""
    try:
        client = get_database_client()
        server_info = client.server_info()
        
        return {
            "connected": True,
            "server_version": server_info.get("version"),
            "connection_pool_size": getattr(client, 'max_pool_size', 'unknown'),
            "auth_db": settings.AUTH_DB_NAME,
            "audit_db": settings.AUDIT_DB_NAME
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e)
        }