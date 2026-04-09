"""
Async database connection management for Scheduler Service using Motor

All database operations use the shared async Motor client to prevent connection exhaustion.
"""

import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from .config import settings

from libs.database.mongo_client_factory import get_mongo_client

logger = logging.getLogger(__name__)

# Global database connection (uses shared client)
_client: AsyncIOMotorClient = None
_db: AsyncIOMotorDatabase = None


def get_database_client() -> AsyncIOMotorClient:
    """
    Get or create async Motor client using shared singleton pattern.
    Uses the same shared client as all repositories to prevent pool exhaustion.
    """
    global _client
    if _client is None:
        try:
            _client = get_mongo_client(
                settings.MONGODB_URI,
                "shared_repository_client",
                maxPoolSize=50,
                minPoolSize=10,
            )
            logger.info("✅ MongoDB async connection established (using shared Motor client)")
        except Exception as e:
            logger.error(f"❌ Failed to connect to MongoDB: {e}")
            raise
    return _client


def get_database() -> AsyncIOMotorDatabase:
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


async def get_database_stats() -> dict:
    """Get database connection statistics"""
    try:
        client = get_database_client()
        await client.admin.command("ping")
        return {
            "connected": True,
            "database": settings.MONGODB_DATABASE,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}
