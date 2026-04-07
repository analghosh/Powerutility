import inspect
import logging
import asyncio
from typing import Any, Callable
import pika

from libs.messaging.base_consumer import BaseConsumer
from libs.messaging.rabbitmq_config import config
from libs.messaging.message_models import JobTriggerMessage

logger = logging.getLogger(__name__)

class JobConsumer(BaseConsumer):
    """
    Consumer for job trigger messages.
    Triggers jobs in CronManager.
    """
    
    def __init__(self, cron_manager, event_loop=None):
        super().__init__(
            queue_name=config.QUEUE_JOBS,
            exchange_name=config.EXCHANGE_JOBS,
            routing_key="job.trigger"
        )
        self.cron_manager = cron_manager
        self.event_loop = event_loop

    def process_message(self, message: Any, properties: pika.BasicProperties) -> bool:
        """
        Process job trigger message.
        """
        try:
            # Validate message
            if isinstance(message, dict):
                job_msg = JobTriggerMessage(**message)
            else:
                logger.error(f"Invalid message format: {type(message)}")
                return False
                
            job_type = job_msg.job_type
            payload = job_msg.payload or {}
            
            logger.info(f"Received job trigger: {job_type} from {job_msg.source_service}")
            
            # Find the job function
            job_func = self.cron_manager._get_job_function(job_type)
            if not job_func:
                logger.warning(f"No job function found for type: {job_type}")
                return True # Ack anyway since we can't handle it
            
            # Trigger the job using the proper logging wrapper
            # We construct a temporary cron job object to pass to the executor
            from libs.core.cron_manager import CronJob

            def _build_invoked_func(base_fn: Callable, job_payload: dict) -> Callable:
                """Wrap job so RabbitMQ payload is passed when the handler accepts ``payload``."""
                sig = inspect.signature(base_fn)
                accepts_payload = "payload" in sig.parameters

                async def async_invoked():
                    if accepts_payload:
                        return await base_fn(payload=job_payload)
                    if asyncio.iscoroutinefunction(base_fn):
                        return await base_fn()
                    return base_fn()

                def sync_invoked():
                    if accepts_payload:
                        return base_fn(payload=job_payload)
                    return base_fn()

                if asyncio.iscoroutinefunction(base_fn):
                    return async_invoked
                if accepts_payload:
                    # Sync handler that expects payload — still wrap for consistent CronJob.func typing
                    return sync_invoked
                return base_fn

            invoked = _build_invoked_func(job_func, payload)
            
            # Create a temporary job object compatible with the executor
            temp_job = CronJob(
                job_id=job_type,
                name=f"{job_type} (Triggered)",
                schedule="manual",
                func=invoked,
                enabled=True
            )
            
            # Use the proper execution method that includes logging and error tracking
            # This ensures queue-triggered jobs show up in the dashboard just like cron jobs
            
            loop = self.event_loop
            if loop is None:
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    logger.warning("No event loop found, creating a new one")
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            
            # We must use run_coroutine_threadsafe because we are in a consumer thread,
            # but the cron manager and its resources are bound to the main asyncio loop.
            future = None
            if asyncio.iscoroutinefunction(self.cron_manager._execute_job_with_logging_async):
                future = asyncio.run_coroutine_threadsafe(
                    self.cron_manager._execute_job_with_logging_async(temp_job), 
                    loop
                )
            else:
                # If for some reason the wrapper isn't async (unlikely in this codebase)
                # This path is less robust for error checking but less likely to be hit
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self.cron_manager._execute_job_with_logging_async(temp_job))
                )
            
            # Attach a callback to log the result of the execution
            if future:
                def job_done_callback(fut):
                    try:
                        exc = fut.exception()
                        if exc:
                            logger.error(f"Job {job_type} execution FAILED in background: {exc}", exc_info=exc)
                        else:
                            res = fut.result()
                            logger.info(f"Job {job_type} execution COMPLETED in background. Result: {str(res)[:100]}...")
                    except Exception as cb_exc:
                        logger.error(f"Error in job callback: {cb_exc}")

                future.add_done_callback(job_done_callback)
                
            logger.info(f"Triggered job {job_type} - Submitted to Main Loop")
            return True
            
        except Exception as e:
            logger.error(f"Failed to process job trigger: {e}", exc_info=True)
            return False
