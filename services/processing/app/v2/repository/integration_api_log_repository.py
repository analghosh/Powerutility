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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_txn_id(body: Any) -> Optional[str]:
        """Return TransactionId from a request/response body dict (case-insensitive key variants)."""
        if not isinstance(body, dict):
            return None
        for key in ("TransactionId", "transactionId", "transaction_id"):
            val = body.get(key)
            if val is not None:
                return str(val)
        return None

    @staticmethod
    def _format_delivery_date(raw: Any) -> Optional[str]:
        """Convert a Unix-timestamp delivery_date value to YYYY-MM-DD HH:MM:SS string."""
        if raw is None:
            return None
        try:
            ts = int(raw) if not isinstance(raw, (int, float)) else raw
            return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(raw)

    # ------------------------------------------------------------------

    async def get_bid_submission_details(self, date_str: str, market_type: str, bid_type: str) -> list:
        """
        Get bid submission details for a specific date.

        Returns one entry per submitSingleBid call (one per bid submitted).
        Multiple bids submitted in one request share the same correlationId but each
        gets its own row — no uniqueness on correlationId.

        Associated logs (getBidDetail, cancelRtmBid, etc.) are matched to their
        parent submission via the TransactionId returned in the submitSingleBid
        response body.
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
                        "market_type": market_type,
                        "bid_type": bid_type,
                        "method": {"$in": [
                            "submitSingleBid", "getBidDetail",
                            "cancelRtmBid", "cancelAllDamBid", "getCancelledBidDetails"
                        ]}
                    }
                },
                {"$sort": {"timestamp": -1}},  # newest first
            ]

            all_logs = await self.aggregate_async(self.collection_name, pipeline)

            # -------------------------------------------------------
            # Build lookup tables keyed by TransactionId so we can
            # match associated logs to the correct submission even when
            # multiple bids share the same correlation_id.
            # -------------------------------------------------------
            # submit_txn_id  → getBidDetail log (latest)
            get_bid_by_txn:    Dict[str, Any] = {}
            # submit_txn_id  → cancelRtmBid log (latest)
            cancel_rtm_by_txn: Dict[str, Any] = {}
            # submit_txn_id  → cancelAllDamBid log (latest)
            cancel_dam_by_txn: Dict[str, Any] = {}
            # cancel_txn_id  → getCancelledBidDetails log (latest)
            get_cancel_by_txn: Dict[str, Any] = {}

            submit_logs: List[Any] = []

            for log in all_logs:
                method  = log.get("method")
                req_body = log.get("requestBody") or {}

                if method == "submitSingleBid":
                    submit_logs.append(log)

                elif method == "getBidDetail":
                    txn = self._extract_txn_id(req_body)
                    if txn and txn not in get_bid_by_txn:
                        get_bid_by_txn[txn] = log

                elif method == "cancelRtmBid":
                    txn = self._extract_txn_id(req_body)
                    if txn and txn not in cancel_rtm_by_txn:
                        cancel_rtm_by_txn[txn] = log

                elif method == "cancelAllDamBid":
                    txn = self._extract_txn_id(req_body)
                    if txn and txn not in cancel_dam_by_txn:
                        cancel_dam_by_txn[txn] = log

                elif method == "getCancelledBidDetails":
                    txn = self._extract_txn_id(req_body)
                    if txn and txn not in get_cancel_by_txn:
                        get_cancel_by_txn[txn] = log

            # -------------------------------------------------------
            # Build one response entry per submitSingleBid log
            # -------------------------------------------------------
            response = []
            for log in submit_logs:
                correlation_id  = log.get("correlation_id")
                resp_body       = log.get("responseBody") or {}
                req_body        = log.get("requestBody")  or {}
                submit_txn_id   = self._extract_txn_id(resp_body)

                result_item: Dict[str, Any] = {
                    "correlationId":                    correlation_id,
                    "submitSingleBidRequestBody":        req_body,
                    "submitSingleBidResponseBody":       resp_body,
                    "getBidDetailResponseBody":          None,
                    "getCancelledBidDetailsResponseBody": None,
                    "cancelRtmBidResponseBody":          None,
                    "cancelAllDamBidResponseBody":       None,
                    "cancelRequestBody":                 None,
                    "DeliveryDate":                      None,
                    "timestamp":                         log.get("timestamp"),
                    "bidType":                           "submit bid",
                }

                # DeliveryDate from submit requestBody
                dd_raw = req_body.get("DeliveryDate")
                result_item["DeliveryDate"] = self._format_delivery_date(dd_raw)

                delivery_date_raw = log.get("delivery_date")

                if submit_txn_id:
                    # getBidDetail — matched by TransactionId in its requestBody
                    if submit_txn_id in get_bid_by_txn:
                        result_item["getBidDetailResponseBody"] = get_bid_by_txn[submit_txn_id].get("responseBody")

                    # cancelRtmBid — matched by TransactionId in its requestBody
                    cancel_rtm = cancel_rtm_by_txn.get(submit_txn_id)
                    if cancel_rtm:
                        result_item["cancelRtmBidResponseBody"] = cancel_rtm.get("responseBody")
                        result_item["cancelRequestBody"]        = cancel_rtm.get("requestBody")
                        if not result_item["DeliveryDate"] and cancel_rtm.get("delivery_date"):
                            result_item["DeliveryDate"] = self._format_delivery_date(cancel_rtm["delivery_date"])
                        if not delivery_date_raw:
                            delivery_date_raw = cancel_rtm.get("delivery_date")
                        # getCancelledBidDetails — matched by TransactionId from cancel response
                        cancel_txn = self._extract_txn_id(cancel_rtm.get("responseBody") or {})
                        if cancel_txn and cancel_txn in get_cancel_by_txn:
                            result_item["getCancelledBidDetailsResponseBody"] = get_cancel_by_txn[cancel_txn].get("responseBody")

                    # cancelAllDamBid — same pattern
                    cancel_dam = cancel_dam_by_txn.get(submit_txn_id)
                    if cancel_dam:
                        result_item["cancelAllDamBidResponseBody"] = cancel_dam.get("responseBody")
                        result_item["cancelRequestBody"]           = cancel_dam.get("requestBody")
                        if not result_item["DeliveryDate"] and cancel_dam.get("delivery_date"):
                            result_item["DeliveryDate"] = self._format_delivery_date(cancel_dam["delivery_date"])
                        if not delivery_date_raw:
                            delivery_date_raw = cancel_dam.get("delivery_date")
                        cancel_txn = self._extract_txn_id(cancel_dam.get("responseBody") or {})
                        if cancel_txn and cancel_txn in get_cancel_by_txn:
                            result_item["getCancelledBidDetailsResponseBody"] = get_cancel_by_txn[cancel_txn].get("responseBody")

                # Fallback: cancellation exists but no getCancelledBidDetails logged → fetch live
                cancel_response = result_item.get("cancelAllDamBidResponseBody") or result_item.get("cancelRtmBidResponseBody")
                if cancel_response and not result_item.get("getCancelledBidDetailsResponseBody"):
                    try:
                        cancel_txn_id = self._extract_txn_id(cancel_response)
                        if cancel_txn_id and delivery_date_raw:
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
                                    transaction_id=str(cancel_txn_id),
                                    delivery_date=int(delivery_date_raw),
                                    bid_type="single-bid",
                                )
                                result_item["getCancelledBidDetailsResponseBody"] = cancel_bid_details
                                logger.info("Fallback: fetched getCancelledBidDetails for correlation_id=%s txn=%s",
                                            correlation_id, cancel_txn_id)
                    except Exception as fallback_err:
                        logger.warning("Fallback getCancelBidDetails failed for correlation_id=%s: %s",
                                       correlation_id, fallback_err)

                # bidType: cancellation-only entries have no submitSingleBidRequestBody
                if not result_item.get("submitSingleBidRequestBody"):
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
