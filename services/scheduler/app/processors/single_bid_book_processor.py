"""
Single Bid Book Processor
Fetches Single Bid Book data from Exchange API and stores it in BookCollection.
"""

import logging
import asyncio
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
import pytz

from .base_processor import BaseProcessor
from services.scheduler.app.core.config import settings
from services.scheduler.app.services.scheduler_exchange_api_client import scheduler_exchange_client
from services.scheduler.app.repository.single_bid_book_repository import SingleBidBookRepository
from services.scheduler.app.repository.rtm_single_bid_approval_repository import RTMSingleBidDataForApprovalRepository
from services.scheduler.app.repository.dam_single_bid_approval_repository import DAMSingleBidDataForApprovalRepository


logger = logging.getLogger(__name__)

class SingleBidBookProcessor(BaseProcessor):
    """
    Processor to fetch and store Single Bid Book details.
    Uses SingleBidBookRepository for database operations.
    """
    
    def __init__(self):
        super().__init__()
        self.client = scheduler_exchange_client
        self.timezone = pytz.timezone(settings.CRON_TIMEZONE)
        
        # Initialize repositories
        # This will also ensure indexes
        self.dam_approval_repo = DAMSingleBidDataForApprovalRepository()
        self.rtm_approval_repo = RTMSingleBidDataForApprovalRepository()
        self.dam_repo = SingleBidBookRepository("dam")
        self.rtm_repo = SingleBidBookRepository("rtm")

    async def execute_dam_cron(self) -> Dict[str, Any]:
        """
        Execute DAM Single Bid Book sync.
        """
        job_type = "dam_book_sync"
        job_name = "DAM Single Bid Book Sync"
        schedule = "0 10 * * *"  # Typical DAM time
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            return {"status": "error", "message": "Failed to record job start"}

        self.set_correlation_id(execution_id)

        try:
            logger.info("[DAM Single Bid Book Sync] Starting...")
            
            # Fetch valid delivery date from API
            delivery_date_int = await self.client.get_valid_delivery_date_dam()
            
            logger.info(f"[DAM Single Bid Book Sync] Fetching for DeliveryDate: {delivery_date_int}")
            
            data = await self.client.get_single_bid_dam(delivery_date_int)
            result = await self._process_and_store(data, "dam", delivery_date_int)
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            result.update({
                "status": "success",
                "execution_time": execution_time,
                "job_type": job_type,
                "correlation_id": execution_id
            })

            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="success",
                success=True,
                result=result
            )

            return result
            
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": str(e),
                "execution_time": execution_time,
                "job_type": job_type,
                "correlation_id": execution_id
            }
            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="error",
                success=False,
                result=error_result,
                error_message=str(e)
            )
            logger.exception(f"[DAM Single Bid Book Sync] Error: {e}")
            return error_result
        finally:
            self.clear_correlation_id()

    async def execute_rtm_cron(self) -> Dict[str, Any]:
        """
        Execute RTM Single Bid Book sync.
        """
        job_type = "rtm_book_sync"
        job_name = "RTM Single Bid Book Sync"
        schedule = "*/15 * * * *"  # Every 15 minutes
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            return {"status": "error", "message": "Failed to record job start"}

        self.set_correlation_id(execution_id)

        try:
            logger.info("[RTM Single Bid Book Sync] Starting...")
            
            # Fetch valid delivery dates from API
            delivery_dates = await self.client.get_valid_delivery_date_rtm()
            
            logger.info(f"[RTM Single Bid Book Sync] Fetching for DeliveryDates: {delivery_dates}")
            
            total_processed = 0
            results_details = []
            
            for delivery_date_int in delivery_dates:
                try:
                    logger.info(f"[RTM Single Bid Book Sync] Processing DeliveryDate: {delivery_date_int}")
                    data = await self.client.get_single_bid_rtm(delivery_date_int)
                    res = await self._process_and_store(data, "rtm", delivery_date_int)
                    total_processed += res.get("processed", 0)
                    results_details.append({"date": delivery_date_int, "result": res})
                except Exception as ex:
                    logger.error(f"[RTM Single Bid Book Sync] Error processing date {delivery_date_int}: {ex}")
                    results_details.append({"date": delivery_date_int, "error": str(ex)})

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            result = {
                "status": "success",
                "processed": total_processed,
                "details": results_details,
                "execution_time": execution_time,
                "job_type": job_type,
                "correlation_id": execution_id
            }

            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="success",
                success=True,
                result=result
            )

            return result
            
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": str(e),
                "execution_time": execution_time,
                "job_type": job_type,
                "correlation_id": execution_id
            }
            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="error",
                success=False,
                result=error_result,
                error_message=str(e)
            )
            logger.exception(f"[RTM Single Bid Book Sync] Error: {e}")
            return error_result
        finally:
            self.clear_correlation_id()

    async def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Implementation of abstract method from BaseProcessor.
        SingleBidBookProcessor does not process raw data documents in this way
        as it fetches data directly from the exchange API.
        """
        return True

    async def _process_and_store(self, data: Dict[str, Any], market_type: str, delivery_date: int) -> Dict[str, Any]:
        """
        Extract details and Upsert into BookCollection using Repository.
        """
        # User confirmed key is 'SingleBidDetails' for both
        bid_details = data.get("SingleBidDetails", [])

        asset_details_resp = await scheduler_exchange_client.get_asset_details()
        
        # Extract Asset Details Logic
        asset_list = asset_details_resp.get("AssetDetails", [])
        if asset_list and isinstance(asset_list, list) and len(asset_list) > 0:
            details = asset_list[0]
            orderQtyDecimal = float(details.get("OrderQtyDecimal") or 1.0)
            orderPriceDecimal = float(details.get("OrderPriceDecimal") or 1.0)
            tradeQtyDecimal = float(details.get("TradeQtyDecimal") or 1.0)
            tradePriceDecimal = float(details.get("TradePriceDecimal") or 1.0)
        else:
            # Fallback to direct access or default
            orderQtyDecimal = float(asset_details_resp.get("OrderQtyDecimal") or 1.0) 
            orderPriceDecimal = float(asset_details_resp.get("OrderPriceDecimal") or 1.0)
            tradeQtyDecimal = float(asset_details_resp.get("TradeQtyDecimal") or 1.0)
            tradePriceDecimal = float(asset_details_resp.get("TradePriceDecimal") or 1.0)

        logger.info(f"[{market_type.upper()} Book Sync] Using Decimal Factors: OrderQty={orderQtyDecimal}, OrderPrice={orderPriceDecimal}, TradeQty={tradeQtyDecimal}, TradePrice={tradePriceDecimal}")       
        # Checking for RTMBlockBidDetails fallback just in case

        if not bid_details:
            logger.info(f"[{market_type.upper()} Book Sync] No bid details found.")
            return {"success": True, "processed": 0}
            
        logger.info(f"[{market_type.upper()} Book Sync] Found {len(bid_details)} bids.")
        if market_type == "dam":
            self.approval_repo = self.dam_approval_repo
            self.book_repo = self.dam_repo
        else:
            self.approval_repo = self.rtm_approval_repo
            self.book_repo = self.rtm_repo

        
        # Prepare docs for upsert
        docs_to_upsert = []
        timestamp = datetime.now()
        
        for bid in bid_details:
            # Ensure OrderId present. If missing, skip.
            order_id = bid.get("OrderId")
            scheduled_at = bid.get("DeliveryDate")
            scheduled_at = datetime.strptime(scheduled_at, "%Y-%m-%d")
            if order_id is None:
                logger.warning(f"[{market_type.upper()} Book Sync] Skipping bid with missing OrderId: {bid}")
                continue

            # Enrich doc
            doc = bid.copy()
            doc["market_type"] = market_type
            doc["synced_at"] = timestamp
            doc["scheduled_at"] = scheduled_at
            
            # Ensure DeliveryDate if missing
            if "DeliveryDate" not in doc:
                doc["DeliveryDate"] = delivery_date
            
            docs_to_upsert.append(doc)
            
        if docs_to_upsert:
            # Select correct repository
            orderIds = [doc["OrderId"] for doc in docs_to_upsert]
            repo = self.dam_repo if market_type.lower() == "dam" else self.rtm_repo
            
            # Use repository driven upsert
            # This handles int validation on OrderId internally
            result_stats = repo.upsert_bids(docs_to_upsert)
            
            logger.info(f"[{market_type.upper()} Book Sync] Bulk Upsert Result: {result_stats}")
            # Do not return here, continue to update approval status

            # Create a lookup map for faster access
            docs_map = {doc["OrderId"]: doc for doc in docs_to_upsert}
        
            # Find docs in approval collection that match the OrderIds
            # find_many is synchronous in BaseRepository
            get_docs_to_update_in_approval = self.approval_repo.find_many({"OrderId": {"$in": orderIds}}, limit=10000)
            if get_docs_to_update_in_approval:
                update_count = 0
                for doc in get_docs_to_update_in_approval:
                    order_id = doc.get("OrderId")
                    if order_id in docs_map:
                        doc_to_upsert = docs_map[order_id]
                        user_id = doc_to_upsert.get("UserId")
                        user_id = f"Exchange-{user_id}" 
                        bidded_qty = 0.0
                        pq_data = doc_to_upsert.get("PQData")
                        buy_sell = doc_to_upsert.get("BuySell")

                        if pq_data:
                            if buy_sell == "S":
                                target_entry = max(pq_data, key=lambda x: x.get("Price", 0))
                                bidded_qty = float(target_entry.get("Quantity", 0))
                            elif buy_sell == "B":
                                target_entry = min(pq_data, key=lambda x: x.get("Price", float("inf")))
                                bidded_qty = float(target_entry.get("Quantity", 0))

                        # Update fields based on Exchange data
                        updates = {
                            "status": doc_to_upsert.get("OrderStatus"),
                            "biddedQty": bidded_qty / orderQtyDecimal,
                            "clearedVolume": doc_to_upsert.get("TotalExecutedQty") / tradeQtyDecimal,
                            "tradedPrice": doc_to_upsert.get("TradePrice") / tradePriceDecimal,
                            "exchangeOrderStatus": doc_to_upsert.get("OrderStatus"),
                            "exchangeError": doc_to_upsert.get("Error"),
                            "exchangeLastUpdatedTime": doc_to_upsert.get("LastUpdatedTime"),
                            user_id: doc_to_upsert.get("PQData"),
                            "synced_at": timestamp
                        }
                        
                        # Perform upsert using PyMongo directly (BaseRepository has no single-doc upsert)
                        await self.approval_repo.collection.update_one(
                            {"OrderId": order_id},
                            {"$set": updates},
                            upsert=True
                        )
                        update_count += 1
                
                logger.info(f"[{market_type.upper()} Book Sync] Updated {update_count} approval documents with exchange status.")

            return {"success": True, "processed": len(docs_to_upsert), "db_stats": result_stats}
            
        return {"success": True, "processed": 0}

# Singleton instance
single_bid_book_processor = SingleBidBookProcessor()
