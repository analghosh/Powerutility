"""
Centralized MongoDB Client Factory for EPMS
Single point of control for all MongoDB connections
"""

import logging
import atexit
import threading
from typing import Dict, Optional
from pymongo import MongoClient
from pymongo.database import Database
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

class MongoClientFactory:
    """
    Centralized factory for MongoDB clients with connection pooling.
    Provides both sync (pymongo) and async (Motor) clients.

    Sync clients: audit_queue (threading-based), auth service
    Motor clients: all other services (scheduler, processing, repositories)
    """

    _instance = None
    _lock = threading.Lock()
    _sync_clients: Dict[str, MongoClient] = {}
    _motor_clients: Dict[str, AsyncIOMotorClient] = {}

    # Optimized connection settings for sync clients (audit queue / auth)
    SYNC_CONNECTION_SETTINGS = {
        "maxPoolSize": 5,
        "minPoolSize": 1,
        "maxIdleTimeMS": 30000,
        "waitQueueTimeoutMS": 5000,
        "serverSelectionTimeoutMS": 5000,
        "socketTimeoutMS": 20000,
        "connectTimeoutMS": 10000,
        "heartbeatFrequencyMS": 10000,
        "maxConnecting": 2,
        "retryWrites": True,
    }

    # Optimized connection settings for Motor async clients
    MOTOR_CONNECTION_SETTINGS = {
        "maxPoolSize": 50,
        "minPoolSize": 10,
        "maxIdleTimeMS": 30000,
        "waitQueueTimeoutMS": 5000,
        "serverSelectionTimeoutMS": 5000,
        "socketTimeoutMS": 20000,
        "connectTimeoutMS": 10000,
        "heartbeatFrequencyMS": 10000,
        "maxConnecting": 5,
        "retryWrites": True,
    }

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    atexit.register(cls._instance.cleanup_all_clients)
                    logger.info("MongoClientFactory initialized")
        return cls._instance

    # ------------------------------------------------------------------
    # Sync (pymongo) client — for audit_queue threading, auth service
    # ------------------------------------------------------------------

    def get_client(self, mongodb_uri: str, client_name: str = "default", **kwargs) -> MongoClient:
        """Get or create a sync pymongo MongoClient."""
        with self._lock:
            if client_name not in self._sync_clients:
                try:
                    settings = {**self.SYNC_CONNECTION_SETTINGS, **kwargs}
                    client = MongoClient(mongodb_uri, **settings)
                    client.admin.command("ping")
                    self._sync_clients[client_name] = client
                    logger.info(f"Created sync MongoDB client '{client_name}'")
                except Exception as e:
                    logger.error(f"Failed to create sync MongoDB client '{client_name}': {e}")
                    raise
            return self._sync_clients[client_name]

    def get_database(self, mongodb_uri: str, database_name: str, client_name: str = "default") -> Database:
        client = self.get_client(mongodb_uri, client_name)
        return client[database_name]

    # ------------------------------------------------------------------
    # Async (Motor) client — for all service repositories
    # ------------------------------------------------------------------

    def get_motor_client(self, mongodb_uri: str, client_name: str = "default", **kwargs) -> AsyncIOMotorClient:
        """Get or create an async Motor client."""
        with self._lock:
            if client_name not in self._motor_clients:
                try:
                    settings = {**self.MOTOR_CONNECTION_SETTINGS, **kwargs}
                    client = AsyncIOMotorClient(mongodb_uri, **settings)
                    self._motor_clients[client_name] = client
                    logger.info(f"Created Motor async client '{client_name}'")
                except Exception as e:
                    logger.error(f"Failed to create Motor client '{client_name}': {e}")
                    raise
            return self._motor_clients[client_name]

    def get_motor_database(self, mongodb_uri: str, database_name: str, client_name: str = "default") -> AsyncIOMotorDatabase:
        client = self.get_motor_client(mongodb_uri, client_name)
        return client[database_name]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close_client(self, client_name: str):
        with self._lock:
            if client_name in self._sync_clients:
                try:
                    self._sync_clients[client_name].close()
                    del self._sync_clients[client_name]
                    logger.info(f"Closed sync MongoDB client '{client_name}'")
                except Exception as e:
                    logger.error(f"Error closing sync client '{client_name}': {e}")
            if client_name in self._motor_clients:
                try:
                    self._motor_clients[client_name].close()
                    del self._motor_clients[client_name]
                    logger.info(f"Closed Motor client '{client_name}'")
                except Exception as e:
                    logger.error(f"Error closing Motor client '{client_name}': {e}")

    def cleanup_all_clients(self):
        with self._lock:
            for name, client in list(self._sync_clients.items()):
                try:
                    client.close()
                    logger.info(f"Closed sync client '{name}'")
                except Exception as e:
                    logger.error(f"Error closing sync client '{name}': {e}")
            self._sync_clients.clear()

            for name, client in list(self._motor_clients.items()):
                try:
                    client.close()
                    logger.info(f"Closed Motor client '{name}'")
                except Exception as e:
                    logger.error(f"Error closing Motor client '{name}': {e}")
            self._motor_clients.clear()

            logger.info("All MongoDB clients cleaned up")

    def get_client_stats(self) -> Dict[str, dict]:
        return {
            "sync_clients": list(self._sync_clients.keys()),
            "motor_clients": list(self._motor_clients.keys()),
        }


