"""
Scheduler Exchange API Client
Dedicated client for interacting with Exchange APIs from the Scheduler Service.
Avoids dependency on Processing Service code.
"""

import logging
import httpx
from datetime import datetime
from typing import Dict, Any, Optional, List
from pymongo import ASCENDING
from pymongo.errors import OperationFailure
from services.scheduler.app.core.config import settings
from libs.database.base_repository import BaseRepository
from libs.database.mongo_client_factory import get_scheduler_client

logger = logging.getLogger(__name__)

class SchedulerExchangeAPIClient:
    """
    Client for Exchange API interactions within Scheduler.
    Handles authentication, config retrieval, and API calls with logging.
    """
    
    def __init__(self):
        self.db_uri = settings.MONGODB_URI
        self.db_name = settings.MONGODB_DATABASE
        self.timeout = 30.0
        self.asset_details_cache = BaseRepository('asset_master', self.db_uri, self.db_name)
        self._apilog_index_ensured = False

    def _get_db(self):
        return get_scheduler_client(self.db_uri)[self.db_name]

    async def _get_exchange_config(self, market_type: str) -> Optional[Dict[str, Any]]:
        """
        Fetch active exchange configuration matching logic in Processing Service.
        """
        try:
            db = self._get_db()
            config_collection = db["exchange_config"]
            
            if market_type.lower() == "dam":
                # Matches DAMTransactionService calling exchange_config_repo.get_active_config()
                # Query: {"is_active": True}
                query = {
                    "is_active": True,
                    "is_deleted": {"$ne": True}, 
                    "bidType": "single", 
                    "market": "DAM"
                }
                logger.info(f"Fetching DAM config with query: {query}")
                return await config_collection.find_one(query)
                
            elif market_type.lower() == "rtm":
                # Matches RTMTransactionService calling exchange_config_repo.get_bidType_market_active_config("single", "RTM")
                # Query: {"is_active": True, "is_deleted": {"$ne": True}, "bidType": "single", "market": "RTM"}
                query = {
                    "is_active": True, 
                    "is_deleted": {"$ne": True}, 
                    "bidType": "single", 
                    "market": "RTM"
                }
                logger.info(f"Fetching RTM config with query: {query}")
                return await config_collection.find_one(query)
            
            else:
                # Fallback for other markets if any
                query = {"is_active": True, "market": {"$regex": f"^{market_type}$", "$options": "i"}}
                return await config_collection.find_one(query)
            
        except Exception as e:
            logger.error(f"Error fetching exchange config: {e}")
            return None

    async def _ensure_apilog_ttl_index(self, db):
        if self._apilog_index_ensured:
            return
        try:
            log_collection = db["iex_integration_apiLog"]
            await log_collection.create_index(
                [("Timestamp", ASCENDING)],
                expireAfterSeconds=604800,
                name="ttl_timestamp_7d",
                background=True
            )
            self._apilog_index_ensured = True
            logger.info("Created TTL index on iex_integration_apiLog")
        except OperationFailure as e:
            if e.code == 86:
                logger.debug("TTL index already exists on iex_integration_apiLog")
                self._apilog_index_ensured = True
            else:
                logger.error("Error creating TTL index on iex_integration_apiLog: %s", e)
        except Exception as e:
            logger.error("Error ensuring TTL index on iex_integration_apiLog: %s", e)

    async def _log_to_db(self, url: str, method: str, headers: Dict, request_body: Any, response_body: Any, market_type: str, api_method: str):
        """
        Log API interaction to iex_integration_apilog.
        """
        try:
            db = self._get_db()
            await self._ensure_apilog_ttl_index(db)
            log_collection = db["iex_integration_apiLog"]
            
            log_entry = {
                "Url": url,
                "Method": method,
                "Headers": headers,
                "RequestBody": request_body,
                "ResponseBody": response_body,
                "MarketType": market_type,
                "ApiMethod": api_method, 
                "Timestamp": datetime.now(),
                "CreatedAt": datetime.now()
            }
            
            await log_collection.insert_one(log_entry)
            
        except Exception as e:
            # Don't fail the job just because logging failed, but error retrieval is important
            logger.error(f"Failed to log API call: {e}")

    async def get_delivery_dates(self, market_type: str) -> Dict[str, Any]:
        """
        Fetch delivery dates from exchange API.
        API: GET {baseUrl}/{marketType}/api/v2/deliverydates/{LoginUserId},{ParticipantId}
        """
        config = await self._get_exchange_config(market_type)
        if not config:
            raise ValueError(f"Exchange Configuration not found for {market_type}")
            
        base_url = config.get("baseUrl") or config.get("BaseUrl")
        login_user_id = config.get("LoginUserId")
        participant_id = config.get("ParticipantId")
        bearer_token = config.get("BearerToken")
        
        url = f"{base_url}/{market_type}/api/v2/deliverydates/{login_user_id},{participant_id}"
        headers = {"Content-Type": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        
        return await self._execute_and_log(url, "GET", headers, None, market_type, "getDeliveryDates")

    async def get_valid_delivery_date_dam(self) -> int:
        """
        Fetch and return valid DAM delivery date.
        """
        data = await self.get_delivery_dates("dam")
        # DAM response usually has direct keys or a list. 
        # Based on user input: {"DeliveryDateId": "T+1", "DeliveryDate": 1770508800}
        # But let's check if it's a list wrapper like RTM or direct.
        # Implementation in dam_transaction_service uses: delivery_response.get("DeliveryDate", 0)
        # So it seems to be at root.
        
        delivery_date = data.get("DeliveryDate")
        if not delivery_date:
            # Check if it's inside a list
            if "DeliveryDates" in data and data["DeliveryDates"]:
                delivery_date = data["DeliveryDates"][0].get("DeliveryDate")
        
        if not delivery_date:
             raise ValueError("Could not find valid DeliveryDate in DAM response")
             
        return int(delivery_date)

    async def get_valid_delivery_date_rtm(self) -> List[int]:
        """
        Fetch and return all valid RTM delivery dates.
        """
        data = await self.get_delivery_dates("rtm")
        # RTM response: {"DeliveryDates": [{"DeliveryDateId": "T", ...}, ...]}
        
        delivery_dates = data.get("DeliveryDates", [])
        if not delivery_dates:
             # Fallback if list is at root
             if isinstance(data, list):
                 delivery_dates = data
             elif "DeliveryDate" in data:
                 # Single object fallback
                 delivery_dates = [data]
        
        if not delivery_dates:
            raise ValueError("Could not find valid DeliveryDates in RTM response")

        valid_dates = []
        for item in delivery_dates:
            d_date = item.get("DeliveryDate")
            if d_date:
                valid_dates.append(int(d_date))
        
        if not valid_dates:
             raise ValueError("Could not find any valid DeliveryDate in RTM response")
             
        return valid_dates

    # --- Live (Production) Methods ---

    async def get_live_delivery_dates(self, market_type: str) -> Dict[str, Any]:
        """
        Fetch delivery dates from Production exchange API.
        """
        config = await self._get_live_exchange_config(market_type)
        if not config:
            raise ValueError(f"Live Exchange Configuration not found for {market_type}")
            
        base_url = config.get("baseUrl") or config.get("BaseUrl")
        login_user_id = config.get("LoginUserId")
        participant_id = config.get("ParticipantId")
        bearer_token = config.get("BearerToken")
        
        url = f"{base_url}/{market_type}/api/v2/deliverydates/{login_user_id},{participant_id}"
        headers = {"Content-Type": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        
        return await self._execute_and_log(url, "GET", headers, None, market_type, "getLiveDeliveryDates")

    async def get_live_valid_delivery_date_dam(self) -> int:
        """
        Fetch and return valid DAM delivery date from Production.
        """
        data = await self.get_live_delivery_dates("dam")
        delivery_date = data.get("DeliveryDate")
        if not delivery_date:
            if "DeliveryDates" in data and data["DeliveryDates"]:
                delivery_date = data["DeliveryDates"][0].get("DeliveryDate")
        
        if not delivery_date:
             raise ValueError("Could not find valid Production DeliveryDate in DAM response")
             
        return int(delivery_date)

    async def get_live_valid_delivery_date_rtm(self) -> List[int]:
        """
        Fetch and return all valid RTM delivery dates from Production.
        """
        data = await self.get_live_delivery_dates("rtm")
        delivery_dates = data.get("DeliveryDates", [])
        if not delivery_dates:
             if isinstance(data, list): delivery_dates = data
             elif "DeliveryDate" in data: delivery_dates = [data]
        
        if not delivery_dates:
            raise ValueError("Could not find valid Production DeliveryDates in RTM response")

        valid_dates = []
        for item in delivery_dates:
            d_date = item.get("DeliveryDate")
            if d_date:
                valid_dates.append(int(d_date))
        
        if not valid_dates:
             raise ValueError("Could not find any valid Production DeliveryDate in RTM response")
             
        return valid_dates

    async def get_single_bid_dam(self, delivery_date: int) -> Dict[str, Any]:
        """
        Fetch DAM Single Bid Book.
        API: /dam/api/v2/singlebid/...
        """
        market = "dam"
        config = await self._get_exchange_config(market)
        if not config:
            raise ValueError("Exchange Configuration not found for DAM")
            
        base_url = config.get("baseUrl") or config.get("BaseUrl")
        login_user_id = config.get("LoginUserId")
        participant_id = config.get("ParticipantId")
        portfolio_id = config.get("PortfolioId")
        bearer_token = config.get("BearerToken")
        
        # DAM Params - Corrected based on user feedback
        # Remove sorting_type as it's not present in DAM API
        period = "ALL"
        bid_area_id = "S2"
        status = "ALL"
        exception_id = 0
        order_id = 0
        # sorting_type removed
        page_number = 0
        page_size = 0
        
        url = (
            f"{base_url}/dam/api/v2/singlebid/"
            f"{login_user_id},{participant_id},{delivery_date},"
            f"{login_user_id},{portfolio_id},{period},"
            f"{bid_area_id},{status},{exception_id},{order_id},{page_number},{page_size}"
        )
        
        headers = {"Content-Type": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        return await self._execute_and_log(url, "GET", headers, None, market, "getSingleBidDam")

    async def get_block_bid_rtm(self, delivery_date: int) -> Dict[str, Any]:
        """
        Fetch RTM Block Bid Book from exchange.
        API: GET /rtm/api/v2/blockbid/{UserId},{ParticipantId},{DeliveryDate},{PortfolioId},{BidAreaId},{Session},{BidReferenceId},{Status},{SortingType},{PageNumber},{PageSize}

        Returns:
            { RTMBlockBidDetails: [...], TotalRecord: int }
        """
        market = "rtm"
        config = await self._get_exchange_config(market)
        if not config:
            raise ValueError("Exchange Configuration not found for RTM")

        base_url      = config.get("baseUrl") or config.get("BaseUrl")
        login_user_id = config.get("LoginUserId")
        participant_id = config.get("ParticipantId")
        portfolio_id  = config.get("PortfolioId")
        bearer_token  = config.get("BearerToken")
        bid_area_id   = config.get("BidAreaId", "S2")

        # Fixed params — fetch all records for the delivery date
        session        = 0
        bid_ref_id     = 0
        status         = "ALL"
        sorting_type   = 2
        page_number    = 0
        page_size      = 0

        url = (
            f"{base_url}/rtm/api/v2/blockbid/"
            f"{login_user_id},{participant_id},{delivery_date},"
            f"{portfolio_id},{bid_area_id},{session},{bid_ref_id},"
            f"{status},{sorting_type},{page_number},{page_size}"
        )

        headers = {"Content-Type": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        return await self._execute_and_log(url, "GET", headers, None, market, "getBlockBidRtm")

    async def get_live_block_bid_rtm(self, delivery_date: int) -> Dict[str, Any]:
        """
        Fetch RTM Block Bid Book from live exchange.
        Same as get_block_bid_rtm but uses live exchange config.
        Returns:
            { RTMBlockBidDetails: [...], TotalRecord: int }
        """
        market = "rtm"
        config = await self._get_live_exchange_config(market)
        if not config:
            raise ValueError("Exchange Configuration not found for RTM (live)")

        base_url      = config.get("baseUrl") or config.get("BaseUrl")
        login_user_id = config.get("LoginUserId")
        participant_id = config.get("ParticipantId")
        portfolio_id  = config.get("PortfolioId")
        bearer_token  = config.get("BearerToken")
        bid_area_id   = config.get("BidAreaId", "S2")

        session        = 0
        bid_ref_id     = 0
        status         = "ALL"
        sorting_type   = 2
        page_number    = 0
        page_size      = 0

        url = (
            f"{base_url}/rtm/api/v2/blockbid/"
            f"{login_user_id},{participant_id},{delivery_date},"
            f"{portfolio_id},{bid_area_id},{session},{bid_ref_id},"
            f"{status},{sorting_type},{page_number},{page_size}"
        )

        headers = {"Content-Type": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        return await self._execute_and_log(url, "GET", headers, None, market, "getLiveBlockBidRtm")

    async def get_single_bid_rtm(self, delivery_date: int) -> Dict[str, Any]:
        """
        Fetch RTM Single Bid Book.
        API: /rtm/api/v2/singlebid/...
        """
        market = "rtm"
        config = await self._get_exchange_config(market)
        if not config:
            raise ValueError("Exchange Configuration not found for RTM")
            
        base_url = config.get("baseUrl") or config.get("BaseUrl")
        login_user_id = config.get("LoginUserId")
        participant_id = config.get("ParticipantId")
        portfolio_id = config.get("PortfolioId")
        bearer_token = config.get("BearerToken")
        
        # RTM Params
        # User curl: .../singlebid/PTCA1,N2DL0PTC0000,1770508800,ALL,S2PY0PTC0136,0,ALL,S2,ALL,2,0,2,0,0
        # Mapped to: ...{user},{participant},{date},{UserFilter},{portfolio},{session},{period},{bid_area},{status},{exception},{order},{sorting},{pg_num},{pg_size}
        
        user_filter = "ALL" # User used ALL instead of login_user_id
        session = 0
        period = "ALL"
        bid_area_id = "S2" # Default for this user
        status = "ALL"
        exception_id = 0 # User used 2, but 0 (All?) is safer default.
        order_id = 0
        sorting_type = 2
        page_number = 0
        page_size = 0
        
        url = (
            f"{base_url}/rtm/api/v2/singlebid/"
            f"{login_user_id},{participant_id},{delivery_date},"
            f"{user_filter},{portfolio_id},{session},{period},"
            f"{bid_area_id},{status},{exception_id},{order_id},{sorting_type},{page_number},{page_size}"
        )
        
        headers = {"Content-Type": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        return await self._execute_and_log(url, "GET", headers, None, market, "getSingleBidRtm")

    async def _execute_and_log(self, url: str, method: str, headers: Dict, body: Any, market: str, api_method: str) -> Dict[str, Any]:
        now = datetime.now().strftime("%H:%M:%S.%f")
        logger.info(f"TESTING LOGS: [{now}] [{market.upper()}] Request: {url}")
        
        response_data = None
        response_text = ""
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                start_time_req = datetime.now()
                if method == "GET":
                    response = await client.get(url, headers=headers)
                else:
                    response = await client.post(url, headers=headers, json=body)
                
                end_time_req = datetime.now()
                duration = (end_time_req - start_time_req).total_seconds()
                now = end_time_req.strftime("%H:%M:%S.%f")
                logger.info(f"TESTING LOGS: [{now}] [{market.upper()}] Response received in {duration:.3f}s")

                response_text = response.text
                try:
                    response_data = response.json()
                except:
                    response_data = {"raw": response_text}
                
                # Log to DB
                await self._log_to_db(url, method, headers, body, response_data, market, api_method)
                
                response.raise_for_status()
                return response_data if isinstance(response_data, dict) else {"SingleBidDetails": [], "TotalRecord": 0}

            except httpx.TimeoutException:
                now = datetime.now().strftime("%H:%M:%S.%f")
                logger.error(f"TESTING LOGS: [{now}] [{market.upper()}] Request TIMED OUT after {self.timeout}s")
                await self._log_to_db(url, method, headers, body, "TIMEOUT", market, api_method)
                raise
            except Exception as e:
                now = datetime.now().strftime("%H:%M:%S.%f")
                # Log failure
                await self._log_to_db(url, method, headers, body, str(e), market, api_method)
                logger.error(f"TESTING LOGS: [{now}] [{market.upper()}] API Request Failed: {e}")
                raise

    async def _get_live_exchange_config(self, market_type: str) -> Optional[Dict[str, Any]]:
        """
        Fetch active exchange configuration matching logic in Processing Service.
        """
        try:
            db = self._get_db()
            config_collection = db["exchange_config_live"]
            
            if market_type.lower() == "dam":
                # Matches DAMTransactionService calling exchange_config_repo.get_active_config()
                # Query: {"is_active": True}
                query = {
                    "is_active": True,
                    "is_deleted": {"$ne": True}, 
                    "bidType": "single", 
                    "market": "DAM"
                }
                logger.info(f"Fetching DAM config with query: {query}")
                return await config_collection.find_one(query)
                
            elif market_type.lower() == "rtm":
                # Matches RTMTransactionService calling exchange_config_repo.get_bidType_market_active_config("single", "RTM")
                # Query: {"is_active": True, "is_deleted": {"$ne": True}, "bidType": "single", "market": "RTM"}
                query = {
                    "is_active": True, 
                    "is_deleted": {"$ne": True}, 
                    "bidType": "single", 
                    "market": "RTM"
                }
                logger.info(f"Fetching RTM config with query: {query}")
                return await config_collection.find_one(query)
            
            else:
                # Fallback for other markets if any
                query = {"is_active": True, "market": {"$regex": f"^{market_type}$", "$options": "i"}}
                return await config_collection.find_one(query)
            
        except Exception as e:
            logger.error(f"Error fetching exchange config: {e}")
            return None

    
    async def get_live_single_bid_dam(self, delivery_date: int) -> Dict[str, Any]:
        """
        Fetch DAM Single Bid Book.
        API: /dam/api/v2/singlebid/...
        """
        market = "dam"
        config = await self._get_live_exchange_config(market)
        if not config:
            raise ValueError("Exchange Configuration not found for DAM")
            
        base_url = config.get("baseUrl") or config.get("BaseUrl")
        login_user_id = config.get("LoginUserId")
        participant_id = config.get("ParticipantId")
        portfolio_id = config.get("PortfolioId")
        bearer_token = config.get("BearerToken")
        
        # DAM Params - Corrected based on user feedback
        # Remove sorting_type as it's not present in DAM API
        period = "ALL"
        bid_area_id = "S2"
        status = "Executed"
        exception_id = 0
        order_id = 0
        # sorting_type removed
        page_number = 0
        page_size = 0
        
        url = (
            f"{base_url}/dam/api/v2/singlebid/"
            f"{login_user_id},{participant_id},{delivery_date},"
            f"{login_user_id},{portfolio_id},{period},"
            f"{bid_area_id},{status},{exception_id},{order_id},{page_number},{page_size}"
        )
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer_token}"
        }
        
        return await self._execute_and_log(url, "GET", headers, None, market, "getSingleBidDam")

    async def get_live_single_bid_rtm(self, delivery_date: int) -> Dict[str, Any]:
        """
        Fetch RTM Single Bid Book.
        API: /rtm/api/v2/singlebid/...
        """
        market = "rtm"
        config = await self._get_live_exchange_config(market)
        if not config:
            raise ValueError("Exchange Configuration not found for RTM")
            
        base_url = config.get("baseUrl") or config.get("BaseUrl")
        login_user_id = config.get("LoginUserId")
        participant_id = config.get("ParticipantId")
        portfolio_id = config.get("PortfolioId")
        bearer_token = config.get("BearerToken")
        
        # RTM Params
        # User curl: .../singlebid/PTCA1,N2DL0PTC0000,1770508800,ALL,S2PY0PTC0136,0,ALL,S2,ALL,2,0,2,0,0
        # Mapped to: ...{user},{participant},{date},{UserFilter},{portfolio},{session},{period},{bid_area},{status},{exception},{order},{sorting},{pg_num},{pg_size}
        
        user_filter = "ALL" # User used ALL instead of login_user_id
        session = 0
        period = "ALL"
        bid_area_id = "S2" # Default for this user
        status = "Executed"
        exception_id = 0 # User used 2, but 0 (All?) is safer default.
        order_id = 0
        sorting_type = 2
        page_number = 0
        
        url = (
            f"{base_url}/rtm/api/v2/singlebid/"
            f"{login_user_id},{participant_id},{delivery_date},"
            f"{user_filter},{portfolio_id},{session},{period},"
            f"{bid_area_id},{status},{exception_id},{order_id},{sorting_type},{page_number}"
        )
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer_token}"
        }
        
        return await self._execute_and_log(url, "GET", headers, None, market, "getSingleBidRtm")

    async def get_asset_details(self, market_type: str = "dam") -> Dict[str, Any]:
        """
        Fetch asset details from staging exchange API.
        """
        return await self._get_asset_details_common(market_type, is_live=False)

    async def get_live_asset_details(self, market_type: str = "dam") -> Dict[str, Any]:
        """
        Fetch asset details from production exchange API.
        """
        return await self._get_asset_details_common(market_type, is_live=True)

    async def _get_asset_details_common(self, market_type: str, is_live: bool) -> Dict[str, Any]:
        """
        Common logic to fetch and cache asset details.
        """
        if is_live:
            config = await self._get_live_exchange_config(market_type)
        else:
            config = await self._get_exchange_config(market_type)
            
        if not config:
            fallback_market = "rtm" if market_type == "dam" else "dam"
            if is_live:
                config = await self._get_live_exchange_config(fallback_market)
            else:
                config = await self._get_exchange_config(fallback_market)
            
            if not config:
                raise ValueError(f"Exchange Configuration not found for Asset Details (Live={is_live})")

        base_url = config.get("baseUrl") or config.get("BaseUrl")
        login_user_id = config.get("LoginUserId")
        participant_id = config.get("ParticipantId")
        bearer_token = config.get("BearerToken")
        
        url = f"{base_url}/{market_type}/api/v2/master/assets/{login_user_id},{participant_id}"
        headers = {"Content-Type": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        
        api_method = "getLiveAssetDetails" if is_live else "getAssetDetails"
        logger.info(f"Fetching asset details from: {url}")
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            try:
                data = response.json()
            except:
                data = response.text
                
            await self._log_to_db(url, "GET", headers, None, data, market_type, api_method)
                        
            if isinstance(data, list) and data:
                # Some APIs return list directly
                return data[0] if isinstance(data[0], dict) else data
            
            # Cache to asset_master collection
            if hasattr(self.asset_details_cache, 'collection'):
                try:
                    # Sync calls
                    await self.asset_details_cache.collection.delete_many({})
                    if isinstance(data, dict):
                        data_to_store = dict(data)
                        data_to_store["created_at"] = datetime.now()
                        data_to_store["is_live"] = is_live
                        await self.asset_details_cache.collection.insert_one(data_to_store)
                except Exception as e:
                     logger.error(f"Failed to cache asset details: {e}")

            response.raise_for_status()
            return data

    
    
# Singleton-like usage
scheduler_exchange_client = SchedulerExchangeAPIClient()
