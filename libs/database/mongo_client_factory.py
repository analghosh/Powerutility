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

logger = logging.getLogger(__name__)

class MongoClientFactory:
    """
    Centralized factory for MongoDB clients with connection pooling
    Ensures all MongoDB connections use consistent settings and proper cleanup
    """
    
    _instance = None
    _lock = threading.Lock()
    _clients: Dict[str, MongoClient] = {}
    
    # Optimized connection settings for all clients
    DEFAULT_CONNECTION_SETTINGS = {
        "maxPoolSize": 5,  # Limit to prevent FD leaks
        "minPoolSize": 1,
        "maxIdleTimeMS": 30000,  # Close idle connections after 30s
        "waitQueueTimeoutMS": 5000,  # Timeout for getting connection from pool
        "serverSelectionTimeoutMS": 5000,  # Server selection timeout
        "socketTimeoutMS": 20000,  # Socket timeout
        "connectTimeoutMS": 10000,  # Connection timeout
        "heartbeatFrequencyMS": 10000,  # Heartbeat frequency
        "maxConnecting": 2,  # Limit concurrent connections
        "retryWrites": True
    }
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    # Register cleanup on exit
                    atexit.register(cls._instance.cleanup_all_clients)
                    logger.info("✅ MongoClientFactory initialized")
        return cls._instance
    
    def get_client(self, mongodb_uri: str, client_name: str = "default", **kwargs) -> MongoClient:
        """
        Get or create a MongoDB client with optimized settings
        
        Args:
            mongodb_uri: MongoDB connection URI
            client_name: Unique identifier for this client
            **kwargs: Additional connection settings (will override defaults)
            
        Returns:
            MongoClient instance with connection pooling
        """
        with self._lock:
            # Pymongo's MongoClient handles reconnection and health checks internally.
            # Aggressive pinging and closing shared clients in a singleton factory 
            # causes "MongoClient closed" errors in concurrent environments.
            
            if client_name not in self._clients:
                try:
                    # Merge default settings with any overrides
                    connection_settings = {**self.DEFAULT_CONNECTION_SETTINGS, **kwargs}
                    
                    client = MongoClient(mongodb_uri, **connection_settings)
                    
                    # Test connection
                    client.admin.command('ping')
                    self._clients[client_name] = client
                    
                    logger.info(f"✅ Created MongoDB client '{client_name}' with pool size {connection_settings['maxPoolSize']}")
                    
                except Exception as e:
                    logger.error(f"❌ Failed to create MongoDB client '{client_name}': {e}")
                    raise
            
            return self._clients[client_name]
    
    def get_database(self, mongodb_uri: str, database_name: str, client_name: str = "default") -> Database:
        """
        Get a database instance using the factory
        
        Args:
            mongodb_uri: MongoDB connection URI
            database_name: Name of the database
            client_name: Unique identifier for the client
            
        Returns:
            Database instance
        """
        client = self.get_client(mongodb_uri, client_name)
        return client[database_name]
    
    def close_client(self, client_name: str):
        """Close a specific client"""
        with self._lock:
            if client_name in self._clients:
                try:
                    self._clients[client_name].close()
                    del self._clients[client_name]
                    logger.info(f"🔒 Closed MongoDB client '{client_name}'")
                except Exception as e:
                    logger.error(f"❌ Error closing client '{client_name}': {e}")
    
    def cleanup_all_clients(self):
        """Close all MongoDB clients"""
        with self._lock:
            for name, client in self._clients.items():
                try:
                    client.close()
                    logger.info(f"🔒 Closed MongoDB client '{name}'")
                except Exception as e:
                    logger.error(f"❌ Error closing client '{name}': {e}")
            
            self._clients.clear()
            logger.info("🔒 All MongoDB clients cleaned up")
    
    def get_client_stats(self) -> Dict[str, any]:
        """Get statistics about current clients"""
        stats = {
            "total_clients": len(self._clients),
            "clients": {}
        }
        
        for name, client in self._clients.items():
            try:
                server_info = client.server_info()
                stats["clients"][name] = {
                    "connected": True,
                    "server_version": server_info.get("version"),
                    "max_pool_size": getattr(client, 'max_pool_size', 5)
                }
            except Exception as e:
                stats["clients"][name] = {
                    "connected": False,
                    "error": str(e)
                }
        
        return stats


# Global singleton instance
_factory = MongoClientFactory()

# Convenience functions for easy access
def get_mongo_client(mongodb_uri: str, client_name: str = "default", **kwargs) -> MongoClient:
    """Get MongoDB client using the factory"""
    return _factory.get_client(mongodb_uri, client_name, **kwargs)

def get_mongo_database(mongodb_uri: str, database_name: str, client_name: str = "default") -> Database:
    """Get MongoDB database using the factory"""
    return _factory.get_database(mongodb_uri, database_name, client_name)

def close_mongo_client(client_name: str):
    """Close a specific MongoDB client"""
    _factory.close_client(client_name)

def cleanup_all_mongo_clients():
    """Cleanup all MongoDB clients"""
    _factory.cleanup_all_clients()

def get_mongo_client_stats() -> Dict[str, any]:
    """Get MongoDB client statistics"""
    return _factory.get_client_stats()

# Service-specific convenience functions
def get_auth_client(mongodb_uri: str) -> MongoClient:
    """Get MongoDB client for auth service"""
    return get_mongo_client(mongodb_uri, "auth_service")

def get_scheduler_client(mongodb_uri: str) -> MongoClient:
    """Get MongoDB client for scheduler service"""
    return get_mongo_client(mongodb_uri, "scheduler_service")

def get_processing_client(mongodb_uri: str) -> MongoClient:
    """Get MongoDB client for processing service"""
    return get_mongo_client(mongodb_uri, "processing_service")

def get_audit_queue_client(mongodb_uri: str) -> MongoClient:
    """Get MongoDB client for audit queue"""
    return get_mongo_client(mongodb_uri, "audit_queue")