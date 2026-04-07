
import logging
import asyncio
from typing import Optional

from services.scheduler.app.consumers.audit_consumer import AuditConsumer
from services.scheduler.app.consumers.job_consumer import JobConsumer
from services.scheduler.app.services.cron_manager import CronManager

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
        """Start all consumers"""
        if self.is_running:
            return

        logger.info("Starting ConsumerManager...")
        
        try:
            # Create consumers
            self.audit_consumer = AuditConsumer()
            # Pass the event loop to the job consumer
            self.job_consumer = JobConsumer(self.cron_manager, self.event_loop)
            
            # Start consumer threads
            if self.audit_consumer:
                self.audit_consumer.start()
            
            if self.job_consumer:
                self.job_consumer.start()
                
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
