"""
Base Cron Job Seeder
Base class for service-specific job seeders
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod

from .job_entities import JobData
from .job_repositories import JobDataRepository

logger = logging.getLogger(__name__)


class BaseJobSeeder(ABC):
    """Base class for service-specific job seeders"""
    
    def __init__(self, job_data_repository: JobDataRepository, service_name: str):
        """
        Initialize the job seeder
        
        Args:
            job_data_repository: Repository for job data operations
            service_name: Name of the service (scheduler, ingestion, etc.)
        """
        # Validate that repository is properly initialized
        if not hasattr(job_data_repository, 'collection'):
            raise ValueError("JobDataRepository not properly initialized")
        
        self.job_data_repository = job_data_repository
        self.service_name = service_name
    
    @abstractmethod
    def get_default_jobs(self) -> List[Dict[str, Any]]:
        """
        Get default jobs for this service
        This method must be implemented by each service
        
        Returns:
            List of job configurations
        """
        pass
    
    async def seed_default_jobs(self) -> Dict[str, Any]:
        """Seed default cron jobs for this service"""
        
        default_jobs = self.get_default_jobs()
        
        # Add service name to all jobs
        for job_config in default_jobs:
            job_config["service"] = self.service_name
        
        results = {
            "service": self.service_name,
            "created": 0,
            "skipped": 0,
            "failed": 0,
            "errors": []
        }
        
        for job_config in default_jobs:
            try:
                result = await self.seed_job(job_config)
                if result == "created":
                    results["created"] += 1
                elif result == "skipped":
                    results["skipped"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(f"Failed to seed {job_config['job_type']}")
                    
            except (ValueError, TypeError, OSError) as e:
                results["failed"] += 1
                results["errors"].append(f"Error seeding {job_config['job_type']}: {str(e)}")
                logger.error("Error seeding job %s for service %s: %s", 
                           job_config["job_type"], self.service_name, str(e))
        
        logger.info("Seeding completed for service %s: %s created, %s skipped, %s failed", 
                   self.service_name, results["created"], results["skipped"], results["failed"])
        
        return results
    
    async def seed_job(self, job_config: Dict[str, Any]) -> str:
        """
        Seed a single job if it doesn't exist
        
        Args:
            job_config: Job configuration dictionary
            
        Returns:
            str: "created", "skipped", or "failed"
        """
        try:
            existing_job = await self.job_data_repository.get_job(job_config["job_type"])
            
            if existing_job:
                logger.info("Job %s already exists for service %s, skipping", 
                           job_config["job_type"], self.service_name)
                return "skipped"
            
            # Create new job
            job_data = JobData(
                job_type=job_config["job_type"],
                name=job_config["name"],
                type=job_config["type"],
                schedule=job_config["schedule"],
                enabled=job_config["enabled"],
                timezone=job_config["timezone"],
                description=job_config.get("description"),
                priority=job_config.get("priority", 1),
                payload=job_config.get("payload", {}),
                metadata=job_config.get("metadata"),
                service=job_config["service"],
                version=1,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            
            job_id = await self.job_data_repository.create_job(job_data)
            logger.info("Seeded job: %s (ID: %s, Service: %s)", 
                       job_config["name"], job_id, job_config["service"])
            return "created"
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to seed job %s for service %s: %s", 
                        job_config["job_type"], self.service_name, str(e))
            return "failed"
    
    async def seed_custom_jobs(self, custom_jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Seed custom jobs provided by user
        
        Args:
            custom_jobs: List of custom job configurations
            
        Returns:
            Dict with seeding results
        """
        results = {
            "service": self.service_name,
            "created": 0,
            "skipped": 0,
            "failed": 0,
            "errors": []
        }
        
        # Add service name to all custom jobs
        for job_config in custom_jobs:
            job_config["service"] = self.service_name
        
        for job_config in custom_jobs:
            try:
                result = await self.seed_job(job_config)
                if result == "created":
                    results["created"] += 1
                elif result == "skipped":
                    results["skipped"] += 1
                else:
                    results["failed"] += 1
                    results["errors"].append(f"Failed to seed {job_config['job_type']}")
                    
            except (ValueError, TypeError, OSError) as e:
                results["failed"] += 1
                results["errors"].append(f"Error seeding {job_config['job_type']}: {str(e)}")
                logger.error("Error seeding custom job %s for service %s: %s", 
                           job_config["job_type"], self.service_name, str(e))
        
        logger.info("Custom job seeding completed for service %s: %s created, %s skipped, %s failed", 
                   self.service_name, results["created"], results["skipped"], results["failed"])
        
        return results
    
    def update_existing_jobs(self) -> Dict[str, Any]:
        """Update existing jobs with new configurations"""
        # This can be implemented later for configuration updates
        logger.info("Update existing jobs not implemented yet for service: %s", self.service_name)
        return {"service": self.service_name, "updated": 0, "errors": []}
    
    async def get_seeding_status(self) -> Dict[str, Any]:
        """Get current seeding status for this service"""
        try:
            total_jobs = await self.job_data_repository.get_job_count(service=self.service_name)
            enabled_jobs = await self.job_data_repository.get_job_count(enabled_only=True, service=self.service_name)
            
            return {
                "service": self.service_name,
                "total_jobs": total_jobs,
                "enabled_jobs": enabled_jobs,
                "disabled_jobs": total_jobs - enabled_jobs,
                "seeding_complete": total_jobs > 0
            }
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to get seeding status for service %s: %s", self.service_name, str(e))
            return {
                "service": self.service_name,
                "total_jobs": 0,
                "enabled_jobs": 0,
                "disabled_jobs": 0,
                "seeding_complete": False,
                "error": str(e)
            }


