
import json
import logging
import pika
from datetime import datetime
from typing import Dict, Any, Optional
from .rabbitmq_manager import RabbitMQManager
from .rabbitmq_config import config
from .message_models import JobTriggerMessage

logger = logging.getLogger(__name__)

class JobPublisher:
    """Publisher for Job Trigger events"""

    def __init__(self):
        self.manager = RabbitMQManager()

    def publish_job_trigger(
        self,
        job_type: str,
        source_service: str,
        payload: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None
    ) -> bool:
        """
        Publish a job trigger message to RabbitMQ.

        Uses a fresh short-lived connection per publish to avoid heartbeat
        timeouts that occur when a long-lived BlockingConnection sits idle
        between ingestion processing cycles.

        Args:
            job_type: Type of job to trigger (e.g. 'demand_data_processing')
            source_service: Service initiating the trigger
            payload: Additional data for the job
            correlation_id: ID for tracing

        Returns:
            bool: True if published successfully, False otherwise
        """
        if not config.USE_RABBITMQ_JOBS:
            logger.debug("RabbitMQ Jobs disabled, skipping publish")
            return False

        connection = None
        try:
            connection = self.manager.create_connection()
            channel = connection.channel()
            self.manager.setup_topology_on_channel(channel)

            message = JobTriggerMessage(
                job_type=job_type,
                source_service=source_service,
                payload=payload or {},
                correlation_id=correlation_id
            )

            channel.basic_publish(
                exchange=config.EXCHANGE_JOBS,
                routing_key="job.trigger",
                body=message.json(),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Persistent
                    content_type='application/json',
                    timestamp=int(datetime.now().timestamp())
                )
            )

            logger.info(f"Published job trigger: {job_type}")
            return True

        except Exception as e:
            logger.error(f"Failed to publish job trigger: {str(e)}")
            return False
        finally:
            if connection and connection.is_open:
                try:
                    connection.close()
                except Exception:
                    pass
