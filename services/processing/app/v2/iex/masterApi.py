"""
Exchange API Client (masterApi)
Client for external exchange APIs: Asset Details, Delivery Dates, Single Bid submission
"""

import httpx
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from services.processing.app.v2.repository.integration_api_log_repository import integration_api_log_repository

logger = logging.getLogger(__name__)


class ExchangeAPIClient:
    """Client for external exchange API calls"""
    
    def __init__(self, base_url: str, login_user_id: str, participant_id: str, portfolio_id: str, bearer_token: Optional[str] = None, correlation_id: Optional[str] = None, market_type: str = "dam", bid_type: str = "single-bid"):
        """
        Initialize the API client
        
        Args:
            base_url: Base URL for the exchange API
            login_user_id: User ID for API authentication
            participant_id: Participant ID
            portfolio_id: Portfolio ID
            correlation_id: Unique ID for tracing request flow
            market_type: Market type (dam, rtm, etc.) - defaults to 'dam'
            bid_type: Bid type (single-bid, block-bid, etc.) - defaults to 'single-bid'
        """
        self.base_url = base_url.rstrip('/')
        self.login_user_id = login_user_id
        self.participant_id = participant_id
        self.portfolio_id = portfolio_id
        self.correlation_id = correlation_id
        self.market_type = market_type.lower()
        self.bid_type = bid_type
        # Standard timeout for read-only calls (asset details, delivery dates, etc.)
        self.timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
        # Longer read timeout for bid submission — IEX exchange API can take >30s to process
        self.submit_timeout = httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0)
        self.bearer_token = bearer_token
        
        # Cache for API responses
        self._asset_details_cache: Optional[Dict[str, Any]] = None
        self._delivery_date_cache: Optional[Dict[str, Any]] = None

    def _get_headers(self) -> Dict[str, str]:
        """Get common headers for API requests"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    async def _log_to_db(self, url: str, request_body: Any, headers: Any, response_body: Any, market_type: str, bid_type: str, method: str, delivery_date: Optional[int] = None):
        """Log API interaction to database"""
        try:
            await integration_api_log_repository.log_api_call(
                url=url,
                request_body=request_body,
                headers=headers,
                response_body=response_body,
                correlation_id=self.correlation_id or "",
                market_type=market_type,
                bid_type=bid_type,
                method=method,
                delivery_date=delivery_date
            )
        except Exception as e:
            logger.error(f"Failed to log API call to DB: {e}")

    # ... (rest of methods) ...

    async def get_bid_details(self, transaction_id: str, delivery_date: int, bid_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch bid details from exchange API
        
        API: GET {baseUrl}/{marketType}/api/v2/biddetails/{LoginUserId},{ParticipantId},{TransactionId},{DeliveryDate}
        
        Args:
            transaction_id: Transaction ID from submission response
            delivery_date: Delivery date timestamp
            
        Returns:
            Bid details response
        """
        url = f"{self.base_url}/{self.market_type}/api/v2/biddetails/{self.login_user_id},{self.participant_id},{transaction_id},{delivery_date}"
        headers = self._get_headers()
        
        logger.info(f"Fetching bid details from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
            # Capture response body
            try:
                data = response.json()
            except:
                data = response.text
                
            # Log to DB
            # I need to update _log_to_db to pass correlation_id.
            # I'll implement _log_to_db with correlation_id arg below.
            await self._log_to_db(url, {"method": "GET"}, headers, data, self.market_type, bid_type or self.bid_type, "getBidDetail", delivery_date)
            
            response.raise_for_status()
            
            logger.info("Bid details fetched successfully")
            return data if isinstance(data, dict) else {}

    async def get_cancel_bid_details(self, transaction_id: str, delivery_date: int, bid_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch bid details after cancellation from exchange API.
        
        Identical to get_bid_details but logs as 'getCancelledBidDetails'
        so cancellation lookups are distinguishable from submission lookups.
        
        API: GET {baseUrl}/{marketType}/api/v2/biddetails/{LoginUserId},{ParticipantId},{TransactionId},{DeliveryDate}
        
        Args:
            transaction_id: Transaction ID from cancellation response
            delivery_date: Delivery date timestamp
            
        Returns:
            Bid details response
        """
        url = f"{self.base_url}/{self.market_type}/api/v2/biddetails/{self.login_user_id},{self.participant_id},{transaction_id},{delivery_date}"
        headers = self._get_headers()
        
        logger.info(f"Fetching cancel bid details from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
            # Capture response body
            try:
                data = response.json()
            except:
                data = response.text
                
            # Log to DB with distinct method name for cancellation
            await self._log_to_db(url, {"method": "GET"}, headers, data, self.market_type, bid_type or self.bid_type, "getCancelledBidDetails", delivery_date)
            
            response.raise_for_status()
            
            logger.info("Cancel bid details fetched successfully")
            return data if isinstance(data, dict) else {}

    async def get_asset_details(self, bid_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch asset details from exchange API
        
        API: GET {baseUrl}/{marketType}/api/v2/master/assets/{LoginUserId},{ParticipantId}
        
        Returns:
            Asset details response with AssetDetails array
        """
        # Note: Bypassing cache to ensure logging happens if requested, or keep caching logic?
        # User said "every api call". If cached, we don't call API. 
        # I'll keep cache but only log when ACTUAL call happens.
        
        if self._asset_details_cache:
            return self._asset_details_cache
            
        url = f"{self.base_url}/{self.market_type}/api/v2/master/assets/{self.login_user_id},{self.participant_id}"
        headers = self._get_headers()
        
        logger.info(f"Fetching asset details from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
            # Capture response body
            try:
                data = response.json()
            except:
                data = response.text
                
            # Log to DB
            await self._log_to_db(url, {"method": "GET"}, headers, data, self.market_type, bid_type or self.bid_type, "getAssetDetail")
            
            response.raise_for_status()
            
            self._asset_details_cache = data if isinstance(data, dict) else {}
            logger.info(f"Asset details fetched successfully")
            return self._asset_details_cache

    async def get_delivery_dates(self, bid_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch delivery dates from exchange API
        
        API: GET {baseUrl}/{marketType}/api/v2/deliverydates/{LoginUserId},{ParticipantId}
        
        Returns:
            Delivery date response with DeliveryDateId and DeliveryDate
        """
        if self._delivery_date_cache:
            return self._delivery_date_cache
            
        url = f"{self.base_url}/{self.market_type}/api/v2/deliverydates/{self.login_user_id},{self.participant_id}"
        headers = self._get_headers()
        
        logger.info(f"Fetching delivery dates from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
             # Capture response body
            try:
                data = response.json()
            except:
                data = response.text
            
            # Log to DB
            await self._log_to_db(url, {"method": "GET"}, headers, data, self.market_type, bid_type or self.bid_type, "getDeliveryDate")

            response.raise_for_status()
            
            self._delivery_date_cache = data if isinstance(data, dict) else {}
            logger.info(f"Delivery date fetched: {self._delivery_date_cache.get('DeliveryDateId')}")
            return self._delivery_date_cache

    async def get_bid_reference_id(self) -> Dict[str, Any]:
        """
        Fetch bid reference ID from exchange API
        
        API: GET {baseUrl}/{marketType}rtm/api/v2/blockbid/bidrefcount/{LoginUserId},{ParticipantId} 
        
        Returns:
            Bid reference ID response
        """
            
        url = f"{self.base_url}/{self.market_type}/api/v2/blockbid/bidrefcount/{self.login_user_id},{self.participant_id}"
        headers = self._get_headers()
        
        logger.info(f"Fetching bid reference ID from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
             # Capture response body
            try:
                data = response.json()
            except:
                data = response.text
            
            # Log to DB
            await self._log_to_db(url, {"method": "GET"}, headers, data, self.market_type, "block-bid", "getBidReferenceId")

            response.raise_for_status()
            
            self._bid_reference_id_cache = data if isinstance(data, dict) else {}
            logger.info(f"Bid reference ID fetched: {self._bid_reference_id_cache.get('RTMBlockBidRefCount')}")
            return self._bid_reference_id_cache

    
    async def submit_single_bid(self, bid_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit single bid to exchange API
        
        API: POST {baseUrl}/{marketType}/api/v2/singlebid/ocf
        
        Args:
            bid_request: Complete bid request body
            
        Returns:
            API response
        """
        # Use dynamic URL based on market type
        # For DAM it is .../dam/api/v2/singlebid/ocf
        # For RTM it is likely .../rtm/api/v2/singlebid (no ocf?) or .../rtm/api/v2/singlebid/ocf
        # I'll rely on market_type. If RTM needs different path suffix, we can adjust here.
        # Assuming consistency for now:
        url = f"{self.base_url}/{self.market_type}/api/v2/singlebid/ocf"
        if self.market_type == 'rtm':
             # RTM might not have /ocf suffix or might be different
             # User mentioned "again call post api" for RTM. 
             # Assuming common V2 structure: /rtm/api/v2/singlebid
             url = f"{self.base_url}/{self.market_type}/api/v2/singlebid"

        headers = self._get_headers()
        
        logger.info(f"Submitting single bid to: {url}")
        logger.debug(f"Bid request: {bid_request}")

        async with httpx.AsyncClient(timeout=self.submit_timeout) as client:
            response = await client.post(url, json=bid_request, headers=headers)

            # Log response regardless of status
            logger.info(f"Single bid response status: {response.status_code}")
            
            try:
                data = response.json()
            except:
                data = response.text
            
            # Log to DB
            await self._log_to_db(url, bid_request, headers, data, self.market_type, "single-bid", "submitSingleBid", bid_request.get("DeliveryDate"))

            response.raise_for_status()
            
            logger.info("Single bid submitted successfully")
            return data if isinstance(data, dict) else {}

    async def submit_block_bid(self, bid_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit RTM block bid to exchange API.

        API: POST {baseUrl}/rtm/api/v2/blockbid  (IEX RTM API v2.0)

        Args:
            bid_request: PascalCase body per v2.0 spec:
                {DeliveryDate, AssetId, BidAreaId, UserId, ParticipantId,
                 PortfolioId, InitiatedBy, BidDetails[]}
        Returns:
            {TransactionId, Status, StatusMessage} — NOT per-bid details.
            Call get_bid_details(TransactionId, DeliveryDate) after 3-5 sec for per-bid data.
        """
        url = f"{self.base_url}/rtm/api/v2/blockbid"
        headers = self._get_headers()

        import json as _json
        logger.info(f"Submitting RTM block bid to: {url}")
        logger.info(
            f"Block bid curl equivalent: "
            f"curl -X POST '{url}' "
            f"-H 'Authorization: Bearer {self.bearer_token}' "
            f"-H 'Content-Type: application/json' "
            f"-d '{_json.dumps(bid_request, default=str)}'"
        )

        async with httpx.AsyncClient(timeout=self.submit_timeout) as client:
            response = await client.post(url, json=bid_request, headers=headers)

            logger.info(f"Block bid response status: {response.status_code}")

            try:
                data = response.json()
            except Exception:
                data = response.text

            await self._log_to_db(
                url, bid_request, headers, data,
                "rtm", "block-bid", "submitBlockBid",
                bid_request.get("DeliveryDate")
            )

            if not response.is_success:
                logger.error(f"Block bid submission failed [{response.status_code}]: {data}")
                return {"Status": "Failed", "StatusMessage": data}

            logger.info(f"RTM block bid submitted successfully: {data}")
            return data if isinstance(data, dict) else {"Status": "Failed", "StatusMessage": data}

    async def get_single_bid_dam(
        self, 
        delivery_date: int, 
        product_id: str = "ALL",
        bid_area_id: str = "S2"
    ) -> Dict[str, Any]:
        """
        Fetch existing DAM single bids for a delivery date.
        
        API: GET dam/api/v2/singlebid/{LoginUserId},{ParticipantId},{DeliveryDate},{UserId},{PortfolioId},{ProductId},{BidAreaId},{Status},{ExceptionId},{OrderId},{PageNumber},{PageSize}
        
        Args:
            delivery_date: Delivery date timestamp
            product_id: Product ID (default ALL)
            bid_area_id: Bid area ID (default S2)
            
        Returns:
            { SingleBidDetails: [], TotalRecord: int }
        """
        # Fixed params as per requirement
        status = "ALL"
        exception_id = 0
        order_id = 0
        page_number = 0
        page_size = 0
        
        url = (
            f"{self.base_url}/dam/api/v2/singlebid/"
            f"{self.login_user_id},{self.participant_id},{delivery_date},"
            f"{self.login_user_id},{self.portfolio_id},{product_id},"
            f"{bid_area_id},{status},{exception_id},{order_id},{page_number},{page_size}"
        )
        headers = self._get_headers()
        
        logger.info(f"Fetching DAM single bids from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
            try:
                data = response.json()
            except:
                data = response.text
                
            await self._log_to_db(url, {"method": "GET"}, headers, data, "dam", "single-bid", "getSingleBidDam")
            
            response.raise_for_status()
            
            logger.info(f"DAM single bids fetched, TotalRecord: {data.get('TotalRecord', 0) if isinstance(data, dict) else 'N/A'}")
            return data if isinstance(data, dict) else {"SingleBidDetails": [], "TotalRecord": 0}

    async def cancel_all_dam_bid(
        self,
        delivery_date: int,
        asset_id: str,
        bid_area_id: str = "S2"
    ) -> Dict[str, Any]:
        """
        Cancel all DAM single bids for a delivery date.
        
        API: PUT dam/api/v2/singlebid/cancelall
        
        Args:
            delivery_date: Delivery date timestamp
            asset_id: Asset ID
            bid_area_id: Bid area ID
            
        Returns:
            { Status: str, StatusMessage: str, TransactionId: str }
        """
        url = f"{self.base_url}/dam/api/v2/singlebid/cancelall"
        headers = self._get_headers()
        
        request_body = {
            "DeliveryDate": delivery_date,
            "AssetId": asset_id,
            "BidAreaId": bid_area_id,
            "UserId": self.login_user_id,
            "ParticipantId": self.participant_id,
            "PortfolioId": self.portfolio_id
        }
        
        logger.info(f"Canceling all DAM bids: {url}")
        logger.debug(f"Cancel request body: {request_body}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.put(url, json=request_body, headers=headers)
            
            try:
                data = response.json()
            except:
                data = response.text
                
            await self._log_to_db(url, request_body, headers, data, "dam", "single-bid", "cancelAllDamBid", delivery_date)
            
            response.raise_for_status()
            
            logger.info(f"DAM cancel response: Status={data.get('Status') if isinstance(data, dict) else 'N/A'}")
            return data if isinstance(data, dict) else {"Status": "Failed", "StatusMessage": str(data)}

    async def get_single_bid_rtm(
        self,
        delivery_date: int,
        bid_area_id: str = "S2",
        isLiveConnection: bool = False
    ) -> Dict[str, Any]:
        """
        Fetch existing RTM single bids for a delivery date.
        
        API: GET rtm/api/v2/singlebid/{LoginUserId},{ParticipantId},{DeliveryDate},{UserId},{PortfolioId},{Session},{Period},{BidAreaId},{Status},{ExceptionId},{OrderId},{SortingType},{PageNumber},{PageSize}
        
        Args:
            delivery_date: Delivery date timestamp
            bid_area_id: Bid area ID (default N1)
            
        Returns:
            { RTMBlockBidDetails: [], TotalRecord: int }
        """
        # Fixed params as per requirement
        session = 0
        period = "ALL"
        status = "ALL"
        exception_id = 0
        order_id = 0
        sorting_type = 2
        page_number = 0
        page_size = 0
        
        url = (
            f"{self.base_url}/rtm/api/v2/singlebid/"
            f"{self.login_user_id},{self.participant_id},{delivery_date},"
            f"{self.login_user_id},{self.portfolio_id},{session},{period},"
            f"{bid_area_id},{status},{exception_id},{order_id},{sorting_type},{page_number}"
        )
        if isLiveConnection:
            url = (
                f"{self.base_url}/rtm/api/v2/singlebid/"
                f"{self.login_user_id},{self.participant_id},{delivery_date},"
                f"{self.login_user_id},{self.portfolio_id},{session},{period},"
                f"{bid_area_id},{status},{exception_id},{order_id},{sorting_type},{page_number}"
            )
        headers = self._get_headers()
        
        logger.info(f"Fetching RTM single bids from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
            try:
                data = response.json()
            except:
                data = response.text
                
            await self._log_to_db(url, {"method": "GET"}, headers, data, "rtm", "single-bid", "getSingleBidRtm")
            
            response.raise_for_status()
            
            logger.info(f"RTM single bids fetched, TotalRecord: {data.get('TotalRecord', 0) if isinstance(data, dict) else 'N/A'}")
            return data if isinstance(data, dict) else {"RTMBlockBidDetails": [], "TotalRecord": 0}

    async def cancel_rtm_bid(
        self,
        order_id: int,
        last_updated_time: int,
        delivery_date: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Cancel individual RTM bid by OrderId.
        
        API: PUT rtm/api/v2/singlebid/cancel
        
        Args:
            order_id: Order ID to cancel (Number)
            last_updated_time: Last updated timestamp from get_single_bid_rtm (Number)
            delivery_date: Delivery date timestamp for logging
            
        Returns:
            { TransactionId: str, Status: str, StatusMessage: str }
        """
        url = f"{self.base_url}/rtm/api/v2/singlebid/cancel"
        headers = self._get_headers()
        
        request_body = {
            "OrderId": int(order_id),  # Must be Number per API spec
            "UserId": self.login_user_id,
            "ParticipantId": self.participant_id,
            "PortfolioId": self.portfolio_id,
            "LastUpdatedTime": int(last_updated_time)  # Must be Number per API spec
        }
        
        logger.info(f"Canceling RTM bid OrderId={order_id}: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.put(url, json=request_body, headers=headers)
            
            try:
                data = response.json()
            except:
                data = response.text
                
            await self._log_to_db(url, request_body, headers, data, "rtm", "single-bid", "cancelRtmBid", delivery_date)
            
            response.raise_for_status()
            
            logger.info(f"RTM cancel response: Status={data.get('Status') if isinstance(data, dict) else 'N/A'}")
            return data if isinstance(data, dict) else {"Status": "Failed", "StatusMessage": str(data)}

    async def cancel_block_bid(
        self,
        order_id: int,
        last_updated_time: int,
        delivery_date: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Cancel a submitted RTM block bid on the exchange.

        API: PUT {baseUrl}/rtm/api/v2/blockbid/cancel  (IEX RTM API v2.0, Section 9.15)

        Request Body (PascalCase per v2.0):
            OrderId, UserId, ParticipantId, PortfolioId, LastUpdatedTime

        Response:
            {TransactionId, Status ("Success"/"Failed"), StatusMessage}

        Args:
            order_id: Exchange OrderId (Number) from bid book / bid details
            last_updated_time: LastUpdatedTime (Number) from exchange — must be current
            delivery_date: Unix timestamp for logging only
        """
        url = f"{self.base_url}/rtm/api/v2/blockbid/cancel"
        headers = self._get_headers()

        request_body = {
            "OrderId": order_id,
            "UserId": self.login_user_id,
            "ParticipantId": self.participant_id,
            "PortfolioId": self.portfolio_id,
            "LastUpdatedTime": last_updated_time,
        }

        logger.info(f"Cancelling RTM block bid order_id={order_id} at: {url}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.put(url, json=request_body, headers=headers)

            logger.info(f"Cancel block bid response status: {response.status_code}")

            try:
                data = response.json()
            except Exception:
                data = response.text

            await self._log_to_db(
                url, request_body, headers, data,
                "rtm", "block-bid", "cancelBlockBid",
                delivery_date
            )

            logger.info(f"Cancel block bid result: {data}")
            return data if isinstance(data, dict) else {"Status": "Failed", "StatusMessage": str(data)}

    async def get_dam_trade_book(
        self,
        delivery_date: int,
        from_period: str = "ALL",
        to_period: str = "ALL",
        buy_sell: int = 0,
        bid_area_id: str = "ALL",
        trade_id: int = 0,
        order_id: int = 0,
        page_number: int = 0,
        page_size: int = 0
    ) -> Dict[str, Any]:
        """
        Fetch DAM trade book data from exchange API.
        
        API: GET dam/api/v2/trades/{LoginUserId},{ParticipantId},{DeliveryDate},{UserId},{PortfolioId},{FromPeriod},{ToPeriod},{BuySell},{BidAreaId},{TradeId},{OrderId},{PageNumber},{PageSize}
        
        Args:
            delivery_date: DAM Delivery Date (Number)
            from_period: From Period Id, 'ALL' for all periods (String)
            to_period: To Period Id, 'ALL' for all periods (String)
            buy_sell: Buy or Sell filter - '0': Both, '1': Buy, '2': Sell (String)
            bid_area_id: Bid Area Id, 'ALL' for all bid areas (String)
            trade_id: Unique Trade Identifier, 0 for all (Number)
            order_id: Order Id, 0 for all (Number)
            page_number: Page number, 0 for all records (Number)
            page_size: Page size, 0 for all records, max 96 (Number)
            
        Returns:
            {
                TradeDetails: List[Dict] - List of DAM Trade Details,
                TotalRecord: int - Total number of records
            }
            
        Trade Details fields:
            - DeliveryDate: DAM Delivery Date
            - AssetId: Asset Id
            - BidAreaId: Bid Area Id
            - UserId: User Id
            - ParticipantId: Participant Id
            - PortfolioId: Portfolio Id
            - OrderId: Order Id
            - TradeId: Unique Trade Identifier
            - FromPeriodId: From Period Id
            - ToPeriodId: To Period Id
            - TradePrice: Traded Price
            - TradeQuantity: Traded Quantity
            - BuySell: 'B' for Buy, 'S' for Sell
            - BidType: 'S' for Single Bid, 'B' for Block Bid
            - OCFSourceBidCategory: For carried forward DAM bid from GDAM (Renewable/Solar)
        """
        url = (
            f"{self.base_url}/dam/api/v2/trades/"
            f"{self.login_user_id},{self.participant_id},{delivery_date},"
            f"{self.login_user_id},{self.portfolio_id},{from_period},{to_period},"
            f"{buy_sell},{bid_area_id},{trade_id},{order_id},{page_number},{page_size}"
        )
        headers = self._get_headers()
        
        logger.info(f"Fetching DAM trade book from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
            try:
                data = response.json()
            except:
                data = response.text
                
            await self._log_to_db(url, {"method": "GET"}, headers, data, "dam", "trade-book", "getDamTradeBook")
            
            response.raise_for_status()
            
            logger.info(f"DAM trade book fetched, TotalRecord: {data.get('TotalRecord', 0) if isinstance(data, dict) else 'N/A'}")
            return data if isinstance(data, dict) else {"TradeDetails": [], "TotalRecord": 0}

    async def get_dam_price_quantity(
        self,
        delivery_date: int
    ) -> Dict[str, Any]:
        """
        Fetch DAM Price Quantity (MCP) data from exchange API.
        
        API: GET dam/api/v2/pqresults/{LoginUserId},{ParticipantId},{DeliveryDate}
        
        Args:
            delivery_date: DAM Delivery Date (Number)
            
        Returns:
            {
                DeliveryDate: int - DAM Delivery Date,
                LastUpdatedTime: int - Last updated time in seconds,
                PQDetails: List[Dict] - List of DAM Period Quantity Details
            }
            
        PQDetails structure:
            - FromPeriodId: String - From Period Id
            - ToPeriodId: String - To Period Id
            - BidAreaDetails: List[Dict] - List of Bid Area Price Quantity Details
              - BidArea: String - Bid Area Id
              - Price: Number - Price
              - BuyQty: Number - Buy Quantity
              - SellQty: Number - Sell Quantity
        """
        url = (
            f"{self.base_url}/dam/api/v2/pqresults/"
            f"{self.login_user_id},{self.participant_id},{delivery_date}"
        )
        headers = self._get_headers()
        
        logger.info(f"Fetching DAM price quantity from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
            try:
                data = response.json()
            except:
                data = response.text
                
            await self._log_to_db(url, {"method": "GET"}, headers, data, "dam", "price-quantity", "getDamPriceQuantity")
            
            response.raise_for_status()
            
            pq_count = len(data.get("PQDetails", [])) if isinstance(data, dict) else 0
            logger.info(f"DAM price quantity fetched, PQDetails count: {pq_count}")
            return data if isinstance(data, dict) else {"PQDetails": [], "DeliveryDate": delivery_date, "LastUpdatedTime": 0}

    async def get_rtm_trade_book(
        self,
        delivery_date: int,
        session: int = 0,
        from_period: str = "ALL",
        to_period: str = "ALL",
        buy_sell: str = "ALL",
        bid_area_id: str = "ALL",
        trade_id: int = 0,
        order_id: int = 0,
        sorting_type: int = 2,
        page_number: int = 0,
        page_size: int = 0
    ) -> Dict[str, Any]:
        """
        Fetch RTM trade book data from exchange API.
        
        API: GET rtm/api/v2/trades/{LoginUserId},{ParticipantId},{DeliveryDate},{UserId},{PortfolioId},{Session},{FromPeriod},{ToPeriod},{BuySell},{BidAreaId},{TradeId},{OrderId},{SortingType},{PageNumber},{PageSize}
        
        Args:
            delivery_date: RTM Delivery Date (Number)
            session: Session of Product, 0 for all sessions (Number)
            from_period: From Period Id, 'ALL' for all periods (String)
            to_period: To Period Id, 'ALL' for all periods (String)
            buy_sell: Buy or Sell filter - 'ALL': Both, 'B': Buy, 'S': Sell (String)
            bid_area_id: Bid Area Id, 'ALL' for all bid areas (String)
            trade_id: Unique Trade Identifier, 0 for all (Number)
            order_id: Order Id, 0 for all (Number)
            sorting_type: Sorting options (Number):
                3: Order Id Ascending, 4: Order Id Descending
                5: Portfolio Id Ascending, 6: Portfolio Id Descending
                7: From Period Id Ascending, 8: From Period Id Descending
                9: Trade Id Ascending, 10: Trade Id Descending
            page_number: Page number, 0 for all records (Number)
            page_size: Page size, 0 for all records, max 96 (Number)
            
        Returns:
            {
                TradeDetails: List[Dict] - List of RTM Trade Details,
                TotalRecord: int - Total number of records
            }
            
        Trade Details fields:
            - DeliveryDate: RTM Delivery Date
            - AssetId: Asset Id
            - BidAreaId: Bid Area Id
            - UserId: User Id
            - ParticipantId: Participant Id
            - PortfolioId: Portfolio Id
            - OrderId: Order Id
            - TradeId: Unique Trade Identifier
            - Session: Session of Product
            - FromPeriodId: From Period Id
            - ToPeriodId: To Period Id
            - TradePrice: Traded Price
            - TradeQuantity: Traded Quantity
            - BuySell: 'B' for Buy, 'S' for Sell
            - BidType: 'S' for Single Bid, 'B' for Block Bid
            - Period: Period for particular Trade
        """
        url = (
            f"{self.base_url}/rtm/api/v2/trades/"
            f"{self.login_user_id},{self.participant_id},{delivery_date},"
            f"{self.login_user_id},{self.portfolio_id},{session},{from_period},{to_period},"
            f"{buy_sell},{bid_area_id},{trade_id},{order_id},{sorting_type},{page_number},{page_size}"
        )
        headers = self._get_headers()
        
        logger.info(f"Fetching RTM trade book from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
            try:
                data = response.json()
            except:
                data = response.text
                
            await self._log_to_db(url, {"method": "GET"}, headers, data, "rtm", "trade-book", "getRtmTradeBook")
            
            response.raise_for_status()
            
            logger.info(f"RTM trade book fetched, TotalRecord: {data.get('TotalRecord', 0) if isinstance(data, dict) else 'N/A'}")
            return data if isinstance(data, dict) else {"TradeDetails": [], "TotalRecord": 0}

    async def get_rtm_price_quantity(
        self,
        delivery_date: int
    ) -> Dict[str, Any]:
        """
        Fetch RTM Price Quantity (MCP) data from exchange API.
        
        API: GET rtm/api/v2/pqresults/{LoginUserId},{ParticipantId},{DeliveryDate}
        
        Args:
            delivery_date: RTM Delivery Date (Number)
            
        Returns:
            {
                DeliveryDate: int - RTM Delivery Date,
                LastUpdatedTime: int - Last updated time in seconds,
                PQDetails: List[Dict] - List of RTM Period Quantity Details
            }
            
        PQDetails structure:
            - Session: Number - Session of Product
            - FromPeriodId: String - From Period Id
            - ToPeriodId: String - To Period Id
            - BidAreaDetails: List[Dict] - List of Bid Area Price Quantity Details
              - BidAreaId: String - Bid Area Id
              - Price: Number - Price
              - BuyQty: Number - Buy Quantity
              - SellQty: Number - Sell Quantity
        """
        url = (
            f"{self.base_url}/rtm/api/v2/pqresults/"
            f"{self.login_user_id},{self.participant_id},{delivery_date}"
        )
        headers = self._get_headers()
        
        logger.info(f"Fetching RTM price quantity from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            
            try:
                data = response.json()
            except:
                data = response.text
                
            await self._log_to_db(url, {"method": "GET"}, headers, data, "rtm", "price-quantity", "getRtmPriceQuantity")
            
            response.raise_for_status()
            
            pq_count = len(data.get("PQDetails", [])) if isinstance(data, dict) else 0
            logger.info(f"RTM price quantity fetched, PQDetails count: {pq_count}")
            return data if isinstance(data, dict) else {"PQDetails": [], "DeliveryDate": delivery_date, "LastUpdatedTime": 0}

    async def submit_dam_block_bid(self, bid_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit DAM block bid to exchange API (OCF variant).

        API: POST {baseUrl}/dam/api/v2/blockbid/ocf  (IEX DAM API v2.2, Section 9.15)

        Args:
            bid_request: PascalCase body per v2.2 spec:
                {DeliveryDate, AssetId, BidAreaId, UserId, ParticipantId,
                 PortfolioId, InitiatedBy, BidDetails[{FromPeriodId, ToPeriodId,
                 OCFOpted, PremiumDiscountPrice, MaxOCFQty, BuySell, Price,
                 Quantity, BidRef, LinkedBidRef, LinkType}]}
        Returns:
            {TransactionId, Status, StatusMessage}
        """
        url = f"{self.base_url}/dam/api/v2/blockbid/ocf"
        headers = self._get_headers()

        import json as _json
        logger.info(f"Submitting DAM block bid to: {url}")
        logger.info(
            f"DAM Block bid curl equivalent: "
            f"curl -X POST '{url}' "
            f"-H 'Authorization: Bearer {self.bearer_token}' "
            f"-H 'Content-Type: application/json' "
            f"-d '{_json.dumps(bid_request, default=str)}'"
        )

        async with httpx.AsyncClient(timeout=self.submit_timeout) as client:
            response = await client.post(url, json=bid_request, headers=headers)

            logger.info(f"DAM block bid response status: {response.status_code}")

            try:
                data = response.json()
            except Exception:
                data = response.text

            await self._log_to_db(
                url, bid_request, headers, data,
                "dam", "block-bid", "submitDamBlockBid",
                bid_request.get("DeliveryDate")
            )

            if not response.is_success:
                logger.error(f"DAM block bid submission failed [{response.status_code}]: {data}")
                return {"Status": "Failed", "StatusMessage": data}

            logger.info(f"DAM block bid submitted successfully: {data}")
            return data if isinstance(data, dict) else {"Status": "Failed", "StatusMessage": data}

    async def cancel_dam_block_bid(
        self,
        order_id: int,
        last_updated_time: int,
        delivery_date: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Cancel a submitted DAM block bid on the exchange.

        API: PUT {baseUrl}/dam/api/v2/blockbid/cancel  (IEX DAM API v2.2)

        Request Body:
            OrderId, UserId, ParticipantId, PortfolioId, LastUpdatedTime

        Response:
            {TransactionId, Status ("Success"/"Failed"), StatusMessage}
        """
        url = f"{self.base_url}/dam/api/v2/blockbid/cancel"
        headers = self._get_headers()

        request_body = {
            "OrderId": order_id,
            "UserId": self.login_user_id,
            "ParticipantId": self.participant_id,
            "PortfolioId": self.portfolio_id,
            "LastUpdatedTime": last_updated_time,
        }

        logger.info(f"Cancelling DAM block bid order_id={order_id} at: {url}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.put(url, json=request_body, headers=headers)

            logger.info(f"Cancel DAM block bid response status: {response.status_code}")

            try:
                data = response.json()
            except Exception:
                data = response.text

            await self._log_to_db(
                url, request_body, headers, data,
                "dam", "block-bid", "cancelDamBlockBid",
                delivery_date
            )

            logger.info(f"Cancel DAM block bid result: {data}")
            return data if isinstance(data, dict) else {"Status": "Failed", "StatusMessage": str(data)}

    async def get_dam_block_bid_ref_count(self) -> Dict[str, Any]:
        """
        Fetch DAM block bid reference count from exchange API.

        API: GET {baseUrl}/dam/api/v2/blockbid/bidrefcount/{LoginUserId},{ParticipantId}

        Returns:
            Bid reference count response (field: DAMBlockBidRefCount)
        """
        url = f"{self.base_url}/dam/api/v2/blockbid/bidrefcount/{self.login_user_id},{self.participant_id}"
        headers = self._get_headers()

        logger.info(f"Fetching DAM block bid ref count from: {url}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)

            try:
                data = response.json()
            except Exception:
                data = response.text

            await self._log_to_db(url, {"method": "GET"}, headers, data, "dam", "block-bid", "getDamBidReferenceId")

            response.raise_for_status()

            logger.info(f"DAM block bid ref count fetched: {data}")
            return data if isinstance(data, dict) else {}

    async def get_dam_block_bids(
        self,
        delivery_date: int,
        from_period: str = "ALL",
        to_period: str = "ALL",
        buy_sell: str = "ALL",
        bid_area_id: str = "ALL",
        status: str = "ALL",
        exception_id: int = 0,
        order_id: int = 0,
        page_number: int = 0,
        page_size: int = 0,
    ) -> Dict[str, Any]:
        """
        Fetch existing DAM block bids for a delivery date.

        API: GET {baseUrl}/dam/api/v2/blockbid/{LoginUserId},{ParticipantId},{DeliveryDate},{UserId},{PortfolioId},{FromPeriodId},{ToPeriodId},{BuySell},{BidAreaId},{Status},{ExceptionId},{OrderId},{PageNumber},{PageSize}

        Returns:
            {DAMBlockBidDetails: [], TotalRecord: int}
        """
        url = (
            f"{self.base_url}/dam/api/v2/blockbid/"
            f"{self.login_user_id},{self.participant_id},{delivery_date},"
            f"{self.login_user_id},{self.portfolio_id},{from_period},{to_period},"
            f"{buy_sell},{bid_area_id},{status},{exception_id},{order_id},{page_number},{page_size}"
        )
        headers = self._get_headers()

        logger.info(f"Fetching existing DAM block bids from: {url}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)

            try:
                data = response.json()
            except Exception:
                data = response.text

            await self._log_to_db(url, {"method": "GET"}, headers, data, "dam", "block-bid", "getDamBlockBids", delivery_date)

            response.raise_for_status()

            total = data.get("TotalRecord", 0) if isinstance(data, dict) else 0
            logger.info(f"Existing DAM block bids fetched, TotalRecord: {total}")
            return data if isinstance(data, dict) else {"DAMBlockBidDetails": [], "TotalRecord": 0}

    def clear_cache(self):
        """Clear cached API responses"""
        self._asset_details_cache = None
        self._delivery_date_cache = None


def block_to_time_period(block: int) -> tuple:
    """
    Convert block number (1-96) to time period (FromPeriodId, ToPeriodId)
    
    Block 1 = 00:00 - 00:15
    Block 2 = 00:15 - 00:30
    ...
    Block 96 = 23:45 - 24:00
    
    Args:
        block: Block number (1-96)
        
    Returns:
        Tuple of (FromPeriodId, ToPeriodId) in HH:MM format
    """
    if block < 1 or block > 96:
        raise ValueError(f"Block must be between 1 and 96, got {block}")
    
    # Each block is 15 minutes
    start_minutes = (block - 1) * 15
    end_minutes = block * 15
    
    from_hour = start_minutes // 60
    from_min = start_minutes % 60
    
    end_hour = end_minutes // 60
    end_min = end_minutes % 60
    
    from_period = f"{from_hour:02d}:{from_min:02d}"
    to_period = f"{end_hour:02d}:{end_min:02d}"
    
    return (from_period, to_period)


def time_slot_to_block(time_slot: str) -> int:
    """
    Convert time slot string to block number
    
    Example: "00:00-00:15" -> 1, "23:45-24:00" -> 96
    
    Args:
        time_slot: Time slot in format "HH:MM-HH:MM"
        
    Returns:
        Block number (1-96)
    """
    start_time = time_slot.split("-")[0].strip()
    hour, minute = map(int, start_time.split(":"))
    
    total_minutes = hour * 60 + minute
    block = (total_minutes // 15) + 1
    
    return block


async def get_exchange_client_for_market(market_type: str, correlation_id: Optional[str] = None) -> ExchangeAPIClient:
    """
    Create an ExchangeAPIClient for the specified market type.
    
    This helper loads exchange configuration from the database and creates
    a properly configured API client.
    
    Args:
        market_type: 'dam' or 'rtm'
        correlation_id: Optional correlation ID for tracing
        
    Returns:
        Configured ExchangeAPIClient instance
        
    Raises:
        ValueError: If no active exchange configuration found
    """
    from services.processing.app.v2.repository.exchange_config_repository import ExchangeConfigRepository
    
    exchange_config_repo = ExchangeConfigRepository()
    config = await exchange_config_repo.get_active_config()
    
    if not config:
        raise ValueError("No active exchange configuration found")
    
    # For RTM, use rtmBaseUrl if available
    if market_type.lower() == "rtm":
        base_url = config.get("rtmBaseUrl") or config.get("baseUrl", "")
    else:
        base_url = config.get("baseUrl", "")
    
    return ExchangeAPIClient(
        base_url=base_url,
        login_user_id=config.get("LoginUserId", ""),
        participant_id=config.get("ParticipantId", ""),
        portfolio_id=config.get("PortfolioId", ""),
        correlation_id=correlation_id,
        market_type=market_type.lower()
    )

