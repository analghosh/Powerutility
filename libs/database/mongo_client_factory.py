"""
Centralized Async MongoDB Client Factory for EPMS
Single point of control for all MongoDB connections using Motor (AsyncIOMotorClient)
"""

import logging
import asyncio
import threading
from typing import Dict, Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


class MongoClientFactory:
    """
    Centralized factory for async MongoDB clients with connection pooling.
    Uses Motor (AsyncIOMotorClient) for non-blocking async operations.
    """

    _instance = None
    _lock = threading.Lock()
    _clients: Dict[str, AsyncIOMotorClient] = {}

    # Optimized connection settings for all clients
    DEFAULT_CONNECTION_SETTINGS = {
        "maxPoolSize": 20,
        "minPoolSize": 2,
        "maxIdleTimeMS": 30000,
        "waitQueueTimeoutMS": 5000,
        "serverSelectionTimeoutMS": 5000,
        "socketTimeoutMS": 20000,
        "connectTimeoutMS": 10000,
        "heartbeatFrequencyMS": 10000,
        "maxConnecting": 2,
        "retryWrites": True,
    }

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    logger.info("✅ MongoClientFactory (async/Motor) initialized")
        return cls._instance

    def get_client(
        self, mongodb_uri: str, client_name: str = "default", **kwargs
    ) -> AsyncIOMotorClient:
        """
        Get or create an AsyncIOMotorClient with optimized settings.

        Args:
            mongodb_uri: MongoDB connection URI
            client_name: Unique identifier for this client
            **kwargs: Additional connection settings (will override defaults)

        Returns:
            AsyncIOMotorClient instance with connection pooling
        """
        # Motor clients are safe to share within a single event loop, but sharing the same
        # instance across different threads/event loops can surface as asyncio Future errors
        # (e.g., "await wasn't used with future"). Uvicorn/gunicorn setups may execute async
        # startup or background jobs in different threads.
        #
        # To make this robust, we scope cached clients to the current thread.
        thread_key = f"{client_name}:{threading.get_ident()}"

        with self._lock:
            if thread_key not in self._clients:
                try:
                    connection_settings = {**self.DEFAULT_CONNECTION_SETTINGS, **kwargs}
                    client = AsyncIOMotorClient(mongodb_uri, **connection_settings)
                    self._clients[thread_key] = client
                    logger.info(
                        f"✅ Created async Motor client '{client_name}' (thread {threading.get_ident()}) "
                        f"(pool size {connection_settings['maxPoolSize']})"
                    )
                except Exception as e:
                    logger.error(f"❌ Failed to create Motor client '{client_name}': {e}")
                    raise

            return self._clients[thread_key]

    def get_database(
        self, mongodb_uri: str, database_name: str, client_name: str = "default"
    ) -> AsyncIOMotorDatabase:
        """
        Get an async database instance using the factory.

        Args:
            mongodb_uri: MongoDB connection URI
            database_name: Name of the database
            client_name: Unique identifier for the client

        Returns:
            AsyncIOMotorDatabase instance
        """
        client = self.get_client(mongodb_uri, client_name)
        return client[database_name]

    def close_client(self, client_name: str):
        """Close a specific client"""
        with self._lock:
            # Close all thread-scoped instances for the logical client name.
            keys_to_close = [k for k in self._clients.keys() if k == client_name or k.startswith(f"{client_name}:")]
            for key in keys_to_close:
                try:
                    self._clients[key].close()
                    del self._clients[key]
                    logger.info(f"🔒 Closed Motor client '{key}'")
                except Exception as e:
                    logger.error(f"❌ Error closing client '{key}': {e}")

    def cleanup_all_clients(self):
        """Close all MongoDB clients"""
        with self._lock:
            for name, client in self._clients.items():
                try:
                    client.close()
                    logger.info(f"🔒 Closed Motor client '{name}'")
                except Exception as e:
                    logger.error(f"❌ Error closing client '{name}': {e}")
            self._clients.clear()
            logger.info("🔒 All Motor clients cleaned up")

    def get_client_stats(self) -> Dict[str, any]:
        """Get statistics about current clients"""
        return {
            "total_clients": len(self._clients),
            "clients": {name: {"type": "AsyncIOMotorClient"} for name in self._clients},
        }


# Global singleton instance
_factory = MongoClientFactory()


# Convenience functions
def get_mongo_client(
    mongodb_uri: str, client_name: str = "default", **kwargs
) -> AsyncIOMotorClient:
    """Get async Motor client using the factory"""
    return _factory.get_client(mongodb_uri, client_name, **kwargs)


def get_mongo_database(
    mongodb_uri: str, database_name: str, client_name: str = "default"
) -> AsyncIOMotorDatabase:
    """Get async Motor database using the factory"""
    return _factory.get_database(mongodb_uri, database_name, client_name)


def close_mongo_client(client_name: str):
    """Close a specific Motor client"""
    _factory.close_client(client_name)


def cleanup_all_mongo_clients():
    """Cleanup all Motor clients"""
    _factory.cleanup_all_clients()


def get_mongo_client_stats() -> Dict[str, any]:
    """Get Motor client statistics"""
    return _factory.get_client_stats()


# Service-specific convenience functions
def get_auth_client(mongodb_uri: str) -> AsyncIOMotorClient:
    """Get async Motor client for auth service"""
    return get_mongo_client(mongodb_uri, "auth_service")


def get_scheduler_client(mongodb_uri: str) -> AsyncIOMotorClient:
    """Get async Motor client for scheduler service"""
    return get_mongo_client(mongodb_uri, "scheduler_service")


def get_processing_client(mongodb_uri: str) -> AsyncIOMotorClient:
    """Get async Motor client for processing service"""
    return get_mongo_client(mongodb_uri, "processing_service")


def get_audit_queue_client(mongodb_uri: str) -> AsyncIOMotorClient:
    """Get async Motor client for audit queue"""
    return get_mongo_client(mongodb_uri, "audit_queue")
