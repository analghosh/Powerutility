"""
Database connection management for Scheduler Service
"""

import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from .config import settings

from libs.database.mongo_client_factory import get_scheduler_client

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient = None
_db: AsyncIOMotorDatabase = None


def get_database_client() -> AsyncIOMotorClient:
    """Get or create Motor async MongoDB client (singleton pattern)."""
    global _client
    if _client is None:
        try:
            _client = get_scheduler_client(settings.MONGODB_URI)
            logger.info("MongoDB Motor client acquired for scheduler service")
        except Exception as e:
            logger.error(f"Failed to acquire MongoDB Motor client: {e}")
            raise
    return _client


def get_database() -> AsyncIOMotorDatabase:
    """Get async Motor database."""
    global _db
    if _db is None:
        client = get_database_client()
        _db = client[settings.MONGODB_DATABASE]
    return _db

def close_database_connections():
    """Close database connections."""
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
        logger.info("Database connections closed")


def get_database_stats() -> dict:
    """Get database connection statistics."""
    try:
        get_database_client()
        return {
            "connected": True,
            "database": settings.MONGODB_DATABASE,
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e),
        }