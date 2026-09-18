
import logging
import asyncio
from typing import Optional

from services.scheduler.app.consumers.audit_consumer import AuditConsumer
from services.scheduler.app.consumers.job_consumer import JobConsumer
from services.scheduler.app.services.cron_manager import CronManager
from libs.messaging.rabbitmq_config import config

logger = logging.getLogger(__name__)

class ConsumerManager:
    """
    Manages the lifecycle of RabbitMQ consumers (AuditConsumer, JobConsumer).
    """
    
    def __init__(self, cron_manager: CronManager, event_loop=None):
        self.cron_manager = cron_manager
        self.event_loop = event_loop
        self.audit_consumer: Optional[AuditConsumer] = None
        self.job_consumer: Optional[JobConsumer] = None
        self.is_running = False

    def start(self):
        """Start the consumers whose feature flag is enabled."""
        if self.is_running:
            return

        if not config.USE_RABBITMQ_JOBS and not config.USE_RABBITMQ_AUDIT:
            logger.info("ConsumerManager: RabbitMQ consumers disabled by configuration")
            self.is_running = True
            return

        logger.info("Starting ConsumerManager...")

        try:
            if config.USE_RABBITMQ_AUDIT:
                self.audit_consumer = AuditConsumer()
                self.audit_consumer.start()
            else:
                logger.info("ConsumerManager: audit consumer disabled (USE_RABBITMQ_AUDIT=false)")

            if config.USE_RABBITMQ_JOBS:
                self.job_consumer = JobConsumer(self.cron_manager, self.event_loop)
                self.job_consumer.start()
            else:
                logger.info("ConsumerManager: job consumer disabled (USE_RABBITMQ_JOBS=false)")

            self.is_running = True
            logger.info("ConsumerManager started successfully")

        except Exception as e:
            logger.error(f"Failed to start ConsumerManager: {e}")

    def stop(self):
        """Stop all consumers"""
        logger.info("Stopping ConsumerManager...")
        self.is_running = False
        
        if self.audit_consumer:
            self.audit_consumer.stop()
            self.audit_consumer.join(timeout=5.0)
            
        if self.job_consumer:
            self.job_consumer.stop()
            self.job_consumer.join(timeout=5.0)
            
        logger.info("ConsumerManager stopped")
