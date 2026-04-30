"""
Database connection management for Auth Service
"""

import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from .config import settings

from libs.database.mongo_client_factory import get_motor_client

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient = None
_auth_db: AsyncIOMotorDatabase = None
_audit_db: AsyncIOMotorDatabase = None


def get_database_client() -> AsyncIOMotorClient:
    """Get Motor async client for auth service."""
    global _client
    if _client is None:
        _client = get_motor_client(settings.MONGODB_URI, "auth_motor_client")
        logger.info("MongoDB Motor client acquired for auth service")
    return _client


def get_auth_database() -> AsyncIOMotorDatabase:
    """Get async Motor auth database."""
    global _auth_db
    if _auth_db is None:
        _auth_db = get_database_client()[settings.AUTH_DB_NAME]
    return _auth_db


def get_audit_database() -> AsyncIOMotorDatabase:
    """Get async Motor audit database."""
    global _audit_db
    if _audit_db is None:
        _audit_db = get_database_client()[settings.AUDIT_DB_NAME]
    return _audit_db


def close_database_connections():
    """Close database connections."""
    global _client, _auth_db, _audit_db
    try:
        from libs.database.mongo_client_factory import close_mongo_client
        close_mongo_client("auth_motor_client")
        _client = None
        _auth_db = None
        _audit_db = None
        logger.info("Auth service database connections closed")
    except Exception as e:
        logger.error(f"Error during database cleanup: {e}")


def get_database_stats() -> dict:
    """Get database connection statistics."""
    try:
        get_database_client()
        return {
            "connected": True,
            "auth_db": settings.AUTH_DB_NAME,
            "audit_db": settings.AUDIT_DB_NAME,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}