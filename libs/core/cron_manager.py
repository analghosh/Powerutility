"""
Base Cron Job Manager
Shared functionality for cron job scheduling across all services
"""

import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from abc import ABC, abstractmethod
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from .job_factory import create_job_data_repository

logger = logging.getLogger(__name__)


class CronJob:
    """Represents a single cron job configuration"""
    
    def __init__(self, job_id: str, name: str, schedule: str, func: Callable, 
                 enabled: bool = True, timezone: str = "UTC", **kwargs):
        self.job_id = job_id
        self.name = name
        self.schedule = schedule
        self.func = func
        self.enabled = enabled
        self.timezone = timezone
        self.kwargs = kwargs


class BaseCronManager(ABC):
    """
    Base cron manager class with common functionality
    Each service should inherit from this and implement service-specific methods
    """
    
    def __init__(self, service_name: str, timezone: str = "UTC", job_data_repository=None, audit_logger=None):
        """Initialize the base cron manager"""
        self.service_name = service_name
        self.timezone = timezone
        self.job_data_repository = job_data_repository
        self.audit_logger = audit_logger
        
        try:
            # Configure scheduler with better misfire handling
            job_defaults = {
                'coalesce': False,  # Run all missed executions
                'max_instances': 3,  # Allow up to 3 concurrent instances
                'misfire_grace_time': 300  # 5 minutes grace time for misfires
            }
            
            self.scheduler = AsyncIOScheduler(
                jobstores={'default': MemoryJobStore()},
                job_defaults=job_defaults,
                timezone=self.timezone
            )
            self._is_running = False
            self._registered_jobs: Dict[str, CronJob] = {}
            
            # Thread tracking for async jobs
            import threading
            self._thread_lock = threading.Lock()
            self._active_threads: Dict[str, threading.Thread] = {}
            
            # Register service-specific jobs
            self._register_service_jobs()
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning("Could not initialize scheduler for service %s: %s", service_name, str(e))
            self.scheduler = None
    
    @abstractmethod
    def _get_job_function(self, job_type: str) -> Optional[Callable]:
        """
        Get the execution function for a job type
        This method must be implemented by each service
        
        Args:
            job_type: The job type identifier
            
        Returns:
            Callable: The function to execute for this job type
        """
        raise NotImplementedError("Subclasses must implement _get_job_function")
    
    @abstractmethod
    def _register_service_jobs(self):
        """
        Register service-specific jobs
        This method must be implemented by each service
        """
        raise NotImplementedError("Subclasses must implement _register_service_jobs")
    
    def register_job(self, job_type: str, name: str, schedule: str, func: Callable, 
                    enabled: bool, timezone: str, **kwargs) -> bool:
        """Register a new cron job"""
        try:
            if timezone is None:
                timezone = self.timezone
                
            cron_job = CronJob(
                job_id=job_type,
                name=name,
                schedule=schedule,
                func=func,
                enabled=enabled,
                timezone=timezone,
                **kwargs
            )
            
            self._registered_jobs[job_type] = cron_job
            
            logger.info("Registered cron job: %s (Type: %s) for service: %s", 
                       name, job_type, self.service_name)
            return True
            
        except (ValueError, TypeError, AttributeError) as e:
            logger.error("Failed to register job %s for service %s: %s", 
                        job_type, self.service_name, str(e))
            return False
    
    def unregister_job(self, job_type: str) -> bool:
        """Unregister a cron job"""
        try:
            if job_type in self._registered_jobs:
                # Remove from scheduler if running
                if self._is_running:
                    self.scheduler.remove_job(job_type)
                
                # Remove from registered jobs
                del self._registered_jobs[job_type]
                
                logger.info("Unregistered cron job: %s from service: %s", 
                           job_type, self.service_name)
                return True
            else:
                logger.warning("Job %s not found for unregistration in service: %s", 
                              job_type, self.service_name)
                return False
                
        except (KeyError, AttributeError) as e:
            logger.error("Failed to unregister job %s from service %s: %s", 
                        job_type, self.service_name, str(e))
            return False
        
    def start(self):
        """Start the cron scheduler with all registered jobs"""
        if not self._is_running:
            try:
                logger.info("Starting cron scheduler for service: %s...", self.service_name)
                
                # Add all enabled jobs to scheduler
                enabled_jobs = 0
                for job_type, cron_job in self._registered_jobs.items():
                    if cron_job.enabled:
                        self._add_job_to_scheduler(cron_job)
                        enabled_jobs += 1
                        logger.info("   Scheduled job: %s (Type: %s) - %s", 
                                   cron_job.name, job_type, cron_job.schedule)
                    else:
                        logger.info("   Skipped disabled job: %s (Type: %s)", 
                                   cron_job.name, job_type)
                
                # Add event listeners for monitoring
                self._add_scheduler_listeners()
                
                # Start the scheduler
                self.scheduler.start()
                self._is_running = True
                logger.info("Cron scheduler started successfully for service: %s!", self.service_name)
                logger.info("   Total registered jobs: %s", len(self._registered_jobs))
                logger.info("   Enabled jobs: %s", enabled_jobs)
                logger.info("   Timezone: %s", self.timezone)
                
            except Exception as e:
                logger.error("Failed to start cron scheduler for service %s: %s", 
                            self.service_name, str(e))
                raise
    
    def stop(self):
        """Stop the cron scheduler and wait for active threads"""
        if self._is_running:
            try:
                logger.info("Stopping cron scheduler for service: %s...", self.service_name)
                
                # Stop the scheduler first
                self.scheduler.shutdown()
                self._is_running = False
                
                # Wait for active threads to complete (with timeout)
                if self._thread_lock:
                    active_threads = []
                    with self._thread_lock:
                        active_threads = [thread for thread in self._active_threads.values() 
                                        if thread.is_alive()]
                    
                    if active_threads:
                        logger.info("Waiting for %d active job threads to complete...", len(active_threads))
                        for thread in active_threads:
                            try:
                                thread.join(timeout=30)  # Wait up to 30 seconds per thread
                                if thread.is_alive():
                                    logger.warning("Thread %s did not complete within timeout", thread.name)
                            except Exception as thread_error:
                                logger.error("Error waiting for thread %s: %s", thread.name, str(thread_error))
                
                logger.info("Cron scheduler stopped successfully for service: %s", self.service_name)
            except (RuntimeError, OSError, AttributeError) as e:
                logger.error("Failed to stop cron scheduler for service %s: %s", 
                            self.service_name, str(e))
    
    def _add_scheduler_listeners(self):
        """Add event listeners to track job execution and misfires"""
        from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED
        
        def job_executed_listener(event):
            """Log when a job executes successfully"""
            logger.info("[SCHEDULER-EVENT] Job executed: %s (service: %s)", 
                       event.job_id, self.service_name)
        
        def job_error_listener(event):
            """Log when a job encounters an error"""
            logger.error("[SCHEDULER-EVENT] Job error: %s (service: %s) - %s", 
                        event.job_id, self.service_name, event.exception)
        
        def job_missed_listener(event):
            """Log when a job execution is missed"""
            logger.warning("[SCHEDULER-EVENT] ⚠️  JOB MISSED: %s (service: %s) - scheduled for: %s", 
                          event.job_id, self.service_name, event.scheduled_run_time)
            
            # Log to audit if available
            if self.audit_logger:
                self.audit_logger.log_system_operation(
                    service=self.service_name,
                    operation="cron_job_missed",
                    message=f"Cron job missed: {event.job_id}",
                    job_id=event.job_id,
                    scheduled_run_time=str(event.scheduled_run_time)
                )
        
        self.scheduler.add_listener(job_executed_listener, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(job_error_listener, EVENT_JOB_ERROR)
        self.scheduler.add_listener(job_missed_listener, EVENT_JOB_MISSED)
        
        logger.info("Added scheduler event listeners for service: %s", self.service_name)
    
    def _add_job_to_scheduler(self, cron_job: CronJob):
        """Add a single job to the scheduler"""
        try:
            # Parse cron schedule
            schedule_parts = cron_job.schedule.split()
            if len(schedule_parts) != 5:
                raise ValueError(f"Invalid cron schedule format: {cron_job.schedule}")
            
            minute, hour, day, month, day_of_week = schedule_parts
            
            # Convert timezone string to pytz timezone object if needed
            import pytz
            if isinstance(cron_job.timezone, str):
                tz_obj = pytz.timezone(cron_job.timezone)
            else:
                tz_obj = cron_job.timezone
            
            logger.info("Creating cron trigger for job %s: minute=%s, hour=%s, day=%s, month=%s, dow=%s, tz=%s",
                       cron_job.name, minute, hour, day, month, day_of_week, tz_obj)
            
            # Create cron trigger with timezone object
            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                timezone=tz_obj  # Use timezone object, not string
            )
            
            # Log next run time
            from datetime import datetime
            now = datetime.now(tz_obj)
            next_run = trigger.get_next_fire_time(None, now)
            if next_run:
                time_until = (next_run - now).total_seconds()
                logger.info("✅ Job %s will run next at %s (in %.1f minutes / %.1f hours)",
                           cron_job.name, next_run.strftime('%Y-%m-%d %H:%M:%S %Z'), 
                           time_until/60, time_until/3600)
            else:
                logger.error("❌ Job %s has NO NEXT RUN TIME! Cron expression might be invalid: %s",
                           cron_job.name, cron_job.schedule)
            
            # Create an async wrapper for all job execution
            async def job_wrapper():
                import asyncio
                import threading
                
                thread_name = threading.current_thread().name
                try:
                    logger.info("[ASYNC] Job %s started in thread: %s", cron_job.name, thread_name)
                    result = await self._execute_job_with_logging_async(cron_job)
                    logger.info("[ASYNC] Job %s completed in thread: %s", cron_job.name, thread_name)
                    return result
                except Exception as e:
                    logger.error("[ASYNC] Error in job execution for %s (thread: %s): %s", 
                               cron_job.name, thread_name, str(e))
                    return {"status": "error", "error": str(e)}
            
            # Add job to scheduler with wrapper
            # Note: coalesce and max_instances are now set in job_defaults
            # but can be overridden per job via kwargs
            self.scheduler.add_job(
                func=job_wrapper,
                trigger=trigger,
                id=cron_job.job_id,
                name=cron_job.name,
                replace_existing=True,
                **cron_job.kwargs
            )
            
            logger.info("Added job to scheduler: %s (ID: %s) for service: %s", 
                       cron_job.name, cron_job.job_id, self.service_name)
            
        except (ValueError, TypeError) as e:
            logger.error("Failed to add job %s to scheduler for service %s: %s", 
                        cron_job.job_id, self.service_name, str(e))
            raise
    
    async def _execute_job_with_logging_async(self, cron_job: CronJob):
        """Execute a job asynchronously with proper audit logging"""
        import time
        import asyncio
        from datetime import datetime
        
        job_type = cron_job.job_id
        job_name = cron_job.name
        job_func = cron_job.func  # Get the actual job function
        start_time = datetime.now()
        execution_start_timestamp = time.time()
        
        # Generate execution ID
        execution_id = f"{job_type}_{start_time.strftime('%Y%m%d_%H%M%S')}"
        
        try:
            # Record job start in job detail repository if available
            if hasattr(self, '_record_job_start'):
                execution_id = self._record_job_start(job_type, job_name, "scheduled", start_time)
            
            # Log job start if audit logger is available
            if self.audit_logger:
                self.audit_logger.log_scheduled_job(
                    service=self.service_name,
                    job_id=execution_id,
                    status="started",
                    message=f"Cron job started: {job_name}",
                    job_name=job_name,
                    job_type=job_type
                )
            
            logger.info("[ASYNC-EXEC] Starting scheduled job: %s (ID: %s)", job_name, job_type)
            
            # Execute the actual job function asynchronously
            if asyncio.iscoroutinefunction(cron_job.func):
                # Function is already async
                result = await cron_job.func()
            else:
                # Function is sync, run in thread pool
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, cron_job.func)
            
            # Calculate duration
            duration_ms = int((time.time() - execution_start_timestamp) * 1000)
            
            # Log successful completion if audit logger is available
            if self.audit_logger:
                self.audit_logger.log_scheduled_job(
                    service=self.service_name,
                    job_id=execution_id,
                    status="completed",
                    message=f"Cron job completed: {job_name}",
                    job_name=job_name,
                    duration_ms=duration_ms,
                    job_type=job_type,
                    result_data=result if isinstance(result, dict) else None
                )
            
            logger.info("[ASYNC-SUCCESS] Completed scheduled job: %s (Duration: %dms)", job_name, duration_ms)
            return result
            
        except Exception as e:
            # Calculate duration even for failed jobs
            duration_ms = int((time.time() - execution_start_timestamp) * 1000)
            
            # Log job failure if audit logger is available
            try:
                if self.audit_logger:
                    self.audit_logger.log_scheduled_job(
                        service=self.service_name,
                        job_id=execution_id,
                        status="failed",
                        message=f"Cron job failed: {job_name} - {str(e)}",
                        job_name=job_name,
                        duration_ms=duration_ms,
                        job_type=job_type,
                        exception_type=type(e).__name__,
                        error_message=str(e)
                    )
            except Exception as audit_error:
                logger.error("Failed to log job failure: %s", audit_error)
            
            logger.error("[ASYNC-FAILED] Failed scheduled job: %s (Duration: %dms) - %s", job_name, duration_ms, str(e))
            raise
    
    def get_all_jobs(self) -> Dict[str, Any]:
        """Get status of all jobs with enhanced monitoring"""
        try:
            if not self._is_running or not self.scheduler:
                logger.warning("Scheduler is not running or not initialized for service: %s", 
                              self.service_name)
                return {
                    'service': self.service_name,
                    'scheduler_running': False,
                    'total_jobs': 0,
                    'active_jobs': 0,
                    'registered_jobs': len(self._registered_jobs),
                    'jobs': [],
                    'message': 'Scheduler is not running'
                }
            
            jobs = self.scheduler.get_jobs()
            if jobs is None:
                jobs = []
            
            job_list = []
            active_jobs = 0
            currently_running = 0
            
            for job in jobs:
                try:
                    # Check if job is currently running
                    running_instances = 0
                    if hasattr(job, '_instances'):
                        running_instances = len(job._instances)
                    
                    job_info = {
                        'job_id': job.id,
                        'name': job.name,
                        'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                        'trigger': str(job.trigger),
                        'active': True,
                        'service': self.service_name,
                        'running_instances': running_instances,
                        'max_instances': getattr(job, 'max_instances', 1),
                        'coalesce': getattr(job, 'coalesce', True),
                        'misfire_grace_time': getattr(job, 'misfire_grace_time', None)
                    }
                    job_list.append(job_info)
                    active_jobs += 1
                    if running_instances > 0:
                        currently_running += 1
                except (AttributeError, ValueError, TypeError) as job_error:
                    logger.error("Error processing job %s for service %s: %s", 
                               job.id if job else 'unknown', self.service_name, str(job_error))
            
            return {
                'service': self.service_name,
                'scheduler_running': self._is_running,
                'total_jobs': len(jobs),
                'active_jobs': active_jobs,
                'currently_running': currently_running,
                'registered_jobs': len(self._registered_jobs),
                'jobs': job_list
            }
        except (AttributeError, RuntimeError, OSError) as e:
            logger.error("Error getting all jobs for service %s: %s", self.service_name, str(e))
            return {
                'service': self.service_name,
                'scheduler_running': self._is_running,
                'total_jobs': 0,
                'active_jobs': 0,
                'registered_jobs': len(self._registered_jobs),
                'jobs': [],
                'error': str(e)
            }
    
    def trigger_job_now(self, job_id: str) -> bool:
        """Trigger a job to run immediately"""
        try:
            job = self.scheduler.get_job(job_id)
            if job:
                self.scheduler.modify_job(job_id, next_run_time=datetime.now())
                logger.info("Triggered job %s to run immediately for service: %s", 
                           job_id, self.service_name)
                return True
            else:
                logger.warning("Job %s not found in service: %s", job_id, self.service_name)
                return False
        except (AttributeError, RuntimeError, OSError) as e:
            logger.error("Error triggering job %s for service %s: %s", 
                        job_id, self.service_name, str(e))
            return False
    
    async def enable_job(self, job_id: str) -> bool:
        """Enable a job"""
        try:
            if job_id in self._registered_jobs:
                # Update in-memory state
                self._registered_jobs[job_id].enabled = True
                
                # Add to scheduler if running
                if self._is_running:
                    self._add_job_to_scheduler(self._registered_jobs[job_id])
                
                # Update database if repository is available
                if self.job_data_repository:
                    try:
                        await self.job_data_repository.update_job_enabled_status(job_id, True)
                        logger.info("Updated job %s enabled status to True in database", job_id)
                    except Exception as db_error:
                        logger.warning("Failed to update job %s in database: %s", job_id, db_error)
                
                logger.info("Enabled job: %s for service: %s", job_id, self.service_name)
                return True
            return False
        except (AttributeError, KeyError, RuntimeError) as e:
            logger.error("Error enabling job %s for service %s: %s", 
                        job_id, self.service_name, str(e))
            return False
    
    async def disable_job(self, job_id: str) -> bool:
        """Disable a job"""
        try:
            if job_id in self._registered_jobs:
                # Update in-memory state
                self._registered_jobs[job_id].enabled = False
                
                # Remove from scheduler if running
                if self._is_running:
                    self.scheduler.remove_job(job_id)
                
                # Update database if repository is available
                if self.job_data_repository:
                    try:
                        await self.job_data_repository.update_job_enabled_status(job_id, False)
                        logger.info("Updated job %s enabled status to False in database", job_id)
                    except Exception as db_error:
                        logger.warning("Failed to update job %s in database: %s", job_id, db_error)
                
                logger.info("Disabled job: %s for service: %s", job_id, self.service_name)
                return True
            return False
        except (AttributeError, KeyError, RuntimeError) as e:
            logger.error("Error disabling job %s for service %s: %s", 
                        job_id, self.service_name, str(e))
            return False
    
    def update_job_schedule(self, job_id: str, new_schedule: str) -> bool:
        """Update job schedule"""
        try:
            if job_id in self._registered_jobs:
                self._registered_jobs[job_id].schedule = new_schedule
                if self._is_running and self._registered_jobs[job_id].enabled:
                    self.scheduler.remove_job(job_id)
                    self._add_job_to_scheduler(self._registered_jobs[job_id])
                logger.info("Updated schedule for job: %s in service: %s", 
                           job_id, self.service_name)
                return True
            return False
        except (AttributeError, KeyError, RuntimeError, ValueError) as e:
            logger.error("Error updating schedule for job %s in service %s: %s", 
                        job_id, self.service_name, str(e))
            return False
    
    def is_running(self) -> bool:
        """Check if the scheduler is running"""
        return self._is_running
    
    def get_registered_jobs(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered jobs configuration"""
        try:
            jobs_config = {}
            for job_id, cron_job in self._registered_jobs.items():
                # Check if job is currently running in a thread
                is_running = False
                thread_name = None
                if self._thread_lock:
                    with self._thread_lock:
                        if job_id in self._active_threads:
                            thread = self._active_threads[job_id]
                            is_running = thread.is_alive()
                            thread_name = thread.name if is_running else None
                
                jobs_config[job_id] = {
                    'name': cron_job.name,
                    'schedule': cron_job.schedule,
                    'enabled': cron_job.enabled,
                    'timezone': cron_job.timezone,
                    'service': self.service_name,
                    'currently_running': is_running,
                    'thread_name': thread_name
                }
            return jobs_config
        except (AttributeError, KeyError, TypeError) as e:
            logger.error("Error getting registered jobs for service %s: %s", 
                        self.service_name, str(e))
            return {}
    
    def get_active_threads(self) -> Dict[str, Dict[str, Any]]:
        """Get information about currently active job threads"""
        try:
            active_threads = {}
            if self._thread_lock:
                with self._thread_lock:
                    for job_id, thread in self._active_threads.items():
                        if thread.is_alive():
                            active_threads[job_id] = {
                                'thread_name': thread.name,
                                'is_alive': thread.is_alive(),
                                'daemon': thread.daemon,
                                'job_name': self._registered_jobs.get(job_id, {}).get('name', 'Unknown')
                            }
            return active_threads
        except (AttributeError, KeyError, TypeError) as e:
            logger.error("Error getting active threads for service %s: %s", 
                        self.service_name, str(e))
            return {}
    
    def cleanup_finished_threads(self):
        """Clean up finished threads from tracking"""
        try:
            if self._thread_lock:
                with self._thread_lock:
                    finished_jobs = [job_id for job_id, thread in self._active_threads.items() 
                                   if not thread.is_alive()]
                    for job_id in finished_jobs:
                        del self._active_threads[job_id]
                    if finished_jobs:
                        logger.info("Cleaned up %d finished threads for service %s", 
                                  len(finished_jobs), self.service_name)
        except (AttributeError, KeyError, TypeError) as e:
            logger.error("Error cleaning up threads for service %s: %s", 
                        self.service_name, str(e))
