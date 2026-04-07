
import pika
import logging
import threading
import time
from typing import Optional
from .rabbitmq_config import config

logger = logging.getLogger(__name__)

class RabbitMQManager:
    """
    Singleton manager for RabbitMQ connection.
    Handles connection establishment, recovery, and channel management.
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(RabbitMQManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self.connection = None
        self.channel = None
        self.is_connected = False
        self._connection_lock = threading.Lock()
        
        # Connection parameters
        self.credentials = pika.PlainCredentials(config.RABBITMQ_USER, config.RABBITMQ_PASSWORD)
        self.parameters = pika.ConnectionParameters(
            host=config.RABBITMQ_HOST,
            port=config.RABBITMQ_PORT,
            virtual_host=config.RABBITMQ_VHOST,
            credentials=self.credentials,
            heartbeat=60,
            blocked_connection_timeout=300
        )
        
    def connect(self) -> bool:
        """Establish connection to RabbitMQ"""
        with self._connection_lock:
            if self.is_connected and self.connection and self.connection.is_open:
                return True
                
            try:
                logger.info(f"Connecting to RabbitMQ at {config.RABBITMQ_HOST}:{config.RABBITMQ_PORT}...")
                self.connection = pika.BlockingConnection(self.parameters)
                self.channel = self.connection.channel()
                self.is_connected = True
                
                # Setup exchanges and queues
                self.setup_topology_on_channel(self.channel)
                
                logger.info("Successfully connected to RabbitMQ")
                return True
            except Exception as e:
                logger.error(f"Failed to connect to RabbitMQ: {str(e)}")
                self.is_connected = False
                return False
    
    def create_connection(self):
        """Create a NEW connection (safe for use in threads)"""
        return pika.BlockingConnection(self.parameters)
    
    def setup_topology_on_channel(self, channel):
        """Declare exchanges and queues on the given channel"""
        try:
            # Declare Exchanges
            channel.exchange_declare(
                exchange=config.EXCHANGE_AUDIT, 
                exchange_type='topic', 
                durable=True
            )
            channel.exchange_declare(
                exchange=config.EXCHANGE_JOBS, 
                exchange_type='direct', 
                durable=True
            )
            channel.exchange_declare(
                exchange=config.EXCHANGE_DLQ, 
                exchange_type='direct', 
                durable=True
            )
            
            # Declare Queues (Quorum queues for reliability)
            queue_args = {"x-queue-type": "quorum"} if config.USE_QUORUM_QUEUES else {}
            
            # Audit Queue
            channel.queue_declare(
                queue=config.QUEUE_AUDIT, 
                durable=True, 
                arguments=queue_args
            )
            channel.queue_bind(
                exchange=config.EXCHANGE_AUDIT,
                queue=config.QUEUE_AUDIT,
                routing_key="audit.#"
            )
            
            # Jobs Queue
            channel.queue_declare(
                queue=config.QUEUE_JOBS, 
                durable=True, 
                arguments=queue_args
            )
            channel.queue_bind(
                exchange=config.EXCHANGE_JOBS,
                queue=config.QUEUE_JOBS,
                routing_key="job.trigger"
            )
            
            logger.info("RabbitMQ topology setup complete for channel")
        except Exception as e:
            logger.error(f"Error setting up RabbitMQ topology: {str(e)}")
            raise

    def get_channel(self):
        """Get the current channel, reconnecting if necessary"""
        if not self.is_connected or not self.connection or self.connection.is_closed:
            if not self.connect():
                return None
        return self.channel

    def get_connection(self):
        """Get the current connection, reconnecting if necessary"""
        if not self.is_connected or not self.connection or self.connection.is_closed:
            if not self.connect():
                return None
        return self.connection
        
    def close(self):
        """Close connection cleanly"""
        try:
            if self.connection and self.connection.is_open:
                self.connection.close()
                logger.info("RabbitMQ connection closed")
        except Exception as e:
            logger.warning(f"Error closing RabbitMQ connection: {str(e)}")
        finally:
            self.is_connected = False
            self.connection = None
            self.channel = None
