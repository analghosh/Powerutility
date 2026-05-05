from libs.database.base_repository import BaseRepository
from services.scheduler.app.models.job_detail_entity import JobDetail
from libs.core.job_factory import create_job_data_repository, create_job_detail_repository
from ..core.config import settings

# Create repository instances
job_data_repository = create_job_data_repository(
    settings.AUDIT_DB_URI,
    settings.AUDIT_DB_NAME,
    settings.JOB_DATA_COLLECTION
)
job_detail_repository = create_job_detail_repository(
    settings.AUDIT_DB_URI,
    settings.AUDIT_DB_NAME,
    settings.JOB_DETAIL_COLLECTION
)


import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from bson import ObjectId

logger = logging.getLogger(__name__)

class DateUpdate:
    def __init__(self):
        self.demand_repo = BaseRepository(
            collection_name=settings.DEMAND_COLLECTION,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

        self.demand_historical_repo = BaseRepository(
            collection_name=settings.DEMAND_HISTORICAL_COLLECTION,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

        self.supply_repo = BaseRepository(
            collection_name=settings.SUPPLY_COLLECTION,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

        self.supply_historical_repo = BaseRepository(
            collection_name=settings.SUPPLY_HISTORICAL_COLLECTION,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

        self.px_bid_table_repo = BaseRepository(
            collection_name=settings.PX_BID_TABLE,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

        self.exchange_table_repo = BaseRepository(
            collection_name=settings.EXCHANGE_TABLE,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )


    async def date_update_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute the date update job with detailed logging and error handling"""
        job_type = "date_update"
        start_time = datetime.now()
        
        # Get job data to retrieve payload and other configuration
        job_data = await job_data_repository.get_job(job_type)
        if not job_data:
            logger.error("Job data not found for job_type: %s", job_type)
            return
        
        # Extract job details from job_data instead of hardcoding
        job_name = job_data.get('name', 'Date Update for Scheduled Entries')
        schedule = job_data.get('schedule', '0 0 * * *')  # Default to daily at midnight if not found
        payload = job_data.get('payload', payload)
        
        logger.info("Starting %s cron job at %s", job_type, start_time.strftime('%Y-%m-%d %H:%M:%S'))
        if payload:
            logger.info("Job payload: %s", payload)
        
        # Record job start - pass job_data to avoid redundant DB call
        execution_id = await self._record_job_start(job_data, start_time)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return
        
        try:
            # Execute scraping job directly with payload
            dateUpdate = await self.executeUpdateJob(payload=payload, manual=False)
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            result = {
                'status': dateUpdate.get('status', 'unknown'),
                'execution_time_seconds': execution_time,
                'message': 'Date update job completed',
                'scrape_details': dateUpdate
            }
            
            # Log detailed results
            logger.info("Execution results for %s:", job_type)
            logger.info("   - Status: %s", result.get('status', 'unknown'))
            logger.info("   - Execution time: %.2f seconds", execution_time)
            
            success = result.get('status') == 'success'
            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status=result.get('status', 'unknown'),
                success=success,
                result=result
            )
            
            if result['status'] == 'success':
                logger.info("Completed %s successfully in %.2fs", job_type, execution_time)
            else:
                logger.error("Failed %s after %.2fs", job_type, execution_time)
                
        except Exception as e:  # pylint: disable=broad-except
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {'error': str(e), 'execution_time': execution_time}
            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status='error',
                success=False,
                result=error_result,
                error_message=str(e)
            )
            logger.error("Error executing %s after %.2fs: %s", job_type, execution_time, str(e))
            logger.exception("Full exception details for %s:", job_type)



    async def executeUpdateJob(self, payload: Optional[Dict[str, Any]] = None, manual: bool = False) -> Dict[str, Any]:
        try:
            await self.update_demand_date()
            await self.update_supply_date()
            await self.update_px_bid_table()
            await self.update_exchange_table()
            return {'status': 'success', 'details': 'All date updates completed successfully'}
        except Exception as e:
            logger.error("Error in executeUpdateJob: %s", str(e))
            return {'status': 'error', 'details': str(e)}

    async def _record_job_start(self, job_data: Dict[str, Any], execution_start: datetime) -> Optional[str]:
        """Record job start in audit database and return execution_id"""
        try:        
            # Extract details from the already-fetched job_data
            job_id = job_data.get('_id')
            job_type = job_data.get('job_type', '')
            job_name = job_data.get('name', 'Unknown Job')  # Provide default to avoid None
            schedule = job_data.get('schedule', '')
            
            # Generate unique execution ID
            execution_id = f"{job_type}_{execution_start.strftime('%Y%m%d_%H%M%S')}"
            
            job_detail = JobDetail(
                job_id=str(job_id),  # Convert ObjectId to string for storage
                execution_id=execution_id,
                job_name=job_name,
                execution_start=execution_start,
                execution_end=execution_start,  # Same as start for processing entry
                duration_seconds=0,  # Will be updated on completion
                status="processing",
                success=False,  # Default to False for processing entry
                error_message=None,
                error_details=None,
                result_data={},
                service="scheduler",
                metadata={
                    "timezone": settings.CRON_TIMEZONE,
                    "schedule": schedule,
                    "job_type": job_type,  # Store job_type for easy querying
                    "processing_started": True
                }
            )
            
            await job_detail_repository.log_execution(job_detail)
            logger.info("Recorded job start for %s (job_type: %s, execution_id: %s) in audit database", 
                       job_id, job_type, execution_id)
            return execution_id
                
        except Exception as e:
            logger.error("Failed to record job start for %s: %s", job_data.get('job_type', 'unknown'), str(e))
            return None

    async def _record_job_completion(self, job_type: str, job_name: str, schedule: str, 
                              execution_start: datetime, execution_end: datetime,
                              execution_id: str, status: str, success: bool, 
                              result: Optional[Dict[str, Any]] = None,
                              error_message: Optional[str] = None):
        """Record job completion in audit database"""
        try:
            duration_seconds = (execution_end - execution_start).total_seconds()
            
            # Get the job_id (ObjectId) from job_data collection using job_type
            
            job_data = await job_data_repository.get_job(job_type)
            if not job_data:
                logger.error("Job data not found for job_type: %s", job_type)
                return
            
            job_id = job_data.get('_id')  # This is the ObjectId reference
            
            job_detail = JobDetail(
                job_id=str(job_id),  # Convert ObjectId to string for storage
                execution_id=execution_id,  # Use the same execution_id from start
                job_name=job_name,
                execution_start=execution_start,
                execution_end=execution_end,
                duration_seconds=duration_seconds,
                status=status,
                success=success,
                error_message=error_message,
                error_details=None,
                result_data=result or {},
                service="scheduler",
                metadata={
                    "timezone": settings.CRON_TIMEZONE,
                    "schedule": schedule,
                    "job_type": job_type,  # Store job_type for easy querying
                    "processing_completed": True,
                    "final_status": status
                }
            )
            
            await job_detail_repository.log_execution(job_detail)
            logger.info("Recorded job completion for %s (job_type: %s, execution_id: %s, status: %s) in audit database", 
                       job_id, job_type, execution_id, status)
                
        except Exception as e:  # pylint: disable=broad-except
            logger.error("Failed to record job completion for %s: %s", job_type, str(e))

    async def update_demand_date(self):
        today = datetime.now().date()
        tommorow = today + timedelta(days=1)
        day_before_yesterday = today - timedelta(days=2)
        day_before_yesterday_str = day_before_yesterday.isoformat()

        existing_data_count = await self.demand_repo.count({})

        create_data = []

        tommorow_data = await self.demand_repo.find_one(filter={"schedule_date": tommorow.isoformat()})

        if tommorow_data:
            logger.info(f"Found {tommorow_data} documents already scheduled for {tommorow.isoformat()}")
        
        else:
            existing_data = await self.demand_repo.find_many(filter={"schedule_date": {"$lt": day_before_yesterday_str}}, limit=existing_data_count)
            if len(existing_data) > 0:
                create_data = existing_data
                for data in create_data:
                    data["schedule_date"] = tommorow.isoformat()

                await self.demand_repo.bulk_upsert(
                    create_data,
                    filter_criteria=lambda doc: {"_id": doc["_id"]}
                )
            else:
                logger.info("No demand documents found to update")

            # Strip _id from data and insert into historical collection
            if len(existing_data)> 0:
                historical_data = []
                for doc in existing_data:
                    doc_copy = doc.copy()
                    doc_copy.pop('_id', None)  # Remove _id field
                    historical_data.append(doc_copy)

                await self.demand_historical_repo.collection.insert_many(historical_data)
                logger.info(f"Inserted {len(historical_data)} documents into demand historical collection")
            else:
                logger.info("No demand documents found to archive")

    async def update_supply_date(self):
        today = datetime.now().date()
        tommorow = today + timedelta(days=1)
        day_before_yesterday = today - timedelta(days=2)
        day_before_yesterday_str = day_before_yesterday.isoformat()
        existing_data_count = await self.supply_repo.count({})

        tommorow_data = await self.supply_repo.find_one(filter={"schedule_date": tommorow.isoformat()})
        if tommorow_data :
            logger.info(f"Found {tommorow_data} documents already scheduled for {tommorow.isoformat()}")

        else:
            existing_data = await self.supply_repo.find_many(filter={"schedule_date": {"$lt": day_before_yesterday_str}}, limit=existing_data_count)
            logger.info(f"Found {len(existing_data)} documents matching the filter")

            if len(existing_data) > 0:
                create_data = existing_data.copy()
                logger.info(f"Updating {len(existing_data)} supply documents")
                for data in create_data:
                    data["schedule_date"] = tommorow.isoformat()

                await self.supply_repo.bulk_upsert(
                    create_data,
                    filter_criteria=lambda doc: {"_id": doc["_id"]}
                )
            else:
                logger.info("No supply documents found to update")

            # Strip _id from data and insert into historical collection
            if len(existing_data) > 0:
                historical_data = []
                for doc in existing_data:
                    doc_copy = doc.copy()
                    doc_copy.pop('_id', None)  # Remove _id field
                    historical_data.append(doc_copy)

                await self.supply_historical_repo.collection.insert_many(historical_data)
                logger.info(f"Inserted {len(historical_data)} documents into supply historical collection")
            else:
                logger.info("No supply documents found to archive")

    async def update_px_bid_table(self):
        
        today = datetime.now().date()
        tommorow = today + timedelta(days=1)
        day_before_yesterday = today - timedelta(days=2)
        day_before_yesterday_str = day_before_yesterday.isoformat()
        existing_data_count = await self.px_bid_table_repo.count({})

        tomorrow_data = await self.px_bid_table_repo.find_one(filter={"schedule_date": tommorow.isoformat()})
        if tomorrow_data:
            logger.info(f"Found {tomorrow_data} documents already scheduled for {tommorow.isoformat()}")
        else:
            existing_data = await self.px_bid_table_repo.find_many(filter={"schedule_date": {"$lt": day_before_yesterday_str}}, limit=existing_data_count)
            if len(existing_data) > 0:
                for data in existing_data:
                    data["schedule_date"] = tommorow.isoformat()

                await self.px_bid_table_repo.bulk_upsert(
                    existing_data,
                    filter_criteria=lambda doc: {"_id": doc["_id"]}
                )
            else:
                logger.info("No px_bid_table documents found to update")

    async def update_exchange_table(self):
        
        today = datetime.now().date()
        tommorow = today + timedelta(days=1)
        day_before_yesterday = today - timedelta(days=2)
        day_before_yesterday_str = day_before_yesterday.isoformat()

        existing_data_count = await self.exchange_table_repo.count({})

        tomorrow_data = await self.exchange_table_repo.find_one(filter={"schedule_date": tommorow.isoformat()})
        if tomorrow_data:
            logger.info(f"Found {tomorrow_data} documents already scheduled for {tommorow.isoformat()}")
        else:
            existing_data = await self.exchange_table_repo.find_many(filter={"schedule_date": {"$lt": day_before_yesterday_str}}, limit=existing_data_count)
            if len(existing_data) > 0:
                for data in existing_data:
                    data["schedule_date"] = tommorow.isoformat()
    
                await self.exchange_table_repo.bulk_upsert(
                    existing_data,
                    filter_criteria=lambda doc: {"_id": doc["_id"]}
                )
            else:
                logger.info("No exchange_table documents found to update")
