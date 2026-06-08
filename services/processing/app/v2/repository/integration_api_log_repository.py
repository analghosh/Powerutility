"""
Integration API Log Repository
Repository for logging API integration calls (request/response)
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from pymongo import ASCENDING
from pymongo.errors import OperationFailure
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

class IntegrationApiLogRepository(GenericRepository):
    """Repository for integration API logs"""
    
    def __init__(self):
        super().__init__()
        self.collection_name = "iex_integration_apilog"
        self._indexes_ensured = False

    async def _ensure_indexes(self):
        if self._indexes_ensured:
            return
        try:
            collection = self.db[self.collection_name]
            await collection.create_index(
                [("timestamp", ASCENDING)],
                expireAfterSeconds=604800,
                name="ttl_timestamp_7d",
                background=True
            )
            self._indexes_ensured = True
            logger.info("Created TTL index on %s", self.collection_name)
        except OperationFailure as e:
            if e.code == 86:
                logger.debug("TTL index already exists on %s", self.collection_name)
                self._indexes_ensured = True
            else:
                logger.error("Error creating TTL index on %s: %s", self.collection_name, e)
        except Exception as e:
            logger.error("Error ensuring indexes on %s: %s", self.collection_name, e)

    async def log_api_call(self, url: str, request_body: Any, headers: Any, response_body: Any, correlation_id: Optional[str] = None, market_type: Optional[str] = None, bid_type: Optional[str] = None, method: Optional[str] = None, delivery_date: Optional[int] = None) -> Any:
        """Log API call details"""
        await self._ensure_indexes()
        log_entry = {
            "url": url,
            "requestBody": request_body,
            "headers": headers,
            "responseBody": response_body,
            "correlation_id": correlation_id,
            "timestamp": datetime.now(),
            "created_at": datetime.now(),
            "market_type": market_type,
            "bid_type": bid_type,
            "method": method,
            "delivery_date": delivery_date
        }
        return await self.insert_one_async(self.collection_name, log_entry)

    async def get_bid_submission_details(self, date_str: str, market_type: str, bid_type: str) -> list:
        """
        Get bid submission details grouped by correlation_id for a specific date.
        
        Args:
            date_str: Date string in YYYY-MM-DD format
            market_type: Market type (DAM/RTM)
            bid_type: Bid type (single-bid, etc.)
            
        Returns:
            List of grouped bid details
        """
        try:
            start_date = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date.replace(hour=23, minute=59, second=59, microsecond=999999)
            
            # Convert date_str to Unix timestamp range for delivery_date
            start_ts = int(start_date.timestamp())
            end_ts = int(end_date.timestamp())
            
            pipeline = [
                {
                    "$match": {
                        "delivery_date": {"$gte": start_ts, "$lte": end_ts},
                        "market_type": market_type,
                        "bid_type": bid_type,
                        "method": {"$in": ["getBidDetail", "submitSingleBid", "cancelRtmBid", "cancelAllDamBid", "getCancelledBidDetails"]}
                    }
                },
                # Sort by timestamp descending to get latest entries first
                {
                    "$sort": {"timestamp": -1}
                },
                {
                    "$group": {
                        "_id": "$correlation_id",
                        "logs": {"$push": "$$ROOT"}
                    }
                }
            ]
            
            logger.info(f"Executing pipeline on {self.collection_name}: {pipeline}")
            grouped_results = await self.aggregate_async(self.collection_name, pipeline)
            
            response = []
            for group in grouped_results:
                correlation_id = group["_id"]
                logs = group["logs"]
                
                result_item = {
                    "correlationId": correlation_id,
                    "submitSingleBidRequestBody": None,
                    "submitSingleBidResponseBody": None,
                    "getBidDetailResponseBody": None,
                    "getCancelledBidDetailsResponseBody": None,
                    "cancelRtmBidResponseBody": None,
                    "cancelAllDamBidResponseBody": None,
                    "cancelRequestBody": None,
                    "DeliveryDate": None,
                    "timestamp": None,
                    "bidType": None
                }
                
                # Track if we've already found the latest getBidDetail / getCancelledBidDetails
                found_get_bid_detail = False
                found_cancel_bid_detail = False
                delivery_date_raw = None
                
                for log in logs:
                    method = log.get("method")
                    if method == "submitSingleBid":
                        result_item["submitSingleBidRequestBody"] = log.get("requestBody")
                        result_item["submitSingleBidResponseBody"] = log.get("responseBody")
                        result_item["timestamp"] = log.get("timestamp")
                        
                        # Extract and format DeliveryDate from requestBody
                        req_body = log.get("requestBody", {})
                        if req_body and "DeliveryDate" in req_body:
                             try:
                                ts = req_body["DeliveryDate"]
                                if isinstance(ts, (int, float)):
                                     # Assuming Unix timestamp
                                     dt_obj = datetime.utcfromtimestamp(ts)
                                     result_item["DeliveryDate"] = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                                elif isinstance(ts, str) and ts.isdigit():
                                     dt_obj = datetime.utcfromtimestamp(int(ts))
                                     result_item["DeliveryDate"] = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                                else:
                                     result_item["DeliveryDate"] = str(ts)
                             except Exception:
                                 result_item["DeliveryDate"] = str(req_body.get("DeliveryDate"))

                    elif method == "getBidDetail" and not found_get_bid_detail:
                        # Only take the first (latest) getBidDetail entry since logs are sorted by timestamp desc
                        result_item["getBidDetailResponseBody"] = log.get("responseBody")
                        found_get_bid_detail = True

                    elif method == "getCancelledBidDetails" and not found_cancel_bid_detail:
                        # Only take the first (latest) getCancelledBidDetails entry
                        result_item["getCancelledBidDetailsResponseBody"] = log.get("responseBody")
                        found_cancel_bid_detail = True

                    elif method == "cancelRtmBid":
                        result_item["cancelRtmBidResponseBody"] = log.get("responseBody")
                        result_item["cancelRequestBody"] = log.get("requestBody")
                        if not result_item["timestamp"]:
                            result_item["timestamp"] = log.get("timestamp")
                        if not delivery_date_raw:
                            delivery_date_raw = log.get("delivery_date")
                        if log.get("delivery_date") and not result_item["DeliveryDate"]:
                            dd = log.get("delivery_date")
                            try:
                                if isinstance(dd, (int, float)):
                                    dt_obj = datetime.utcfromtimestamp(dd)
                                    result_item["DeliveryDate"] = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                                elif isinstance(dd, str) and dd.isdigit():
                                    dt_obj = datetime.utcfromtimestamp(int(dd))
                                    result_item["DeliveryDate"] = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                                else:
                                    result_item["DeliveryDate"] = str(dd)
                            except Exception:
                                result_item["DeliveryDate"] = str(dd)

                    elif method == "cancelAllDamBid":
                        result_item["cancelAllDamBidResponseBody"] = log.get("responseBody")
                        result_item["cancelRequestBody"] = log.get("requestBody")
                        if not result_item["timestamp"]:
                            result_item["timestamp"] = log.get("timestamp")
                        if not delivery_date_raw:
                            delivery_date_raw = log.get("delivery_date")
                        if log.get("delivery_date") and not result_item["DeliveryDate"]:
                            dd = log.get("delivery_date")
                            try:
                                if isinstance(dd, (int, float)):
                                    dt_obj = datetime.utcfromtimestamp(dd)
                                    result_item["DeliveryDate"] = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                                elif isinstance(dd, str) and dd.isdigit():
                                    dt_obj = datetime.utcfromtimestamp(int(dd))
                                    result_item["DeliveryDate"] = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                                else:
                                    result_item["DeliveryDate"] = str(dd)
                            except Exception:
                                result_item["DeliveryDate"] = str(dd)
                
                # Fallback: if cancellation exists but no getCancelledBidDetails log, fetch from API and log it
                cancel_response = result_item.get("cancelAllDamBidResponseBody") or result_item.get("cancelRtmBidResponseBody")
                if cancel_response and not result_item.get("getCancelledBidDetailsResponseBody"):
                    try:
                        txn_id = cancel_response.get("TransactionId") if isinstance(cancel_response, dict) else None
                        if txn_id and delivery_date_raw:
                            from services.processing.app.v2.iex.masterApi import ExchangeAPIClient
                            from services.processing.app.v2.repository.exchange_config_repository import ExchangeConfigRepository
                            
                            exchange_config_repo = ExchangeConfigRepository()
                            config = await exchange_config_repo.get_bidType_market_active_config("single", market_type.upper())
                            if config:
                                fallback_client = ExchangeAPIClient(
                                    base_url=config.get("baseUrl", ""),
                                    login_user_id=config.get("LoginUserId", ""),
                                    participant_id=config.get("ParticipantId", ""),
                                    portfolio_id=config.get("PortfolioId", ""),
                                    bearer_token=config.get("BearerToken", ""),
                                    correlation_id=correlation_id,
                                    market_type=market_type.lower(),
                                    bid_type="single-bid",
                                )
                                cancel_bid_details = await fallback_client.get_cancel_bid_details(
                                    transaction_id=str(txn_id),
                                    delivery_date=int(delivery_date_raw),
                                    bid_type="single-bid",
                                )
                                result_item["getCancelledBidDetailsResponseBody"] = cancel_bid_details
                                logger.info(f"Fallback: fetched and logged getCancelledBidDetails for correlation_id={correlation_id}")
                    except Exception as fallback_err:
                        logger.warning(f"Fallback getCancelBidDetails failed for correlation_id={correlation_id}: {fallback_err}")

                # Determine bidType based on presence of submission request body
                if result_item.get("submitSingleBidRequestBody"):
                    result_item["bidType"] = "submit bid"
                else:
                    result_item["bidType"] = "withdrawalBid"
                    
                response.append(result_item)
                
            return response
            
        except Exception as e:
            logger.error(f"Error fetching bid submission details: {e}")
            return []

    async def get_block_bid_submission_details(self, date_str: str) -> list:
        """
        Get RTM block bid submission details for a specific date.
        Reads from iex_integration_apilog filtered by bid_type="block-bid".

        Returns one entry per exchange call (one per bid submitted), sorted newest first.
        Multiple bids submitted in one API call share the same correlationId — each
        gets its own row so no submission is hidden.
        """
        try:
            start_date = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
            end_date   = start_date.replace(hour=23, minute=59, second=59, microsecond=999999)
            start_ts   = int(start_date.timestamp())
            end_ts     = int(end_date.timestamp())

            pipeline = [
                {
                    "$match": {
                        "delivery_date": {"$gte": start_ts, "$lte": end_ts},
                        "market_type": "rtm",
                        "bid_type": "block-bid",
                        "method": "submitBlockBid"
                    }
                },
                {"$sort": {"timestamp": -1}},
            ]

            logs = await self.aggregate_async(self.collection_name, pipeline)

            response = []
            for log in logs:
                dd = log.get("delivery_date")
                delivery_date_str = None
                if dd:
                    try:
                        delivery_date_str = datetime.utcfromtimestamp(int(dd)).strftime("%Y-%m-%d")
                    except Exception:
                        delivery_date_str = str(dd)

                response.append({
                    "correlationId":             log.get("correlation_id"),
                    "submitBlockBidRequestBody":  log.get("requestBody"),
                    "submitBlockBidResponseBody": log.get("responseBody"),
                    "DeliveryDate":               delivery_date_str,
                    "timestamp":                  log.get("timestamp"),
                    "bidType":                    "block-bid"
                })

            return response

        except Exception as e:
            logger.error(f"Error fetching block bid submission details: {e}")
            return []


# Singleton instance
integration_api_log_repository = IntegrationApiLogRepository()
