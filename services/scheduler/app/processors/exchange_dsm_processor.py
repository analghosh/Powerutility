"""
Exchange DSM Processor
Processes exchange DSM raw data into serve and historical collections
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from libs.models.provider_models import DataType, IngestionType
from libs.repository.exchange_dsm_repository import ExchangeDsmRepository
from libs.models.exchange_dsm_serve_entity import ExchangeDsmServeEntity, ExchangeDsmHistoricalEntity
from services.scheduler.app.processors.base_processor import BaseProcessor
from services.scheduler.app.core.config import settings

logger = logging.getLogger(__name__)


class ExchangeDsmProcessor(BaseProcessor):
    """
    Processor for Exchange DSM data
    """

    def __init__(self):
        super().__init__()
        self.repository = ExchangeDsmRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute the processing job
        """
        job_type = "exchange_dsm_processing"
        job_name = "Exchange DSM Processing"
        schedule = settings.EXCHANGE_DSM_PROCESSING_CRON
        start_time = datetime.now()

        # Generate and record job start to get execution_id (correlation ID)
        execution_id = await self._record_job_execution(job_type, job_name, schedule, start_time, is_start=True)
        if not execution_id:
            logger.error(f"Failed to record job start for {job_type}")
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "data_type": "exchange_dsm"
            }

        # Set correlation ID
        self.set_correlation_id(execution_id)
        
        logger.info("=== EXECUTING EXCHANGE DSM DATA PROCESSING JOB ===")

        try:
            metadata = {
                "data_type": DataType.EXCHANGE_DSM.value,
                "scraped_at": start_time,
                "source": "exchange_dsm_processor_cron",
                "triggered_by": "cron_job",
                "execution_id": execution_id,
                "correlation_id": execution_id
            }

            # Process the metadata trigger
            trigger_res = await self.process_metadata_trigger(metadata)
            success = trigger_res.get("success", False)

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            result = {
                "status": "success" if success else "error",
                "message": "Exchange DSM data processed successfully" if success else "Exchange DSM data processing failed",
                "execution_time": execution_time,
                "data_type": "exchange_dsm",
                "correlation_id": execution_id,
                "trigger_details": trigger_res
            }

            await self._record_job_execution(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status=result["status"],
                success=success,
                result=result,
                is_start=False
            )

            return result

        except Exception as e:
            logger.error(f"Error executing exchange DSM job: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            self.clear_correlation_id()

    async def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Process raw data and convert to serve entities
        """
        try:
            if not raw_data:
                logger.warning("No raw data provided for processing")
                return True

            logger.info(f"Processing {len(raw_data)} raw records for Exchange DSM")

            # Provider configuration metadata
            # BaseProcessor already provides provider_config
            config = provider_config.get("metadata", {}).get("config", {})
            market_type = config.get("market_type", "UNKNOWN")
            px_name = config.get("px_name", "UNKNOWN")

            serve_entities = []
            historical_entities = []

            for raw_record in raw_data:
                try:
                    # In BaseProcessor's workflow, raw_data is the list of records 
                    # from a single document's 'raw_data' field.
                    # But for DSM, ingestion might store it under 'data' or the list itself.
                    # Let's handle both.
                    
                    items = []
                    if isinstance(raw_record, dict) and "data" in raw_record:
                        items = raw_record["data"]
                    elif isinstance(raw_record, dict):
                        items = [raw_record]
                    elif isinstance(raw_record, list):
                        items = raw_record
                    
                    for item in items:
                        # Create serve entity
                        serve_entity = ExchangeDsmServeEntity(
                            scheduled_at=item.get("scheduled_at"),
                            time_block=item.get("time_block"),
                            market_type=market_type,
                            px_name=px_name,
                            total_buy_cleared_vol=item.get("total_buy_cleared_vol", 0.0),
                            total_sell_cleared_vol=item.get("total_sell_cleared_vol", 0.0),
                            price=item.get("price", 0.0)
                        )
                        serve_entities.append(serve_entity)

                        # Create historical entity if it's for today or older
                        if serve_entity.scheduled_at.date() <= datetime.now().date():
                            historical_entity = ExchangeDsmHistoricalEntity(
                                **serve_entity.model_dump()
                            )
                            historical_entities.append(historical_entity)
                            
                except Exception as item_error:
                    logger.error(f"Error processing item: {item_error}")
                    continue

            # Bulk Upsert into Serve Collection
            success = True
            if serve_entities:
                records = [e.model_dump() for e in serve_entities]
                upsert_result = self.repository.bulk_upsert_optimized(
                    records=records,
                    unique_key_fields=["scheduled_at", "time_block", "market_type", "px_name"]
                )
                success = success and upsert_result.get("success", False)
                logger.info(f"Upserted records result: {upsert_result}")

            # Bulk Insert into Historical Collection
            if historical_entities:
                try:
                    self.repository.bulk_insert_historical(historical_entities)
                    logger.info(f"Inserted {len(historical_entities)} historical records")
                except Exception as hist_error:
                    logger.error(f"Error inserting historical records: {hist_error}")
                    # Don't fail the whole job for historical only? 
                    # For consistency with ExchangeTradeProcessor, we set success=False
                    success = False

            return success

        except Exception as e:
            logger.error(f"Failed to process raw data to serve: {e}", exc_info=True)
            return False
