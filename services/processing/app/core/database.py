"""
Database connection management for Processing Service
"""

import logging
import os
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from .config import settings

from libs.database.mongo_client_factory import get_processing_client

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient = None
_db: AsyncIOMotorDatabase = None
_audit_db: AsyncIOMotorDatabase = None
_auth_db: AsyncIOMotorDatabase = None


def get_database_client() -> AsyncIOMotorClient:
    """Get or create Motor async MongoDB client (singleton pattern)."""
    global _client
    if _client is None:
        try:
            _client = get_processing_client(settings.MONGODB_URI)
            logger.info("MongoDB Motor client acquired for processing service")
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


def get_audit_database() -> AsyncIOMotorDatabase:
    """Get async Motor audit database."""
    global _audit_db
    if _audit_db is None:
        client = get_database_client()
        _audit_db = client[settings.AUDIT_DB_NAME]
    return _audit_db


def get_auth_database() -> AsyncIOMotorDatabase:
    """Get async Motor auth database."""
    global _auth_db
    if _auth_db is None:
        client = get_database_client()
        auth_db_name = os.getenv("AUTH_DB_NAME", "dev_auth_db")
        _auth_db = client[auth_db_name]
    return _auth_db


def close_database_connections():
    """Close all database connections."""
    global _client, _db, _audit_db, _auth_db
    if _client:
        _client.close()
        _client = None
        _db = None
        _audit_db = None
        _auth_db = None
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
