"""
Async database connection management for Auth Service using Motor
"""

import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from .config import settings

logger = logging.getLogger(__name__)

# Global database connections
_client: AsyncIOMotorClient = None
_auth_db: AsyncIOMotorDatabase = None
_audit_db: AsyncIOMotorDatabase = None


def get_database_client() -> AsyncIOMotorClient:
    """Get async Motor client using centralized factory"""
    from libs.database.mongo_client_factory import get_auth_client
    return get_auth_client(settings.MONGODB_URI)


def get_auth_database() -> AsyncIOMotorDatabase:
    """Get auth database"""
    global _auth_db
    if _auth_db is None:
        client = get_database_client()
        _auth_db = client[settings.AUTH_DB_NAME]
    return _auth_db


def get_audit_database() -> AsyncIOMotorDatabase:
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

        _client = None
        _auth_db = None
        _audit_db = None

        logger.info("🔒 Auth service database connections cleanup completed")

    except Exception as e:
        logger.error(f"❌ Error during database cleanup: {e}")


async def get_database_stats() -> dict:
    """Get database connection statistics"""
    try:
        client = get_database_client()
        server_info = await client.admin.command("ping")

        return {
            "connected": True,
            "auth_db": settings.AUTH_DB_NAME,
            "audit_db": settings.AUDIT_DB_NAME,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}
