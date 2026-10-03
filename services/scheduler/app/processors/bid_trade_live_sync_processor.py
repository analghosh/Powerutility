"""
Bid Trade Live Sync Processor
Fetches live bid trade data from DAM and RTM APIs and stores it in the bid_trade_serve collection.
"""

import logging
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, date, timedelta
import pytz

from .base_processor import BaseProcessor
from services.scheduler.app.core.config import settings
from services.scheduler.app.services.scheduler_exchange_api_client import scheduler_exchange_client
from services.scheduler.app.repository.bid_trade_serve_repository import BidTradeServeRepository
from libs.models.bid_trade_serve_entity import BidTradeServeEntity
import httpx

from libs.database.base_repository import BaseRepository
from libs.middleware.correlation import get_correlation_id
from services.scheduler.app.repository.rtm_single_bid_approval_repository import RTMSingleBidDataForApprovalRepository
from services.scheduler.app.repository.dam_single_bid_approval_repository import DAMSingleBidDataForApprovalRepository
from services.scheduler.app.repository.timeblock_repository import TimeblockRepository

logger = logging.getLogger(__name__)

class BidTradeLiveSyncProcessor(BaseProcessor):
    """
    Processor to sync live bid trade data from exchange.
    """
    
    def __init__(self):
        super().__init__()
        self.client = scheduler_exchange_client
        self.timezone = pytz.timezone(settings.CRON_TIMEZONE)
        self.bid_trade_serve_repo = BidTradeServeRepository()
        self.rtm_single_bid_approval_repo = RTMSingleBidDataForApprovalRepository()
        self.dam_single_bid_approval_repo = DAMSingleBidDataForApprovalRepository()
        self.gdam_single_bid_approval_repo = DAMSingleBidDataForApprovalRepository(settings.MONGODB_GDAM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION)
        self.hpdam_single_bid_approval_repo = DAMSingleBidDataForApprovalRepository(settings.MONGODB_HPDAM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION)
        self.time_block_cache = TimeblockRepository()
        self.dam_bid_data_live = "dam_bid_data_live"
        self.gdam_bid_data_live = "gdam_bid_data_live"
        self.hpdam_bid_data_live = "hpdam_bid_data_live"
        self.rtm_bid_data_live = "rtm_bid_data_live"
        from services.scheduler.app.core.database import get_database
        self.db = get_database()

        self._live_market_config = {
            "dam": {
                "get_delivery_dates": self.client.get_live_valid_delivery_date_dam,
                "get_single_bid": self.client.get_live_single_bid_dam,
                "approval_repo": self.dam_single_bid_approval_repo,
                "raw_collection": self.dam_bid_data_live,
                "is_multi_date": False,
            },
            "gdam": {
                "get_delivery_dates": self.client.get_live_valid_delivery_date_gdam,
                "get_single_bid": self.client.get_live_single_bid_gdam,
                "approval_repo": self.gdam_single_bid_approval_repo,
                "raw_collection": self.gdam_bid_data_live,
                "is_multi_date": False,
            },
            "hpdam": {
                "get_delivery_dates": self.client.get_live_valid_delivery_date_hpdam,
                "get_single_bid": self.client.get_live_single_bid_hpdam,
                "approval_repo": self.hpdam_single_bid_approval_repo,
                "raw_collection": self.hpdam_bid_data_live,
                "is_multi_date": False,
            },
            "rtm": {
                "get_delivery_dates": self.client.get_live_valid_delivery_date_rtm,
                "get_single_bid": self.client.get_live_single_bid_rtm,
                "approval_repo": self.rtm_single_bid_approval_repo,
                "raw_collection": self.rtm_bid_data_live,
                "is_multi_date": True,
            },
        }

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute the cron job for Bid Trade Live Sync.
        Runs DAM, GDAM, HPDAM and RTM syncs.
        """
        logger.info("TESTING LOGS: Starting Bid Trade Live Sync Cron Job")
        results = {}

        # DAM Sync
        logger.info("TESTING LOGS: [FLOW] 1. Starting DAM Sync")
        try:
            results["dam"] = await self.execute_dam_live_sync()
            logger.info("TESTING LOGS: [FLOW] 2. DAM Sync Finished")
        except Exception as e:
            logger.error(f"TESTING LOGS: [FLOW] 2. DAM Sync FAILED: {e}")
            results["dam"] = {"status": "error", "error": str(e)}

        # GDAM Sync
        logger.info("TESTING LOGS: [FLOW] 1b. Starting GDAM Sync")
        try:
            results["gdam"] = await self.execute_gdam_live_sync()
            logger.info("TESTING LOGS: [FLOW] 2b. GDAM Sync Finished")
        except Exception as e:
            logger.error(f"TESTING LOGS: [FLOW] 2b. GDAM Sync FAILED: {e}")
            results["gdam"] = {"status": "error", "error": str(e)}

        # HPDAM Sync
        logger.info("TESTING LOGS: [FLOW] 1c. Starting HPDAM Sync")
        try:
            results["hpdam"] = await self.execute_hpdam_live_sync()
            logger.info("TESTING LOGS: [FLOW] 2c. HPDAM Sync Finished")
        except Exception as e:
            logger.error(f"TESTING LOGS: [FLOW] 2c. HPDAM Sync FAILED: {e}")
            results["hpdam"] = {"status": "error", "error": str(e)}

        # RTM Sync
        logger.info("TESTING LOGS: [FLOW] 3. Starting RTM Sync")
        try:
             results["rtm"] = await self.execute_rtm_live_sync()
             logger.info("TESTING LOGS: [FLOW] 4. RTM Sync Finished")
        except Exception as e:
            logger.error(f"TESTING LOGS: [FLOW] 4. RTM Sync FAILED: {e}")
            results["rtm"] = {"status": "error", "error": str(e)}

        # RTM Block Bid Sync
        logger.info("TESTING LOGS: [FLOW] 5. Starting RTM Block Bid Sync")
        try:
            results["rtm_block_bid"] = await self.execute_rtm_block_bid_live_sync()
            logger.info("TESTING LOGS: [FLOW] 6. RTM Block Bid Sync Finished")
        except Exception as e:
            logger.error(f"TESTING LOGS: [FLOW] 6. RTM Block Bid Sync FAILED: {e}")
            results["rtm_block_bid"] = {"status": "error", "error": str(e)}

        return results

    async def execute_dam_live_sync(self) -> Dict[str, Any]:
        """
        Execute DAM live sync.
        """
        logger.info("TESTING LOGS: Entering execute_dam_live_sync")
        return await self._execute_sync("dam")

    async def execute_gdam_live_sync(self, delivery_date: Optional[int] = None) -> Dict[str, Any]:
        """
        Execute GDAM live sync.

        delivery_date is an IEX delivery-date timestamp. Omitted, the sync takes the
        date IEX currently offers (T+1); given, it fetches that date instead, which is
        how a day that has already traded can be pulled in again.
        """
        logger.info("TESTING LOGS: Entering execute_gdam_live_sync")
        return await self._execute_sync("gdam", delivery_date)

    async def execute_hpdam_live_sync(self, delivery_date: Optional[int] = None) -> Dict[str, Any]:
        """
        Execute HPDAM live sync.

        delivery_date is an IEX delivery-date timestamp; omitted, the sync takes the
        date IEX currently offers (T+1), as for GDAM.
        """
        logger.info("TESTING LOGS: Entering execute_hpdam_live_sync")
        return await self._execute_sync("hpdam", delivery_date)

    async def execute_rtm_live_sync(self) -> Dict[str, Any]:
        """
        Execute RTM live sync.
        """
        logger.info("TESTING LOGS: Entering execute_rtm_live_sync")
        return await self._execute_sync("rtm")

    async def _execute_sync(self, market: str, delivery_date: Optional[int] = None) -> Dict[str, Any]:
        """
        Common execution logic for sync.
        """
        logger.info(f"TESTING LOGS: Entering _execute_sync with market={market}")
        job_name = f"{market.upper()} Live Bid Trade Sync"
        cfg = self._live_market_config.get(market.lower())
        if not cfg:
            return {"status": "error", "message": f"Unsupported market: {market}"}

        try:
            logger.info(f"TESTING LOGS: [{job_name}] Starting execution...")

            # 1. Get Delivery Date(s)
            if delivery_date is not None:
                delivery_dates = [delivery_date]
            elif cfg["is_multi_date"]:
                delivery_dates = await cfg["get_delivery_dates"]()
            else:
                d_int = await cfg["get_delivery_dates"]()
                delivery_dates = [d_int]

            logger.info(f"TESTING LOGS: [{job_name}] Fetching for DeliveryDates: {delivery_dates}")

            results_details = []
            for delivery_date_int in delivery_dates:
                # 2. Fetch Data
                data = await cfg["get_single_bid"](delivery_date_int)

                # 3. Process and Store
                now = datetime.now().strftime("%H:%M:%S.%f")
                logger.info(f"TESTING LOGS: [{now}] [{job_name}] Processing date: {delivery_date_int}")
                logger.info(f"TESTING LOGS: [{now}] [{job_name}] BEFORE await _process_and_store")
                res = await self._process_and_store(data, market, delivery_date_int)
                now = datetime.now().strftime("%H:%M:%S.%f")
                logger.info(f"TESTING LOGS: [{now}] [{job_name}] AFTER await _process_and_store")
                results_details.append({"date": delivery_date_int, "result": res})

            return {
                "status": "success",
                "details": results_details
            }

        except Exception as e:
            logger.error(f"TESTING LOGS: [CRITICAL ERROR] [{job_name}] Exception: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "message": str(e)
            }


    async def process_raw_data_to_serve(self, raw_data, metadata, provider_config):
         # Not used for this processor style
         return True

    async def _process_and_store(self, data: Dict[str, Any], market: str, delivery_date_int: int) -> Dict[str, Any]:
        """
        Process the raw API response and store in bid_trade_serve.
        """
        now = datetime.now().strftime("%H:%M:%S.%f")
        logger.info(f"TESTING LOGS: [{now}] Started _process_and_store for {market.upper()}")
        
        bid_details = []
        try:
            bid_details = data.get("SingleBidDetails", [])
            if not bid_details and "RTMBlockBidDetails" in data:
                bid_details = data["RTMBlockBidDetails"]
            logger.info(f"TESTING LOGS: [{market.upper()}] Extracted {len(bid_details)} bid details")
        except Exception as e:
            logger.error(f"TESTING LOGS: [{market.upper()}] Error extracting details: {e}")
            return {"error": str(e)}

        if not bid_details:
            logger.info(f"TESTING LOGS: [{market.upper()}] No bid details found in API response - DONE.")
            return {"processed": 0}

        timeblocks = await self.fetch_timeblock_data()
        
        def normalize_time(t):
            if not t: return t
            parts = t.split(':')
            if len(parts) >= 2:
                h = parts[0].strip().zfill(2)
                m = parts[1].strip()[:2].zfill(2)
                return f"{h}:{m}"
            return t

        timeblock_map = {f"{normalize_time(tb['starttime'])}-{normalize_time(tb['endtime'])}": tb['timeblock'] for tb in timeblocks}
        market_cfg = self._live_market_config.get(market.lower())
        target_collection_name = market_cfg["raw_collection"] if market_cfg else f"{market.lower()}_bid_data_live"

        # Factors
        asset_details_response = await scheduler_exchange_client.get_live_asset_details(market)
        asset_list = asset_details_response.get("AssetDetails", [])
        if asset_list and isinstance(asset_list, list) and len(asset_list) > 0:
            details = asset_list[0]
            orderQtyDecimal = float(details.get("OrderQtyDecimal") or 1.0)
            orderPriceDecimal = float(details.get("OrderPriceDecimal") or 1.0)
            tradeQtyDecimal = float(details.get("TradeQtyDecimal") or 1.0)
            tradePriceDecimal = float(details.get("TradePriceDecimal") or 1.0)
        else:
            orderQtyDecimal = float(asset_details_response.get("OrderQtyDecimal") or 1.0) 
            orderPriceDecimal = float(asset_details_response.get("OrderPriceDecimal") or 1.0)
            tradeQtyDecimal = float(asset_details_response.get("TradeQtyDecimal") or 1.0)
            tradePriceDecimal = float(asset_details_response.get("TradePriceDecimal") or 1.0)

        def safe_float(val, default=0.0):
            if val is None: return default
            try: return float(str(val).replace(",", ""))
            except (ValueError, TypeError): return default

        aggregated = {}
        skipped_order_id = 0
        skipped_date = 0
        skipped_timeblock = 0
        
        for bid in bid_details:
            # --- 1. ID and Date Handling ---
            order_id_raw = bid.get("OrderId")
            if isinstance(order_id_raw, dict) and "$numberLong" in order_id_raw:
                order_id = str(order_id_raw["$numberLong"])
            else:
                order_id = str(order_id_raw) if order_id_raw is not None else None
            
            if not order_id:
                skipped_order_id += 1
                continue
            
            bid["OrderId"] = order_id
            
            item_date = bid.get("DeliveryDate")
            if isinstance(item_date, (int, float)):
                bid["scheduled_at"] = datetime.fromtimestamp(item_date)
            elif isinstance(item_date, str):
                try: 
                   bid["scheduled_at"] = datetime.strptime(item_date, "%Y-%m-%d")
                except (ValueError, TypeError):
                   logger.warning(f"Invalid DeliveryDate format for order {order_id}: {item_date}. Skipping.")
                   skipped_date += 1
                   continue
            else:
                logger.warning(f"Missing DeliveryDate from API for order {order_id}. Skipping.")
                skipped_date += 1
                continue

            bid_scheduled_at = bid["scheduled_at"]
            bid_delivery_dt_obj = datetime.combine(bid_scheduled_at.date(), datetime.min.time())
            bid_delivery_dt_str = bid_delivery_dt_obj.strftime("%Y-%m-%d")

            # --- 2. Raw Sync ---
            await self.db[target_collection_name].update_one({"OrderId": order_id}, {"$set": bid}, upsert=True)

            # --- 3. Secondary Sync (Approval Records) ---
            from_period = bid.get("FromPeriodId")
            to_period = bid.get("ToPeriodId")
            traded_qty = safe_float(bid.get("TotalExecutedQty", 0))
            trade_price = safe_float(bid.get("TradePrice", 0))

            approval_repo = market_cfg["approval_repo"] if market_cfg else self.dam_single_bid_approval_repo
            approval_query = {"start_time": from_period, "end_time": to_period, "bid_date": bid_delivery_dt_str, "exchangeStatus": "Submitted"}
            
            # Update approval if exists
            await approval_repo.collection.update_one(
                approval_query,
                {"$set": {
                    "clearedVolume": traded_qty / tradeQtyDecimal,
                    "tradedPrice": trade_price / tradePriceDecimal
                }}
            )

            # --- 4. Serve Aggregation (Only Executed) ---
            order_status = bid.get("OrderStatus")
            if not order_status or str(order_status).strip().lower() != "executed":
                continue
            
            time_block_key = f"{normalize_time(from_period)}-{normalize_time(to_period)}"
            time_block = timeblock_map.get(time_block_key)
            if not time_block:
                skipped_timeblock += 1
                continue

            buy_sell = bid.get("BuySell") or "B"
            pq_data = bid.get("PQData", [])
            order_bidded_qty = 0.0
            
            if pq_data:
                try:
                    if buy_sell == "S":
                        target_entry = max(pq_data, key=lambda x: safe_float(x.get("Price"), 0.0))
                        order_bidded_qty = safe_float(target_entry.get("Quantity", 0))
                        traded_qty = 0 - traded_qty 
                    elif buy_sell == "B":
                        target_entry = min(pq_data, key=lambda x: safe_float(x.get("Price"), float('inf')))
                        order_bidded_qty = safe_float(target_entry.get("Quantity", 0))
                except Exception as e:
                    logger.error(f"Error parsing PQData for order {order_id}: {e}")

            group = BidTradeServeEntity._map_type_to_group(market.upper())
            agg_key = (bid_delivery_dt_str, time_block, market.upper())

            aggregated[agg_key] = {
                "scheduled_at": bid_delivery_dt_obj,
                "time_block": time_block,
                "buy_sell_flag": buy_sell,
                "type": market.upper(),
                "bid_type": "single",
                "bidded": order_bidded_qty / orderQtyDecimal,
                "traded": traded_qty / tradeQtyDecimal,
                "value": trade_price / tradePriceDecimal,
                "count": 1,
                "group": group,
                "source": "live_sync",
                "order_id": order_id
            }

        # --- 5. Save Aggregated Serve Records ---
        entities_to_save = [BidTradeServeEntity(**data) for data in aggregated.values()]
        if entities_to_save:
            unique_key = ["scheduled_at", "time_block", "type", "group", "bid_type"]
            records_with_filters = [
                {"filter_query": {k: e.model_dump(by_alias=True).get(k) for k in unique_key}, 
                 "record_data": e.model_dump(by_alias=True)} 
                for e in entities_to_save
            ]
            result = await self.bid_trade_serve_repo.bulk_upsert_serve_records(records_with_filters)
            logger.info(f"TESTING LOGS: [{market.upper()}] Bulk upsert complete. Records: {result.get('total_count')}")
        
        logger.info(f"TESTING LOGS: [{market.upper()}] Process complete. Skipped: ID={skipped_order_id}, Date={skipped_date}, TB={skipped_timeblock}")
        return {"processed": len(entities_to_save)}

    async def execute_rtm_block_bid_live_sync(self) -> Dict[str, Any]:
        """
        Fetch live RTM block bid data and:
        1. Sync status/trade fields into dicision_rtm_block_bid_iex_response (keyed by order_id)
        2. Aggregate executed bids and upsert into bid_trade_serve with bid_type='block'
        """
        job_name = "RTM Block Bid Live Sync"
        logger.info(f"TESTING LOGS: [{job_name}] Starting...")

        try:
            delivery_dates = await self.client.get_live_valid_delivery_date_rtm()
            logger.info(f"TESTING LOGS: [{job_name}] DeliveryDates: {delivery_dates}")

            # Decimal factors for RTM
            asset_details_response = await scheduler_exchange_client.get_live_asset_details("rtm")
            asset_list = asset_details_response.get("AssetDetails", [])
            if asset_list and isinstance(asset_list, list) and len(asset_list) > 0:
                details = asset_list[0]
                orderQtyDecimal   = float(details.get("OrderQtyDecimal")   or 1.0)
                tradeQtyDecimal   = float(details.get("TradeQtyDecimal")   or 1.0)
                tradePriceDecimal = float(details.get("TradePriceDecimal") or 1.0)
            else:
                orderQtyDecimal   = float(asset_details_response.get("OrderQtyDecimal")   or 1.0)
                tradeQtyDecimal   = float(asset_details_response.get("TradeQtyDecimal")   or 1.0)
                tradePriceDecimal = float(asset_details_response.get("TradePriceDecimal") or 1.0)

            timeblocks = await self.fetch_timeblock_data()

            def normalize_time(t):
                if not t:
                    return t
                parts = t.split(':')
                if len(parts) >= 2:
                    h = parts[0].strip().zfill(2)
                    m = parts[1].strip()[:2].zfill(2)
                    return f"{h}:{m}"
                return t

            timeblock_map = {
                f"{normalize_time(tb['starttime'])}-{normalize_time(tb['endtime'])}": tb['timeblock']
                for tb in timeblocks
            }

            def safe_float(val, default=0.0):
                if val is None:
                    return default
                try:
                    return float(str(val).replace(",", ""))
                except (ValueError, TypeError):
                    return default

            total_processed = 0
            results_details = []

            for delivery_date_int in delivery_dates:
                try:
                    data = await self.client.get_live_block_bid_rtm(delivery_date_int)
                    bid_details = data.get("RTMBlockBidDetails", [])

                    if not bid_details:
                        logger.info(f"TESTING LOGS: [{job_name}] No RTMBlockBidDetails for {delivery_date_int}")
                        results_details.append({"date": delivery_date_int, "processed": 0})
                        continue

                    timestamp = datetime.now()
                    processed = 0
                    aggregated = {}

                    for bid in bid_details:
                        order_id_raw = bid.get("OrderId")
                        if isinstance(order_id_raw, dict) and "$numberLong" in order_id_raw:
                            order_id = str(order_id_raw["$numberLong"])
                        else:
                            order_id = str(order_id_raw) if order_id_raw is not None else None

                        if not order_id:
                            continue

                        order_status = bid.get("OrderStatus", "")
                        quantity     = safe_float(bid.get("Quantity", 0))
                        price        = safe_float(bid.get("Price", 0))

                        # --- 1. Sync into dicision_rtm_block_bid_iex_response ---
                        await self.db["dicision_rtm_block_bid_iex_response"].update_one(
                            {"order_id": int(order_id)},
                            {"$set": {
                                "status":                  order_status,
                                "exchangeOrderStatus":     order_status,
                                "exchangeLastUpdatedTime": bid.get("LastUpdatedTime", ""),
                                "biddedQty":               abs(quantity) / orderQtyDecimal,
                                "clearedVolume":           abs(quantity) / tradeQtyDecimal,
                                "tradedPrice":             price / tradePriceDecimal,
                                "synced_at":               timestamp,
                            }}
                        )
                        processed += 1

                        # --- 2. Serve aggregation (only Executed bids) ---
                        if str(order_status).strip().lower() != "executed":
                            continue

                        item_date = bid.get("DeliveryDate")
                        if isinstance(item_date, (int, float)):
                            bid_date_obj = datetime.fromtimestamp(item_date)
                        elif isinstance(item_date, str):
                            try:
                                bid_date_obj = datetime.strptime(item_date, "%Y-%m-%d")
                            except (ValueError, TypeError):
                                continue
                        else:
                            bid_date_obj = datetime.fromtimestamp(delivery_date_int)

                        bid_delivery_dt_obj = datetime.combine(bid_date_obj.date(), datetime.min.time())
                        bid_delivery_dt_str = bid_delivery_dt_obj.strftime("%Y-%m-%d")

                        from_period = bid.get("FromPeriodId", "")
                        to_period   = bid.get("ToPeriodId", "")
                        time_block_key = f"{normalize_time(from_period)}-{normalize_time(to_period)}"
                        time_block = timeblock_map.get(time_block_key)
                        if not time_block:
                            continue

                        buy_sell = bid.get("BuySell") or "B"
                        bidded_qty = abs(quantity) / orderQtyDecimal
                        traded_qty = abs(quantity) / tradeQtyDecimal
                        if buy_sell == "S":
                            traded_qty = 0 - traded_qty

                        group = BidTradeServeEntity._map_type_to_group("RTM")
                        agg_key = (bid_delivery_dt_str, time_block)

                        aggregated[agg_key] = {
                            "scheduled_at": bid_delivery_dt_obj,
                            "time_block": time_block,
                            "buy_sell_flag": buy_sell,
                            "type": "RTM",
                            "bid_type": "block",
                            "bidded": bidded_qty,
                            "traded": traded_qty,
                            "value": price / tradePriceDecimal,
                            "group": group,
                            "source": "live_sync",
                            "order_id": order_id
                        }

                    # --- 3. Save aggregated block bid serve records ---
                    entities_to_save = [BidTradeServeEntity(**d) for d in aggregated.values()]
                    if entities_to_save:
                        unique_key = ["scheduled_at", "time_block", "type", "group", "bid_type"]
                        records_with_filters = [
                            {"filter_query": {k: e.model_dump(by_alias=True).get(k) for k in unique_key},
                             "record_data": e.model_dump(by_alias=True)}
                            for e in entities_to_save
                        ]
                        result = await self.bid_trade_serve_repo.bulk_upsert_serve_records(records_with_filters)
                        logger.info(f"TESTING LOGS: [{job_name}] date={delivery_date_int} serve upsert={result.get('total_count')}")

                    total_processed += processed
                    results_details.append({"date": delivery_date_int, "processed": processed, "serve_records": len(entities_to_save)})
                    logger.info(f"TESTING LOGS: [{job_name}] date={delivery_date_int} processed={processed}")

                except Exception as ex:
                    logger.error(f"TESTING LOGS: [{job_name}] Error for date {delivery_date_int}: {ex}")
                    results_details.append({"date": delivery_date_int, "error": str(ex)})

            return {"status": "success", "total_processed": total_processed, "details": results_details}

        except Exception as e:
            logger.error(f"TESTING LOGS: [{job_name}] CRITICAL ERROR: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

