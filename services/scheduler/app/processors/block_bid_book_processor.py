"""
Block Bid Book Processor
Polls the IEX RTM Block Bid Book API every 15 minutes and syncs
RTMBlockBidDetails into rtm_block_bid_book_status.

Pattern mirrors BidTradeLiveSyncProcessor:
  1. Raw upsert into rtm_block_bid_book_status (keyed by OrderId)
  2. Secondary sync: update dicision_rtm_block_bid_iex_response
     with latest OrderStatus, biddedQty, clearedVolume, tradedPrice
"""
import logging
from typing import Dict, Any, List
from datetime import datetime

import pytz

from .base_processor import BaseProcessor
from services.scheduler.app.core.config import settings
from services.scheduler.app.services.scheduler_exchange_api_client import scheduler_exchange_client

logger = logging.getLogger(__name__)


class BlockBidBookProcessor(BaseProcessor):

    def __init__(self):
        super().__init__()
        self.client = scheduler_exchange_client
        self.timezone = pytz.timezone(settings.CRON_TIMEZONE)
        self.block_bid_book_collection = settings.MONGODB_RTM_BLOCK_BID_BOOK_COLLECTION
        from services.scheduler.app.core.database import get_database
        self.db = get_database()

    async def execute_cron_job(self) -> Dict[str, Any]:
        job_type   = "rtm_block_bid_book_sync"
        job_name   = "RTM Block Bid Book Sync"
        schedule   = "*/15 * * * *"
        start_time = datetime.now()

        execution_id = self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            return {"status": "error", "message": "Failed to record job start"}

        self.set_correlation_id(execution_id)

        try:
            logger.info("[RTM Block Bid Book Sync] Starting...")

            delivery_dates = await self.client.get_valid_delivery_date_rtm()
            logger.info(f"[RTM Block Bid Book Sync] DeliveryDates: {delivery_dates}")

            total_processed = 0
            results_details = []

            for delivery_date_int in delivery_dates:
                try:
                    data = await self.client.get_block_bid_rtm(delivery_date_int)
                    res  = await self._process_and_store(data, delivery_date_int)
                    total_processed += res.get("processed", 0)
                    results_details.append({"date": delivery_date_int, "result": res})
                except Exception as ex:
                    logger.error(f"[RTM Block Bid Book Sync] Error for date {delivery_date_int}: {ex}")
                    results_details.append({"date": delivery_date_int, "error": str(ex)})

            end_time = datetime.now()
            result = {
                "status":         "success",
                "processed":      total_processed,
                "details":        results_details,
                "execution_time": (end_time - start_time).total_seconds(),
                "job_type":       job_type,
                "correlation_id": execution_id,
            }

            self._record_job_completion(
                job_type=job_type, job_name=job_name, schedule=schedule,
                execution_start=start_time, execution_end=end_time,
                execution_id=execution_id, status="success", success=True, result=result,
            )
            return result

        except Exception as e:
            end_time = datetime.now()
            error_result = {
                "status":         "error",
                "message":        str(e),
                "execution_time": (end_time - start_time).total_seconds(),
                "job_type":       job_type,
                "correlation_id": execution_id,
            }
            self._record_job_completion(
                job_type=job_type, job_name=job_name, schedule=schedule,
                execution_start=start_time, execution_end=end_time,
                execution_id=execution_id, status="error", success=False,
                result=error_result, error_message=str(e),
            )
            logger.exception(f"[RTM Block Bid Book Sync] Error: {e}")
            return error_result
        finally:
            self.clear_correlation_id()

    async def process_raw_data_to_serve(self, raw_data, metadata, provider_config) -> bool:
        return True

    async def _process_and_store(self, data: Dict[str, Any], delivery_date_int: int) -> Dict[str, Any]:
        bid_details: List[Dict[str, Any]] = data.get("RTMBlockBidDetails", [])

        if not bid_details:
            logger.info(f"[RTM Block Bid Book Sync] No RTMBlockBidDetails for {delivery_date_int}")
            return {"processed": 0, "skipped": 0}

        logger.info(f"[RTM Block Bid Book Sync] {len(bid_details)} records for {delivery_date_int}")

        # Decimal factors — same pattern as BidTradeLiveSyncProcessor
        asset_details_resp = await scheduler_exchange_client.get_asset_details(market_type="rtm")
        asset_list = asset_details_resp.get("AssetDetails", [])
        if asset_list and isinstance(asset_list, list) and len(asset_list) > 0:
            details           = asset_list[0]
            orderQtyDecimal   = float(details.get("OrderQtyDecimal")   or 1.0)
            orderPriceDecimal = float(details.get("OrderPriceDecimal") or 1.0)
            tradeQtyDecimal   = float(details.get("TradeQtyDecimal")   or 1.0)
            tradePriceDecimal = float(details.get("TradePriceDecimal") or 1.0)
        else:
            orderQtyDecimal   = float(asset_details_resp.get("OrderQtyDecimal")   or 1.0)
            orderPriceDecimal = float(asset_details_resp.get("OrderPriceDecimal") or 1.0)
            tradeQtyDecimal   = float(asset_details_resp.get("TradeQtyDecimal")   or 1.0)
            tradePriceDecimal = float(asset_details_resp.get("TradePriceDecimal") or 1.0)

        logger.info(
            f"[RTM Block Bid Book Sync] Decimal factors: "
            f"OrderQty={orderQtyDecimal}, OrderPrice={orderPriceDecimal}, "
            f"TradeQty={tradeQtyDecimal}, TradePrice={tradePriceDecimal}"
        )

        def safe_float(val, default=0.0):
            if val is None:
                return default
            try:
                return float(str(val).replace(",", ""))
            except (ValueError, TypeError):
                return default

        timestamp = datetime.now()
        processed = 0
        skipped   = 0

        for bid in bid_details:
            # --- OrderId ---
            order_id_raw = bid.get("OrderId")
            if isinstance(order_id_raw, dict) and "$numberLong" in order_id_raw:
                order_id = str(order_id_raw["$numberLong"])
            else:
                order_id = str(order_id_raw) if order_id_raw is not None else None

            if not order_id:
                skipped += 1
                continue

            bid["OrderId"] = order_id

            # --- scheduled_at (YYYY-MM-DD) ---
            item_date = bid.get("DeliveryDate")
            if isinstance(item_date, (int, float)):
                bid["scheduled_at"] = datetime.fromtimestamp(item_date).strftime("%Y-%m-%d")
            elif isinstance(item_date, str):
                bid["scheduled_at"] = item_date
            else:
                bid["scheduled_at"] = datetime.fromtimestamp(delivery_date_int).strftime("%Y-%m-%d")

            # Convenience fields
            from_p = bid.get("FromPeriodId", "")
            to_p   = bid.get("ToPeriodId", "")
            bid["time_block"]   = f"{from_p}-{to_p}"
            bid["synced_at"]    = timestamp
            bid["market_type"]  = "rtm"

            # --- 1. Raw upsert into rtm_block_bid_book_status ---
            self.db[self.block_bid_book_collection].update_one(
                {"OrderId": order_id},
                {"$set": bid},
                upsert=True,
            )

            # --- 2. Secondary sync — update dicision_rtm_block_bid_iex_response ---
            order_status = bid.get("OrderStatus", "")
            quantity     = safe_float(bid.get("Quantity", 0))
            price        = safe_float(bid.get("Price", 0))

            self.db["dicision_rtm_block_bid_iex_response"].update_one(
                {"order_id": int(order_id)},
                {"$set": {
                    "status":                  order_status,
                    "exchangeOrderStatus":     order_status,
                    "exchangeLastUpdatedTime": bid.get("LastUpdatedTime", ""),
                    "biddedQty":               abs(quantity) / orderQtyDecimal,
                    "clearedVolume":           abs(quantity) / tradeQtyDecimal,
                    "tradedPrice":             price / tradePriceDecimal,
                    "synced_at":               timestamp,
                }},
            )

            processed += 1

        logger.info(f"[RTM Block Bid Book Sync] Done. processed={processed}, skipped={skipped}")
        return {"processed": processed, "skipped": skipped}


# Singleton
block_bid_book_processor = BlockBidBookProcessor()
