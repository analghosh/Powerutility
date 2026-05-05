"""
Exchange Trade data processor for handling HPX exchange trade data processing logic.
"""

import logging
from datetime import datetime, date
from typing import Dict, List, Any, Optional

from .base_processor import BaseProcessor
from libs.models.exchange_trade_serve_entity import ExchangeTradeServeEntity, ExchangeTradeHistoricalEntity
from libs.repository.exchange_trade_repository import ExchangeTradeRepository
from libs.enum.updated_flag import UpdatedFlag

logger = logging.getLogger(__name__)

class ExchangeTradeProcessor(BaseProcessor):
    """
    Exchange trade data processor that handles exchange trade data processing and storage.
    """
    
    def __init__(self):
        super().__init__()
        self.repository = ExchangeTradeRepository()
        
        # Preload timeblock mappings for better performance using base processor's caching
        self.preload_timeblock_mappings()
        
        # Initialize job repositories for execution tracking
        from libs.core.job_factory import create_job_data_repository, create_job_detail_repository
        from ..core.config import settings
        
        self.job_data_repository = create_job_data_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DATA_COLLECTION
        )
        self.job_detail_repository = create_job_detail_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DETAIL_COLLECTION
        )

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute exchange trade data processing as a cron job with execution_id as correlation ID
        """
        job_type = "exchange_trade_processing"
        job_name = "Exchange Trade Data Processing"
        schedule = "*/15 * * * *"  # Default schedule
        start_time = datetime.now()
        
        # Generate and record job start to get execution_id (correlation ID)
        execution_id = self._record_job_execution(job_type, job_name, schedule, start_time, is_start=True)
        if not execution_id:
            logger.error(f"Failed to record job start for {job_type}")
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "data_type": "exchange_trade"
            }
        
        # Set execution_id as correlation ID using base processor method
        self.set_correlation_id(execution_id)
        
        logger.info("=== EXECUTING EXCHANGE TRADE DATA PROCESSING JOB ===")
        
        try:
            # Create metadata for exchange trade data processing with correlation ID
            metadata = {
                "data_type": "exchange_trade",
                "scraped_at": start_time,
                "source": "exchange_trade_processor_cron",
                "triggered_by": "cron_job",
                "execution_id": execution_id,
                "correlation_id": execution_id
            }
            
            # Process the metadata trigger with correlation ID context
            trigger_res = await self.process_metadata_trigger(metadata)
            success = trigger_res.get("success", False)
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            # Record job completion with correlation ID
            result = {
                "status": "success" if success else "error",
                "message": "Exchange Trade data processed successfully" if success else "Exchange Trade data processing failed",
                "execution_time": execution_time,
                "data_type": "exchange_trade",
                "correlation_id": execution_id,
                "cron_job": "exchange_trade_processing",
                "trigger_details": trigger_res
            }
            
            self._record_job_execution(
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
            
            if success:
                logger.info("Exchange Trade data processing completed successfully in %.2fs", execution_time)
            else:
                logger.error("Exchange Trade data processing failed after %.2fs", execution_time)
                
            return result
                
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Exchange trade data processing error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "exchange_trade",
                "correlation_id": execution_id
            }
            
            self._record_job_execution(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="error",
                success=False,
                result=error_result,
                error_message=str(e),
                is_start=False
            )
            
            logger.error("Error executing exchange trade data processing job: %s", str(e), exc_info=True)
            return error_result
        finally:
            # Clear correlation ID after job completion
            self.clear_correlation_id()

    async def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Process raw data and convert to exchange trade serve entities for storage using Bulk Operations.
        """
        try:
            if not self._validate_raw_data(raw_data):
                return False
            
            logger.info("Processing exchange trade records (Bulk Mode)")
            
            # Extract config
            config = provider_config.get("metadata", {}).get("config", {})
            market_type = config.get("market_type", "UNKNOWN").upper()
            px_name = provider_config.get("metadata", {}).get("source", "HPX").upper()
            
            exchange_entities = []
            historical_entities = []
            
            for table_data in raw_data: # In case the raw payload has a wrapper
                 # HPX raw data usually has a "Table" key based on User request
                 if "Table" in table_data:
                     records = table_data["Table"]
                 else:
                     # If the list itself is the records
                     records = [table_data]
                 
                 for item in records:
                    if not isinstance(item, dict):
                        continue
                    
                    try:
                        scheduled_at_raw = item.get("DelDate")
                        if not scheduled_at_raw:
                            continue
                            
                        # process date
                        try:
                            scheduled_date = datetime.strptime(scheduled_at_raw, "%Y-%m-%d").date()
                        except ValueError:
                            logger.error(f"Invalid date format in ExchangeTradeData: {scheduled_at_raw}")
                            continue

                        # Extract time_block
                        time_block = int(item.get("TimeBlock", 0))
                        if time_block == 0:
                            continue

                        # Add mapped date and block to item for from_raw_data
                        item["scheduled_at"] = scheduled_date
                        
                        # Calculate date difference for routing based on 4-day window logic
                        today_date = datetime.now().date()
                        day_diff = (today_date - scheduled_date).days
                        
                        entity = ExchangeTradeServeEntity.from_raw_data(item, market_type, px_name)
                        
                        if day_diff >= 3:
                            # Historical only (Older than 2 days before today)
                            hist_entity = ExchangeTradeHistoricalEntity(**entity.model_dump())
                            historical_entities.append(hist_entity)
                        elif day_diff == 1:
                            # Yesterday - both serve and historical
                            exchange_entities.append(entity)
                            hist_entity = ExchangeTradeHistoricalEntity(**entity.model_dump())
                            historical_entities.append(hist_entity)
                        else:
                            # Today, tomorrow, day before yesterday - serve only
                            exchange_entities.append(entity)
                            
                    except Exception as e:
                        logger.error(f"Error processing exchange trade item: {e}")
                        continue
            
            # Persist entities
            success = True
            if exchange_entities:
                entity_records = [e.model_dump_for_db() for e in exchange_entities]
                upsert_result = await self.repository.bulk_upsert_exchange_trade_records(entity_records)
                success = success and upsert_result.get("success", False)
                if not success:
                    logger.error(f"Bulk upsert exchange trade serve failed: {upsert_result.get('error')}")
                else:
                    logger.info(f"Successfully upserted {len(exchange_entities)} exchange trade records")

            if historical_entities:
                try:
                    await self.repository.bulk_insert_historical(historical_entities)
                    logger.info(f"Successfully inserted {len(historical_entities)} exchange trade historical records")
                except Exception as e:
                    logger.error(f"Bulk insert exchange trade historical failed: {e}")
                    success = False
            
            return success

        except Exception as e:
            logger.error("Error processing raw data to serve: %s", str(e), exc_info=True)
            return False

    def _validate_raw_data(self, raw_data: List[Dict[str, Any]]) -> bool:
        """Validate raw data input"""
        if not raw_data:
            logger.warning("No raw data provided for processing")
            return False

        if not isinstance(raw_data, list):
            logger.error("Raw data must be a list, got: %s", type(raw_data))
            return False
            
        return True
