
import logging
import threading
import time
import json
import pika
from typing import Callable, Dict, Any, Optional
from abc import ABC, abstractmethod

from libs.messaging.rabbitmq_manager import RabbitMQManager
from libs.messaging.rabbitmq_config import config

logger = logging.getLogger(__name__)

class BaseConsumer(ABC, threading.Thread):
    """
    Base class for RabbitMQ consumers.
    Runs in a separate thread to process messages.
    """
    
    def __init__(self, queue_name: str, exchange_name: str = None, routing_key: str = None):
        super().__init__()
        self.queue_name = queue_name
        self.exchange_name = exchange_name
        self.routing_key = routing_key
        self.manager = RabbitMQManager()
        self.should_stop = False
        self.channel = None
        self.connection = None
        self.daemon = True  # Daemon thread so it stops when main process stops
        self.name = f"Consumer-{queue_name}"
        
    def run(self):
        """Main consumer loop"""
        logger.info(f"Starting consumer for queue: {self.queue_name}")
        
        while not self.should_stop:
            try:
                # Get connection and channel
                self.connection = self.manager.create_connection()
                if not self.connection or self.connection.is_closed:
                    logger.warning("Connection lost, reconnecting...")
                    time.sleep(5)
                    continue
                    
                self.channel = self.connection.channel()
                
                # Setup topology (ensure queue exists)
                self.setup_topology()
                
                # Set prefetch count
                self.channel.basic_qos(prefetch_count=config.RABBITMQ_CONSUMER_PREFETCH)
                
                # Start consuming
                self.channel.basic_consume(
                    queue=self.queue_name,
                    on_message_callback=self._on_message,
                    auto_ack=False
                )
                
                logger.info(f"Consumer listening on {self.queue_name}")
                self.channel.start_consuming()
                
            except Exception as e:
                logger.error(f"Consumer error: {str(e)}")
                if not self.should_stop:
                    time.sleep(5)  # Wait before retry
            finally:
                self._cleanup()

    def stop(self):
        """Stop the consumer"""
        logger.info(f"Stopping consumer for {self.queue_name}")
        self.should_stop = True
        try:
            if self.channel and self.channel.is_open:
                self.channel.stop_consuming()
            if self.connection and self.connection.is_open:
                self.connection.close()
        except Exception as e:
            logger.warning(f"Error closing consumer connection: {e}")

    def setup_topology(self):
        """Setup queue and binding if needed"""
        # Ensure base topology (exchanges) exists
        self.manager.setup_topology_on_channel(self.channel)
        
        # Declare queue
        args = {"x-queue-type": "quorum"} if config.USE_QUORUM_QUEUES else {}
        self.channel.queue_declare(
            queue=self.queue_name, 
            durable=True,
            arguments=args
        )
        
        # Bind if exchange is provided
        if self.exchange_name and self.routing_key:
            self.channel.queue_bind(
                exchange=self.exchange_name,
                queue=self.queue_name,
                routing_key=self.routing_key
            )

    def _on_message(self, ch, method, properties, body):
        """Internal message handler"""
        try:
            # Parse message
            try:
                message = json.loads(body)
            except json.JSONDecodeError:
                message = body.decode('utf-8')
                
            # Process message
            success = self.process_message(message, properties)
            
            if success:
                ch.basic_ack(delivery_tag=method.delivery_tag)
            else:
                # Nack and requeue or dead-letter
                # For now, we nack without requeue to avoid infinite loops if it's a poison message
                # Ideally, use dead-letter exchange
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    @abstractmethod
    def process_message(self, message: Any, properties: pika.BasicProperties) -> bool:
        """
        Process the message.
        Return True if successful (ack), False if failed (nack).
        """
        pass