# Global singleton instance
_factory = MongoClientFactory()


# ------------------------------------------------------------------
# Sync convenience functions (audit_queue, auth — intentionally sync)
# ------------------------------------------------------------------

def get_mongo_client(mongodb_uri: str, client_name: str = "default", **kwargs) -> MongoClient:
    """Get sync MongoClient — use only for threading-based (audit queue, auth) contexts."""
    return _factory.get_client(mongodb_uri, client_name, **kwargs)

def get_mongo_database(mongodb_uri: str, database_name: str, client_name: str = "default") -> Database:
    return _factory.get_database(mongodb_uri, database_name, client_name)

def close_mongo_client(client_name: str):
    _factory.close_client(client_name)

def cleanup_all_mongo_clients():
    _factory.cleanup_all_clients()

def get_mongo_client_stats() -> Dict[str, dict]:
    return _factory.get_client_stats()

def get_auth_client(mongodb_uri: str) -> MongoClient:
    """Sync client for auth service (user.py uses sync pymongo)."""
    return get_mongo_client(mongodb_uri, "auth_service")

def get_audit_queue_client(mongodb_uri: str) -> MongoClient:
    """Sync client for audit queue (queue_manager.py uses threading)."""
    return get_mongo_client(mongodb_uri, "audit_queue")


# ------------------------------------------------------------------
# Motor async convenience functions — for all service repositories
# ------------------------------------------------------------------

def get_motor_client(mongodb_uri: str, client_name: str = "default", **kwargs) -> AsyncIOMotorClient:
    """Get async Motor client — use for all async service repositories."""
    return _factory.get_motor_client(mongodb_uri, client_name, **kwargs)

def get_motor_database(mongodb_uri: str, database_name: str, client_name: str = "default") -> AsyncIOMotorDatabase:
    return _factory.get_motor_database(mongodb_uri, database_name, client_name)

def get_processing_client(mongodb_uri: str) -> AsyncIOMotorClient:
    """Motor async client for processing service."""
    return get_motor_client(mongodb_uri, "processing_service")

def get_scheduler_client(mongodb_uri: str) -> AsyncIOMotorClient:
    """Motor async client for scheduler service."""
    return get_motor_client(mongodb_uri, "scheduler_service")

def get_processing_motor_client(mongodb_uri: str) -> AsyncIOMotorClient:
    """Alias for get_processing_client."""
    return get_processing_client(mongodb_uri)

def get_scheduler_motor_client(mongodb_uri: str) -> AsyncIOMotorClient:
    """Alias for get_scheduler_client."""
    return get_scheduler_client(mongodb_uri)
