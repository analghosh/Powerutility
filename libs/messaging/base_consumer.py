
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
        
    # Auth errors: stop retrying entirely — wrong credentials won't fix themselves
    _AUTH_ERROR_CODES = {403}
    _AUTH_ERROR_PHRASES = ("ACCESS_REFUSED", "authentication", "ProbableAuthenticationError", "credentials")

    def _is_auth_error(self, exc: Exception) -> bool:
        """Return True if the exception indicates a permanent auth failure."""
        msg = str(exc)
        if isinstance(exc, pika.exceptions.ProbableAuthenticationError):
            return True
        if isinstance(exc, pika.exceptions.ConnectionClosedByBroker):
            if exc.reply_code in self._AUTH_ERROR_CODES:
                return True
        return any(phrase in msg for phrase in self._AUTH_ERROR_PHRASES)

    def run(self):
        """Main consumer loop with exponential backoff and auth-error fast-fail."""
        logger.info(f"Starting consumer for queue: {self.queue_name}")

        retry_delay = 5      # seconds — initial backoff
        max_delay = 300      # cap at 5 minutes for transient errors
        consecutive_failures = 0

        while not self.should_stop:
            try:
                self.connection = self.manager.create_connection()
                if not self.connection or self.connection.is_closed:
                    logger.warning("Connection unavailable, retrying...")
                    time.sleep(retry_delay)
                    continue

                # Reset backoff on successful connection
                retry_delay = 5
                consecutive_failures = 0

                self.channel = self.connection.channel()
                self.setup_topology()
                self.channel.basic_qos(prefetch_count=config.RABBITMQ_CONSUMER_PREFETCH)
                self.channel.basic_consume(
                    queue=self.queue_name,
                    on_message_callback=self._on_message,
                    auto_ack=False
                )

                logger.info(f"Consumer listening on {self.queue_name}")
                self.channel.start_consuming()

            except Exception as e:
                if self._is_auth_error(e):
                    # Auth errors are permanent — stop the consumer thread entirely
                    logger.error(
                        f"Consumer [{self.queue_name}] stopping: authentication failed. "
                        f"Fix RABBITMQ_USER/RABBITMQ_PASSWORD or set USE_RABBITMQ_JOBS=false. "
                        f"Error: {e}"
                    )
                    self.should_stop = True
                    break

                # Transient error — exponential backoff
                consecutive_failures += 1
                retry_delay = min(retry_delay * 2, max_delay)
                logger.error(
                    f"Consumer [{self.queue_name}] error (attempt {consecutive_failures}), "
                    f"retrying in {retry_delay}s: {e}"
                )
                if not self.should_stop:
                    time.sleep(retry_delay)
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
