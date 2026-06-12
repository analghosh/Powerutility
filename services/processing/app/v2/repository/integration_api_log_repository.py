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
    def _extract_txn_id_from_url(url: Any) -> Optional[str]:
        """
        Extract TransactionId from a biddetails GET URL.
        URL format: .../biddetails/{LoginUserId},{ParticipantId},{TransactionId},{DeliveryDate}
        TransactionId is the 3rd comma-separated segment of the last URL path component.
        """
        if not isinstance(url, str):
            return None
        try:
            last_segment = url.rstrip("/").split("/")[-1]
            parts = last_segment.split(",")
            if len(parts) >= 3:
                return parts[2]
        except Exception:
            pass
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

        Returns separate entries for every submitSingleBid, cancelRtmBid and
        cancelAllDamBid call.  Cancels are no longer nested inside their parent
        submit — they appear as their own rows, even when they share the same
        correlationId.

        Associated logs (getBidDetail, getCancelledBidDetails) are matched via
        TransactionId as before.
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
            # Build lookup tables
            # -------------------------------------------------------
            # submit correlation_id  → submit log
            submit_by_corr:    Dict[str, Any] = {}
            # cancel correlation_id → cancelRtmBid log
            cancel_rtm_by_corr: Dict[str, Any] = {}
            # cancel correlation_id → cancelAllDamBid log
            cancel_dam_by_corr: Dict[str, Any] = {}
            # getBidDetail: TransactionId in URL → log
            get_bid_by_txn:    Dict[str, Any] = {}
            # getCancelledBidDetails: TransactionId in URL → log
            get_cancel_by_txn: Dict[str, Any] = {}

            for log in all_logs:
                method   = log.get("method")
                corr     = log.get("correlation_id")
                req_body = log.get("requestBody") or {}
                resp_body = log.get("responseBody") or {}

                if method == "submitSingleBid":
                    if corr and corr not in submit_by_corr:
                        submit_by_corr[corr] = log

                elif method == "cancelRtmBid":
                    if corr and corr not in cancel_rtm_by_corr:
                        cancel_rtm_by_corr[corr] = log

                elif method == "cancelAllDamBid":
                    if corr and corr not in cancel_dam_by_corr:
                        cancel_dam_by_corr[corr] = log

                elif method == "getBidDetail":
                    txn = self._extract_txn_id_from_url(log.get("url"))
                    if txn and txn not in get_bid_by_txn:
                        get_bid_by_txn[txn] = log

                elif method == "getCancelledBidDetails":
                    txn = self._extract_txn_id_from_url(log.get("url"))
                    if txn and txn not in get_cancel_by_txn:
                        get_cancel_by_txn[txn] = log

            response: List[Dict[str, Any]] = []

            # -------------------------------------------------------
            # 1) Submit entries
            # -------------------------------------------------------
            for corr, log in submit_by_corr.items():
                resp_body       = log.get("responseBody") or {}
                req_body        = log.get("requestBody")  or {}
                submit_txn_id   = self._extract_txn_id(resp_body)
                delivery_date_raw = log.get("delivery_date")

                result_item: Dict[str, Any] = {
                    "correlationId":                    corr,
                    "submitSingleBidRequestBody":       req_body,
                    "submitSingleBidResponseBody":      resp_body,
                    "getBidDetailResponseBody":         None,
                    "getCancelledBidDetailsResponseBody": None,
                    "cancelRtmBidResponseBody":         None,
                    "cancelAllDamBidResponseBody":      None,
                    "cancelRequestBody":                None,
                    "DeliveryDate":                     None,
                    "timestamp":                        log.get("timestamp"),
                    "bidType":                          "submit bid",
                }

                dd_raw = req_body.get("DeliveryDate")
                result_item["DeliveryDate"] = self._format_delivery_date(dd_raw)

                has_auto_cancel = False
                if submit_txn_id:
                    if submit_txn_id in get_bid_by_txn:
                        result_item["getBidDetailResponseBody"] = get_bid_by_txn[submit_txn_id].get("responseBody")

                # Link cancel logs by correlation_id (same request flow)
                cancel_rtm = cancel_rtm_by_corr.get(corr)
                if cancel_rtm:
                    has_auto_cancel = True
                    result_item["cancelRtmBidResponseBody"] = cancel_rtm.get("responseBody")
                    result_item["cancelRequestBody"]        = cancel_rtm.get("requestBody")
                    if not result_item["DeliveryDate"] and cancel_rtm.get("delivery_date"):
                        result_item["DeliveryDate"] = self._format_delivery_date(cancel_rtm["delivery_date"])
                    if not delivery_date_raw:
                        delivery_date_raw = cancel_rtm.get("delivery_date")
                    cancel_txn = self._extract_txn_id(cancel_rtm.get("responseBody") or {})
                    if cancel_txn and cancel_txn in get_cancel_by_txn:
                        result_item["getCancelledBidDetailsResponseBody"] = get_cancel_by_txn[cancel_txn].get("responseBody")

                cancel_dam = cancel_dam_by_corr.get(corr)
                if cancel_dam:
                    has_auto_cancel = True
                    result_item["cancelAllDamBidResponseBody"] = cancel_dam.get("responseBody")
                    result_item["cancelRequestBody"]           = cancel_dam.get("requestBody")
                    if not result_item["DeliveryDate"] and cancel_dam.get("delivery_date"):
                        result_item["DeliveryDate"] = self._format_delivery_date(cancel_dam["delivery_date"])
                    if not delivery_date_raw:
                        delivery_date_raw = cancel_dam.get("delivery_date")
                    cancel_txn = self._extract_txn_id(cancel_dam.get("responseBody") or {})
                    if cancel_txn and cancel_txn in get_cancel_by_txn:
                        result_item["getCancelledBidDetailsResponseBody"] = get_cancel_by_txn[cancel_txn].get("responseBody")

                # Fallback: fetch live getCancelledBidDetails if missing
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
                                    correlation_id=corr,
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
                                            corr, cancel_txn_id)
                    except Exception as fallback_err:
                        logger.warning("Fallback getCancelBidDetails failed for correlation_id=%s: %s",
                                       corr, fallback_err)

                if has_auto_cancel:
                    result_item["bidType"] = "auto_withdrawal_and_submit"
                    result_item["isAutoWithdrawal"] = True

                response.append(result_item)

            # -------------------------------------------------------
            # 2) Cancel entries — returned separately
            # -------------------------------------------------------
            for corr, cancel_log in cancel_rtm_by_corr.items():
                req_body = cancel_log.get("requestBody") or {}
                resp_body = cancel_log.get("responseBody") or {}
                delivery_date_raw = cancel_log.get("delivery_date")

                # Determine if this cancel is auto (same correlation as a submit) or manual
                is_auto = corr in submit_by_corr

                result_item: Dict[str, Any] = {
                    "correlationId": corr,
                    "submitSingleBidRequestBody": None,
                    "submitSingleBidResponseBody": None,
                    "getBidDetailResponseBody": None,
                    "getCancelledBidDetailsResponseBody": None,
                    "cancelRtmBidResponseBody": resp_body,
                    "cancelAllDamBidResponseBody": None,
                    "cancelRequestBody": req_body,
                    "DeliveryDate": self._format_delivery_date(delivery_date_raw),
                    "timestamp": cancel_log.get("timestamp"),
                    "bidType": "auto_withdrawal" if is_auto else "manual_withdrawal",
                    "isAutoWithdrawal": is_auto,
                    "isManualWithdrawal": not is_auto,
                }

                cancel_txn = self._extract_txn_id(resp_body)
                if cancel_txn and cancel_txn in get_cancel_by_txn:
                    result_item["getCancelledBidDetailsResponseBody"] = get_cancel_by_txn[cancel_txn].get("responseBody")

                response.append(result_item)

            for corr, cancel_log in cancel_dam_by_corr.items():
                req_body = cancel_log.get("requestBody") or {}
                resp_body = cancel_log.get("responseBody") or {}
                delivery_date_raw = cancel_log.get("delivery_date")

                is_auto = corr in submit_by_corr

                result_item: Dict[str, Any] = {
                    "correlationId": corr,
                    "submitSingleBidRequestBody": None,
                    "submitSingleBidResponseBody": None,
                    "getBidDetailResponseBody": None,
                    "getCancelledBidDetailsResponseBody": None,
                    "cancelRtmBidResponseBody": None,
                    "cancelAllDamBidResponseBody": resp_body,
                    "cancelRequestBody": req_body,
                    "DeliveryDate": self._format_delivery_date(delivery_date_raw),
                    "timestamp": cancel_log.get("timestamp"),
                    "bidType": "auto_withdrawal" if is_auto else "manual_withdrawal",
                    "isAutoWithdrawal": is_auto,
                    "isManualWithdrawal": not is_auto,
                }

                cancel_txn = self._extract_txn_id(resp_body)
                if cancel_txn and cancel_txn in get_cancel_by_txn:
                    result_item["getCancelledBidDetailsResponseBody"] = get_cancel_by_txn[cancel_txn].get("responseBody")

                response.append(result_item)

            return response

        except Exception as e:
            logger.error(f"Error fetching bid submission details: {e}")
            return []

    async def get_block_bid_submission_details(self, date_str: str) -> list:
        """
        Get RTM block bid submission details for a specific date.
        Reads from iex_integration_apilog filtered by bid_type="block-bid".

        Each exchange call is expanded into one row per BidDetails entry so that
        multi-block submissions do not collapse into a single visible row.
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

                req_body = log.get("requestBody") or {}
                resp_body = log.get("responseBody") or {}
                bid_details = req_body.get("BidDetails") or []

                if not bid_details:
                    response.append({
                        "correlationId":             log.get("correlation_id"),
                        "submitBlockBidRequestBody":  req_body,
                        "submitBlockBidResponseBody": resp_body,
                        "DeliveryDate":               delivery_date_str,
                        "timestamp":                  log.get("timestamp"),
                        "bidType":                    "block-bid",
                        "blockCount":                 0,
                    })
                    continue

                for idx, detail in enumerate(bid_details):
                    response.append({
                        "correlationId":              log.get("correlation_id"),
                        "transactionId":              resp_body.get("TransactionId"),
                        "submitStatus":               resp_body.get("Status"),
                        "submitStatusMessage":        resp_body.get("StatusMessage"),
                        "deliveryDate":               delivery_date_str,
                        "timestamp":                  log.get("timestamp"),
                        "bidType":                    "block-bid",
                        "blockIndex":                 idx,
                        "blockCount":                 len(bid_details),
                        "session":                    detail.get("Session"),
                        "fromPeriod":                 detail.get("FromPeriodId"),
                        "toPeriod":                   detail.get("ToPeriodId"),
                        "buySell":                    detail.get("BuySell"),
                        "price":                      detail.get("Price"),
                        "quantity":                   detail.get("Quantity"),
                        "bidReferenceId":             detail.get("BidReferenceId"),
                        "submitBlockBidRequestBody":  req_body,
                        "submitBlockBidResponseBody": resp_body,
                    })

            return response

        except Exception as e:
            logger.error(f"Error fetching block bid submission details: {e}")
            return []


# Singleton instance
integration_api_log_repository = IntegrationApiLogRepository()
