"""
DAM Transaction Service
Orchestrates the DAM Single Bid Transaction flow:
1. Fetch exchange config
2. Call external APIs (assets, delivery dates)
3. Get bid data from DamBiddingServices
4. Transform bidblockarray → API format
5. Submit to external API
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, time
from bson import ObjectId
import httpx
from fastapi import HTTPException

from ..repository.exchange_config_repository import ExchangeConfigRepository
from ..repository.limit_values_repository import LimitValuesRepository
from ..iex.masterApi import ExchangeAPIClient, block_to_time_period, time_slot_to_block

logger = logging.getLogger(__name__)


from .new_dam_bidding_services import dam_bidding_services
from libs.core.jwt_validator import TokenPayload
from ..repository.dam_single_bid_data_for_approval_repository import DAMSingleBidDataForApprovalRepository
from ..repository.bid_trade_serve_repository import BidTradeServeRepository

class DAMTransactionService:
    """Service for DAM Single Bid Transaction API integration"""
    
    def __init__(self):
        self.exchange_config_repo = ExchangeConfigRepository()
        self.limit_values_repo = LimitValuesRepository()
        self.dam_single_bid_repo = DAMSingleBidDataForApprovalRepository()
        self.bid_trade_serve_repo = BidTradeServeRepository()
        self._api_client: Optional[ExchangeAPIClient] = None

    async def _get_api_client(self) -> ExchangeAPIClient:
        """Get or create API client with exchange config"""
        if self._api_client:
            return self._api_client
            
        config = await self.exchange_config_repo.get_active_config()
        if not config:
            raise ValueError("No active exchange configuration found")
        
        self._api_client = ExchangeAPIClient(
            base_url=config.get("baseUrl", ""),
            login_user_id=config.get("LoginUserId", ""),
            participant_id=config.get("ParticipantId", ""),
            portfolio_id=config.get("PortfolioId", ""),
            bearer_token=config.get("BearerToken", ""),
        )
        
        return self._api_client

    async def _get_all_ocf_values(self, delivery_date: int) -> Dict[int, Dict[str, Any]]:
        """
        Fetch OCF values for ALL 96 blocks in a single DB query (replaces 96 sequential queries).

        Args:
            delivery_date: Unix timestamp of delivery date

        Returns:
            Dict mapping block number (1-96) → {PremiumDiscountPrice, MaxOCFQty, OCFOpted}
        """
        dt = datetime.fromtimestamp(delivery_date, tz=timezone.utc)
        scheduled_at_str = dt.strftime("%Y-%m-%d")

        logger.info(f"Fetching all OCF values for date {scheduled_at_str} in bulk")

        # Single query: fetch all OCF rows for this date (both id types, all blocks)
        all_ocf_rows = await self.limit_values_repo.get_all_data_with_filters(
            "limit_values",
            filters={
                "id": {"$in": ["ocfPremiumDiscountPrice", "ocfMaxOCFQty"]},
                "category": "Limits",
                "market": "DAM",
                "scheduled_at": {"$regex": f"^{scheduled_at_str}"}
            }
        )

        # Build per-block maps
        premium_map: Dict[int, float] = {}
        max_qty_map: Dict[int, float] = {}

        for row in all_ocf_rows:
            block = row.get("block")
            val = row.get("values", 0) or 0
            if row.get("id") == "ocfPremiumDiscountPrice":
                premium_map[block] = val
            elif row.get("id") == "ocfMaxOCFQty":
                max_qty_map[block] = val

        # Build full map for blocks 1-96
        ocf_map: Dict[int, Dict[str, Any]] = {}
        for block in range(1, 97):
            premium = premium_map.get(block, 0)
            max_qty = max_qty_map.get(block, 0)
            ocf_map[block] = {
                "PremiumDiscountPrice": premium,
                "MaxOCFQty": max_qty,
                "OCFOpted": "Y" if (premium != 0 or max_qty != 0) else "N"
            }

        logger.info(f"OCF bulk fetch complete: {len(all_ocf_rows)} rows → 96 blocks mapped")
        return ocf_map

    def _validate_round_quantity(self, quantity: float, order_qty_decimal: int) -> int:
        """
        Validate and truncate quantity to integer.
        mIgnoring decimal part as per requirement (27.7 -> 27).
        """
        return int(quantity)

    def _transform_bid_block_to_api_format(
        self, 
        bid_block_array: Dict[str, Any],
        delivery_date: int,
        ocf_values_map: Dict[int, Dict[str, Any]],
        asset_details: Dict[str, Any],
        lowerDPR: int,
        upperDPR: int,
        biddedQtyDecimal: int
    ) -> List[Dict[str, Any]]:
        """
        Transform bidblockarray to API BidDetails format
        
        Args:
            bid_block_array: Original bid data from DamBiddingServices
            delivery_date: Unix timestamp
            ocf_values_map: Map of block -> OCF values
            asset_details: Asset details for validation
            
        Returns:
            List of BidDetails items for API request
        """
        bid_details = []
        order_qty_decimal = asset_details.get("OrderQtyDecimal", 100) # Default to 2 decimals if missing
        
        # Iterate through each session in bid_block_array
        for session_key, time_blocks in bid_block_array.items():
            if not isinstance(time_blocks, dict):
                continue
                
            # Iterate through each time block
            for time_slot, pq_data in time_blocks.items():
                if not isinstance(pq_data, dict):
                    continue
                
                # Parse time slot (e.g., "00:00-00:15")
                try:
                    parts = time_slot.split("-")
                    from_period = parts[0].strip()
                    to_period = parts[1].strip()
                    block = time_slot_to_block(time_slot)
                except (ValueError, IndexError) as e:
                    logger.warning(f"Invalid time slot format: {time_slot}, error: {e}")
                    continue
                
                # Build PQData array
                pq_array = []
                buy_sell = "S"  # Default to Sell
                
                for idx, item in pq_data.items():
                    if not isinstance(item, dict):
                        continue
                    
                    price = int(float(item.get("RTMPrice", 0)))
                    if "Volume" in item:
                       raw_quantity = item["Volume"] * biddedQtyDecimal
                    else:
                       raw_quantity = item.get("AC_Volume", 0) * biddedQtyDecimal
                    
                    if raw_quantity in [None, ""]:
                        raw_quantity = 0

                    # Validate Quantity
                    quantity = self._validate_round_quantity(float(raw_quantity), order_qty_decimal)
                    quantity = abs(quantity)
                    # Determine Buy/Sell from first item
                    if idx == "0" or idx == 0:
                        sell_buy_type = item.get("sell_buy_bid_type", "sell").lower()
                        buy_sell = "S" if sell_buy_type == "sell" else "B"
                        
                    # If price is 0 or quantity is 0 and not the first item, skip
                    
                    if buy_sell == "S":
                        quantity = -quantity
                    pq_array.append({
                        "Price": price,
                        "Quantity": quantity
                    })
                
                # If pq_array is empty, skip adding this block (prevents sending blocks with no valid bids or 0 volume)
                if not pq_array:
                    continue

                # Calculate max_qty from actual volume data
                max_qty = max(abs(pq["Quantity"]) for pq in pq_array) if pq_array else 0
                
                # Skip this time slot if there's no actual volume data
                # This prevents adding boundary prices without real data
                if max_qty == 0:
                    logger.warning(f"Skipping time slot {time_slot} - no actual volume data")
                    continue

                # Ensure boundary prices (0 and 10000) are always included
                # For Buy: Price 0 = max qty, Price 10000 = 0
                # For Sell: Price 0 = 0, Price 10000 = max qty (negative)
                existing_prices = {pq["Price"] for pq in pq_array}
                
                if 0 not in existing_prices:
                    if buy_sell == "B":
                        pq_array.insert(0, {"Price": 0, "Quantity": max_qty})
                    else:  # Sell
                        pq_array.insert(0, {"Price": 0, "Quantity": 0})
                
                if 10000 not in existing_prices:
                    if buy_sell == "B":
                        pq_array.append({"Price": 10000, "Quantity": 0})
                    else:  # Sell
                        pq_array.append({"Price": 10000, "Quantity": -max_qty})

                # Get OCF values for this block
                ocf = ocf_values_map.get(block, {
                    "PremiumDiscountPrice": 0,
                    "MaxOCFQty": 0,
                    "OCFOpted": "N"
                })
                
                bid_detail = {
                    "FromPeriodId": from_period,
                    "ToPeriodId": to_period,
                    "BuySell": buy_sell,
                    "OCFOpted": ocf["OCFOpted"],
                    "PremiumDiscountPrice": ocf["PremiumDiscountPrice"],
                    "MaxOCFQty": ocf["MaxOCFQty"],
                    "PQData": pq_array
                }
                
                bid_details.append(bid_detail)
        
        return bid_details

    async def submit_single_bid(
        self,
        current_user: TokenPayload,
        bid_data: Dict[str, Any],
        ocf_toggle: bool = False,
        bid_area_id: str = "",
        correlation_id: str = None,
        start_time: time = None
    ) -> Dict[str, Any]:
        """
        Submit single bid to external exchange API
        
        Args:
            current_user: Current authenticated user
            bid_data: Dictionary containing bid data for BidCreationpost (prices, volumes, etc)
            ocf_toggle: If True, fetch OCF values from limit_values
            bid_area_id: Bid area ID (if provided, overrides config)
            correlation_id: Request correlation ID
            
        Returns:
            Bid Details Response from exchange
        """
        if not correlation_id:
            import uuid
            correlation_id = str(uuid.uuid4())
            
        logger.info(f"Starting single bid submission for user {current_user.username}, ocfToggle={ocf_toggle}, correlation_id={correlation_id}")
        
        # 1. Fetch Bid Data
        # Pass the provided bid_data to BidCreationpost to generate bidblockarray
        _, bid_creation_result = await dam_bidding_services.BidCreationpost(bid_data) 
        bid_block_array = bid_creation_result.get("bidblockarray", {})
        
        if not bid_block_array:
            logger.warning("BidCreationpost returned empty or no bidblockarray.")

        # 2. Get API client (with correlation_id)
        # We need to manually construct client to pass correlation_id since _get_api_client doesn't accept it yet?
        # Or I update _get_api_client helper. I'll update the helper implicitly or just do it here.
        # Check _get_api_client implementation:
        # It fetches config then calls ExchangeAPIClient.
        # I'll modify _get_api_client to accept correlation_id or just inline logic here for safety/speed.
        # Better: inline logic to ensure I pass data correct.
        bidType = "single"
        market = "DAM"
        
        config = await self.exchange_config_repo.get_bidType_market_active_config(bidType, market)
        if not config:
            raise ValueError("No active exchange configuration found")
            
        api_client = ExchangeAPIClient(
            base_url=config.get("baseUrl", ""),
            login_user_id=config.get("LoginUserId", ""),
            participant_id=config.get("ParticipantId", ""),
            portfolio_id=config.get("PortfolioId", ""),
            bearer_token=config.get("BearerToken", ""),
            correlation_id=correlation_id,
            market_type="dam",
            bid_type="single-bid",
        )
        
        # 3. Fetch independent external data
        # Fetch asset details
        asset_response = await api_client.get_asset_details(bid_type="single-bid")
        asset_details_list = asset_response.get("AssetDetails", [])
        asset_details = asset_details_list[0] if asset_details_list else {}
        asset_id = asset_details.get("AssetId", "INDIA")
        lowerDPR = asset_details.get("LowerDPR", 0)
        upperDPR = asset_details.get("UpperDPR", 10000)
        biddedQtyDecimal = asset_details.get("OrderQtyDecimal", 10)
        
        # Fetch delivery date
        delivery_response = await api_client.get_delivery_dates(bid_type="single-bid")
        delivery_date = delivery_response.get("DeliveryDate", 0)
        
        delivery_dt = datetime.fromtimestamp(delivery_date, tz=timezone.utc)
        delivery_date_str = delivery_dt.strftime("%Y-%m-%d")
        bidding_date = bid_data.get("bidding_date", delivery_date_str)
        
        logger.info(f"Asset: {asset_id}, DeliveryDate: {delivery_date}, bidding_date: {bidding_date}")
        
        # 4. Check for existing bids and cancel if found
        effective_bid_area_id = bid_area_id or config.get("BidAreaId", "S2")
        logger.info(f"Checking for existing DAM bids with BidAreaId: {effective_bid_area_id}")
        
        try:
            existing_bids = await api_client.get_single_bid_dam(
                delivery_date=delivery_date,
                product_id="ALL",
                bid_area_id=effective_bid_area_id
            )
            
            total_record = existing_bids.get("TotalRecord", 0)
            single_bid_details = existing_bids.get("SingleBidDetails", [])
            
            if total_record > 0 or len(single_bid_details) > 0:
                logger.info(f"Found {total_record} existing bids, canceling before new submission")
                
                cancel_result = await api_client.cancel_all_dam_bid(
                    delivery_date=delivery_date,
                    asset_id=asset_id,
                    bid_area_id=effective_bid_area_id
                )
                
                if cancel_result.get("Status") != "Success":
                    logger.warning(f"Bid cancellation returned non-success status: {cancel_result}")
                    # Continue with submission anyway - user may want this behavior
                
                logger.info(f"Successfully cancelled existing bids: {cancel_result.get('TransactionId', 'N/A')}")
                # Update DB status to cancelled for old records
                try:
                    await self.dam_single_bid_repo.update_many(
                        {
                            "bid_date": bidding_date,
                            "bid_status": {"$in": ["submitted"]}
                        },
                        {"$set": {"bid_status": "withdrawal_bid", "exchangeStatus": "Cancelled"}}
                    )
                except Exception as db_err:
                    logger.warning(f"Failed to update DB status to cancelled during pre-submission clear: {db_err}")
            else:
                logger.info("No existing bids found, proceeding with submission")
                
        except httpx.HTTPStatusError as e:
            logger.warning(f"Error checking/canceling existing bids: {e.response.text}. Proceeding with submission.")
        except Exception as e:
            logger.warning(f"Error checking/canceling existing bids: {str(e)}. Proceeding with submission.")
        
        # 5. Prepare OCF values map
        if ocf_toggle:
            # Single bulk DB query replaces 96 sequential queries (was ~2-3s, now <100ms)
            logger.info("OCF toggle is ON, fetching all OCF values in bulk")
            ocf_values_map = await self._get_all_ocf_values(delivery_date)
        else:
            # Default OCF values when toggle is off — build directly, no DB needed
            default_ocf = {"PremiumDiscountPrice": 0, "MaxOCFQty": 0, "OCFOpted": "N"}
            ocf_values_map = {block: default_ocf for block in range(1, 97)}
        
        # 6. Transform bid data with Quantity validation
        bid_details = self._transform_bid_block_to_api_format(
            bid_block_array, 
            delivery_date,
            ocf_values_map,
            asset_details,
            lowerDPR,
            upperDPR,
            biddedQtyDecimal
        )
        
        # 7. Build final request
        request_body = {
            "DeliveryDate": delivery_date,
            "AssetId": asset_id,
            "BidAreaId": effective_bid_area_id,
            "UserId": config.get("LoginUserId", ""),
            "ParticipantId": config.get("ParticipantId", ""),
            "PortfolioId": config.get("PortfolioId", ""),
            "InitiatedBy": config.get("LoginUserId", ""),
            "BidDetails": bid_details
        }
        
        logger.info(f"Submitting bid with {len(bid_details)} time blocks")
        
        # 8. Submit to external API
        try:
            submit_response = await api_client.submit_single_bid(request_body)
        except httpx.TimeoutException as e:
            err_msg = f"{type(e).__name__}: Exchange API did not respond within the allowed time"
            logger.error(f"DAM bid submission timed out — {err_msg}")
            raise HTTPException(status_code=504, detail="Exchange API timed out during bid submission. Please retry.")
        except httpx.HTTPStatusError as e:
            logger.error(f"External API submission failed [{e.response.status_code}]: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Exchange API Error: {e.response.text}")
        except Exception as e:
            err_msg = f"{type(e).__name__}: {str(e)}" if str(e) else type(e).__name__
            logger.error(f"Submission error: {err_msg}")
            raise HTTPException(status_code=500, detail=f"Submission failed: {err_msg}")
        
        transaction_id = submit_response.get("TransactionId")
        if not transaction_id:
             logger.warning("No TransactionId in submit response, cannot fetch details.")
             return submit_response
             
        # Format dates for aggregation
        # bidding_date already extracted above
        
        # Use provided start_time or default to "00:00" if not provided
        # Note: start_time is now passed as string "HH:MM"
        start_time_str = start_time.strftime("%H:%M") if start_time else "00:00"

        logger.info(f"Aggregating bids for date: {bidding_date}, inserted_time < {start_time_str}")

        # Aggregation Pipeline
        pipeline = [
            {
                "$match": {
                    "bid_date": bidding_date,
                    "inserted_time": {"$lt": start_time_str},
                    "bid_status": "approved"
                }
            },
            {
                "$sort": {
                    "start_time": 1,
                    "end_time": 1,
                    "revision_no": -1
                }
            },
            {
                "$group": {
                    "_id": {
                        "start_time": "$start_time",
                        "end_time": "$end_time"
                    },
                    "doc": {
                        "$first": "$$ROOT"
                    }
                }
            },
            {
                "$replaceRoot": {
                    "newRoot": "$doc"
                }
            }
        ]

        # Execute Aggregation
        aggregated_docs = await self.dam_single_bid_repo.aggregate(pipeline)
        
        # Mark status to submitted
        for doc in aggregated_docs:
            await self.dam_single_bid_repo.update_one(
                {"_id": doc["_id"]},
                {"$set": {"bid_status": "submitted", "exchangeRevisionNo": doc["revision_no"]}},
            )
            data_rev = doc.get("rldc_revision_no")
            if data_rev is not None:
                try:
                    await dam_bidding_services.capture_serve_repo.update_exchange_submission_flag(
                        schedule_date=bidding_date,
                        dc_sg_type="SG",
                        rldc_revision_no=data_rev,
                        flag="Y",
                        bid_block_no=doc.get("block_no"),
                        bid_revision_no=doc.get("revision_no"),
                    )
                    await dam_bidding_services.manual_capture_serve_repo.update_exchange_submission_flag(
                        schedule_date=bidding_date,
                        dc_sg_type="SG",
                        rldc_revision_no=data_rev,
                        flag="Y",
                        bid_block_no=doc.get("block_no"),
                        bid_revision_no=doc.get("revision_no"),
                    )
                except Exception as cap_err:
                    logger.warning(f"Failed to update DAM capture exchange_submission_flag: {cap_err}")

        # 9. Get Bid Details
        bid_details_response = await api_client.get_bid_details(transaction_id, delivery_date, bid_type="single-bid")
        
        # Map API response to DB
        single_bid_details = bid_details_response.get("SingleBidDetails", [])
        for detail in single_bid_details:
            from_period = detail.get("FromPeriodId")
            to_period = detail.get("ToPeriodId")
            user_id = detail.get("UserId")
            user_id_key = f"EX-{user_id}"
            buy_sell = detail.get("BuySell")
            pq_data = detail.get("pq_data", []) or []
            bidded_qty = 0
            if pq_data:
                if buy_sell == "S":
                    target_entry = max(pq_data, key=lambda x: x.get("Price", 0))
                    bidded_qty = float(target_entry.get("Quantity", 0)) / biddedQtyDecimal
                elif buy_sell == "B":
                    target_entry = min(pq_data, key=lambda x: x.get("Price", float("inf")))
                    bidded_qty = float(target_entry.get("Quantity", 0)) / biddedQtyDecimal

            
            # Map API fields to DB fields
            update_data = {
                "exchangeStatus": detail.get("OrderStatus"),
                "exchangeOrderId": detail.get("OrderId"),
                "exchangeOrderTime": detail.get("OrderTime"),
                "exchangeError": detail.get("Error"),
                "exchangeLastUpdatedTime": detail.get("LastUpdatedTime"),
                "exchangePQData": detail.get("PQData"),
                "updatedAt": datetime.now(),
                "exchangeUserId": user_id, 
                "bidded": bidded_qty,   
                "orderId": detail.get("OrderId"),
            }
            
            # Update specific document based on start_time/end_time in the approved (now submitted) set
            # We filter by bid_date and time, and ensure it's one of the ones we just processed 
            # (or generic match for that slot on that day)
            await self.dam_single_bid_repo.update_one(
                {
                    "bid_date": bidding_date,
                    "start_time": from_period,
                    "end_time": to_period,
                    "bid_status": "submitted" 
                },
                {"$set": update_data}
            )
            
            # Update bidded in bid_trade_serve
            try:
                tb = BidTradeServeRepository.from_period_to_time_block(from_period)
                if tb > 0:
                    await self.bid_trade_serve_repo.update_bidded_by_block(
                        delivery_date_ts=delivery_date,
                        time_block=tb,
                        bid_type="DAM",
                        bidded_value=bidded_qty
                    )
            except Exception as bts_err:
                logger.warning(f"Failed to update bid_trade_serve for DAM block {from_period}: {bts_err}")

        # 10. Store in exchange_biddetail collection
        # Add correlation_id to the document for linking
        # (Though integration logs have it, good to have here too)
        bid_details_record = bid_details_response.copy()
        bid_details_record["correlation_id"] = correlation_id
        bid_details_record["market_type"] = "DAM"
        bid_details_record["created_at"] = datetime.now()
        
        from services.processing.app.v2.repository.exchange_bid_detail_repository import exchange_bid_detail_repository
        await exchange_bid_detail_repository.create_bid_detail(bid_details_record)
        
        # Return exact response from bid details API
        return bid_details_response

    async def cancel_bid(
        self,
        correlation_id: str = None
    ) -> Dict[str, Any]:
        """
        Cancel all existing DAM bids for today's delivery date.
        
        Flow:
        1. Fetch exchange config
        2. Get delivery date
        3. Get asset details
        4. Check for existing bids
        5. Cancel if exists
        
        Args:
            correlation_id: Request correlation ID
            
        Returns:
            Cancel operation result with status
        """
        if not correlation_id:
            import uuid
            correlation_id = str(uuid.uuid4())
            
        logger.info(f"Starting DAM bid cancellation, correlation_id={correlation_id}")
        
        # 1. Get config
        config = await self.exchange_config_repo.get_bidType_market_active_config("single", "DAM")
        if not config:
            raise ValueError("No active exchange configuration found")
        
        # 2. Create API client
        api_client = ExchangeAPIClient(
            base_url=config.get("baseUrl", ""),
            login_user_id=config.get("LoginUserId", ""),
            participant_id=config.get("ParticipantId", ""),
            portfolio_id=config.get("PortfolioId", ""),
            bearer_token=config.get("BearerToken", ""),
            correlation_id=correlation_id,
            market_type="dam",
            bid_type="single-bid",
        )
        
        # 3. Get delivery date
        delivery_response = await api_client.get_delivery_dates(bid_type="single-bid")
        delivery_date = delivery_response.get("DeliveryDate", 0)
        
        if not delivery_date:
            raise ValueError("Could not fetch delivery date from exchange")
        
        # 4. Get asset details
        asset_response = await api_client.get_asset_details(bid_type="single-bid")
        asset_details_list = asset_response.get("AssetDetails", [])
        asset_details = asset_details_list[0] if asset_details_list else {}
        asset_id = asset_details.get("AssetId", "INDIA")
        
        bid_area_id = config.get("BidAreaId", "S2")
        
        logger.info(f"Checking for existing bids: DeliveryDate={delivery_date}, AssetId={asset_id}, BidAreaId={bid_area_id}")
        
        # 5. Check for existing bids
        existing_bids = await api_client.get_single_bid_dam(
            delivery_date=delivery_date,
            product_id="ALL",
            bid_area_id=bid_area_id
        )
        
        total_record = existing_bids.get("TotalRecord", 0)
        single_bid_details = existing_bids.get("SingleBidDetails", [])
        
        if total_record == 0 and len(single_bid_details) == 0:
            logger.info("No existing bids found to cancel")
            return {
                "success": True,
                "message": "No existing bids found to cancel",
                "bids_cancelled": 0,
                "correlation_id": correlation_id
            }
        
        # 6. Cancel existing bids
        logger.info(f"Found {total_record} existing bids, proceeding with cancellation")
        
        cancel_result = await api_client.cancel_all_dam_bid(
            delivery_date=delivery_date,
            asset_id=asset_id,
            bid_area_id=bid_area_id
        )
        
        if cancel_result.get("Status") == "Success":
            delivery_dt = datetime.fromtimestamp(delivery_date, tz=timezone.utc)
            delivery_date_str = delivery_dt.strftime("%Y-%m-%d")

            # 6a. Immediate status update in DB to 'withdrawn' for all bids on this date
            try:
                cancel_transaction_id = cancel_result.get("TransactionId")
                update_set = {
                    "bid_status": "withdrawn",
                    "updatedAt": datetime.now()
                }
                if cancel_transaction_id:
                    update_set["cancel_transaction_id"] = cancel_transaction_id

                await self.dam_single_bid_repo.update_many(
                    {
                        "bid_date": delivery_date_str,
                        "bid_status": {"$in": ["submitted", "approved", "approved_withdrawal"]}
                    },
                    {"$set": update_set}
                )
                logger.info(f"Immediately updated statuses to 'withdrawn' for DAM date {delivery_date_str}")
            except Exception as b_db_err:
                logger.warning(f"Failed bulk status update for DAM cancellation: {b_db_err}")

            # 6b. Reset bidded to 0 in bid_trade_serve
            try:
                await self.bid_trade_serve_repo.reset_bidded_for_date(delivery_date, "DAM")
            except Exception as bts_err:
                logger.warning(f"Failed to reset bid_trade_serve for DAM cancellation: {bts_err}")

            # 6c. Enrichment: Fetch latest details from exchange
            cancel_transaction_id = cancel_result.get("TransactionId")
            if cancel_transaction_id:
                try:
                    logger.info(f"Fetching bid details for DAM cancellation TransactionId={cancel_transaction_id}")
                    bid_details_response = await api_client.get_cancel_bid_details(cancel_transaction_id, delivery_date, bid_type="single-bid")
                    
                    if isinstance(bid_details_response, dict):
                        # Store response for audit
                        bid_details_record = bid_details_response.copy()
                        bid_details_record["correlation_id"] = correlation_id
                        bid_details_record["market_type"] = "DAM"
                        bid_details_record["bid_type"] = "withdrawal"
                        bid_details_record["created_at"] = datetime.now()
                        await self.exchange_bid_detail_repo.create_bid_detail(bid_details_record)
                        
                        # Enrich records with exchange details
                        exchange_details = bid_details_response.get("SingleBidDetails", [])
                        for detail in exchange_details:
                            update_data = {
                                "exchangeStatus": detail.get("OrderStatus"),
                                "exchangeOrderId": detail.get("OrderId"),
                                "exchangeOrderTime": detail.get("OrderTime"),
                                "exchangeError": detail.get("Error"),
                                "exchangeLastUpdatedTime": detail.get("LastUpdatedTime"),
                                "exchangePQData": detail.get("PQData"),
                                "updatedAt": datetime.now(),
                                "exchangeUserId": detail.get("UserId"),
                                "orderId": detail.get("OrderId"),
                                "cancel_transaction_id": cancel_transaction_id
                            }
                            await self.dam_single_bid_repo.update_one(
                                {
                                    "bid_date": delivery_date_str,
                                    "start_time": detail.get("FromPeriodId"),
                                    "end_time": detail.get("ToPeriodId")
                                },
                                {"$set": update_data}
                            )
                        logger.info(f"Enriched DAM bid records with exchange details for date {delivery_date_str}")
                    else:
                        logger.warning(f"Unexpected response type from DAM get_bid_details: {type(bid_details_response)}")
                except Exception as e:
                    logger.warning(f"Failed to enrich DAM bid details after cancellation: {e}")
            else:
                # Fallback to general update if no transaction id
                try:
                    await self.update_single_bid_dam_in_system(delivery_date, asset_id, bid_area_id)
                except Exception as sys_upd_err:
                    logger.warning(f"System update fallback failed for DAM: {sys_upd_err}")

            # Reset bidded to 0 for all blocks on this delivery date in bid_trade_serve
            try:
                await self.bid_trade_serve_repo.reset_bidded_for_date(
                    delivery_date_ts=delivery_date,
                    bid_type="DAM"
                )
            except Exception as bts_err:
                logger.warning(f"Failed to reset bid_trade_serve bidded for DAM date {delivery_date_str}: {bts_err}")

            return {
                "success": True,
                "message": "Successfully cancelled all existing bids",
                "bids_cancelled": total_record,
                "transaction_id": cancel_result.get("TransactionId"),
                "correlation_id": correlation_id
            }
        else:
            return {
                "success": False,
                "message": cancel_result.get("StatusMessage", "Cancellation failed"),
                "bids_cancelled": 0,
                "correlation_id": correlation_id,
                "exchange_response": cancel_result
            }

    async def update_single_bid_dam_in_system(self, delivery_date: int, asset_id: str, bid_area_id: str, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        if not correlation_id:
            import uuid
            correlation_id = str(uuid.uuid4())
        """
        Fetch DAM Single Bid Book.
        API: /dam/api/v2/singlebid/...
        """
        config = await self.exchange_config_repo.get_bidType_market_active_config("single", "DAM")
        if not config:
            raise ValueError("No active exchange configuration found")
            
        api_client = ExchangeAPIClient(
            base_url=config.get("baseUrl", ""),
            login_user_id=config.get("LoginUserId", ""),
            participant_id=config.get("ParticipantId", ""),
            portfolio_id=config.get("PortfolioId", ""),
            bearer_token=config.get("BearerToken", ""),
            correlation_id=correlation_id,
            market_type="dam",
            bid_type="single-bid",
        )
        get_dam_bid_response = await api_client.get_single_bid_dam(
            delivery_date=delivery_date
        )

        single_bid_details = get_dam_bid_response.get("SingleBidDetails", [])
        for bid in single_bid_details:
            order_id = bid.get("OrderId")
            order_status = bid.get("OrderStatus")
            if order_status == "Cancelled":
                dam_single_bid_data = await self.dam_single_bid_repo.find_one_async({"exchangeOrderId": order_id})
                if dam_single_bid_data:
                    current_revision = dam_single_bid_data.get("revision_no", 0)
                    current_exchange_revision = dam_single_bid_data.get("exchangeRevisionNo", 0)
                    await self.dam_single_bid_repo.update_one_async(
                        {"_id": dam_single_bid_data["_id"]},
                        {"$set": {
                            "bid_status": "withdrawal_bid",
                            "revision_no": current_revision + 1,
                            "exchangeRevisionNo": current_exchange_revision + 1,
                            "exchangeStatus": bid.get("OrderStatus"),
                            "exchangeError": "Bid is cancelled by user"
                        }}
                    )
        return "Data Updated when cancelling bid"

    async def get_trade_book(
        self,
        delivery_date: Optional[int] = None,
        from_period: str = "ALL",
        to_period: str = "ALL",
        buy_sell: str = "0",
        bid_area_id: str = "ALL",
        trade_id: int = 0,
        order_id: int = 0,
        page_number: int = 0,
        page_size: int = 0,
        correlation_id: str = None
    ) -> Dict[str, Any]:
        """
        Fetch DAM trade book data from exchange API.
        
        Flow:
        1. Fetch exchange config
        2. Get delivery date (if not provided)
        3. Call trade book API with filters
        
        Args:
            delivery_date: DAM Delivery Date timestamp (if None, fetches current delivery date)
            from_period: From Period Id, 'ALL' for all periods
            to_period: To Period Id, 'ALL' for all periods
            buy_sell: '0': Both, '1': Buy, '2': Sell
            bid_area_id: Bid Area Id, 'ALL' for all
            trade_id: Trade Id filter (0 for all)
            order_id: Order Id filter (0 for all)
            page_number: Page number (0 for all)
            page_size: Page size (0 for all, max 96)
            correlation_id: Request correlation ID
            
        Returns:
            {
                "success": bool,
                "data": {
                    "TradeDetails": [...],
                    "TotalRecord": int
                },
                "correlation_id": str
            }
        """
        if not correlation_id:
            import uuid
            correlation_id = str(uuid.uuid4())
            
        logger.info(f"Fetching DAM trade book, correlation_id={correlation_id}")
        
        # 1. Get config
        config = await self.exchange_config_repo.get_bidType_market_active_config("single", "DAM")
        if not config:
            raise ValueError("No active exchange configuration found")
        
        # 2. Create API client
        api_client = ExchangeAPIClient(
            base_url=config.get("baseUrl", ""),
            login_user_id=config.get("LoginUserId", ""),
            participant_id=config.get("ParticipantId", ""),
            portfolio_id=config.get("PortfolioId", ""),
            bearer_token=config.get("BearerToken", ""),
            correlation_id=correlation_id,
            market_type="dam",
            bid_type="trade-book",
        )
        
        # 3. Get delivery date if not provided
        if not delivery_date:
            delivery_response = await api_client.get_delivery_dates(bid_type="single-bid")
            delivery_date = delivery_response.get("DeliveryDate", 0)
            
            if not delivery_date:
                raise ValueError("Could not fetch delivery date from exchange")
        
        logger.info(f"Fetching trade book for DeliveryDate={delivery_date}, BidAreaId={bid_area_id}")
        
        # 4. Fetch trade book
        trade_book_response = await api_client.get_dam_trade_book(
            delivery_date=delivery_date,
            from_period=from_period,
            to_period=to_period,
            buy_sell=buy_sell,
            bid_area_id=bid_area_id,
            trade_id=trade_id,
            order_id=order_id,
            page_number=page_number,
            page_size=page_size
        )
        
        total_records = trade_book_response.get("TotalRecord", 0)
        trade_details = trade_book_response.get("TradeDetails", [])
        
        logger.info(f"Trade book fetched successfully: {total_records} records")
        
        return {
            "success": True,
            "data": trade_book_response,
            "total_records": total_records,
            "trade_count": len(trade_details),
            "delivery_date": delivery_date,
            "correlation_id": correlation_id
        }

    async def get_price_quantity(
        self,
        delivery_date: Optional[int] = None,
        correlation_id: str = None
    ) -> Dict[str, Any]:
        """
        Fetch DAM Price Quantity (MCP) data from exchange API.
        
        Flow:
        1. Fetch exchange config
        2. Get delivery date (if not provided)
        3. Call price quantity API
        
        Args:
            delivery_date: DAM Delivery Date timestamp (if None, fetches current delivery date)
            correlation_id: Request correlation ID
            
        Returns:
            {
                "success": bool,
                "data": {
                    "DeliveryDate": int,
                    "LastUpdatedTime": int,
                    "PQDetails": [...]
                },
                "correlation_id": str
            }
        """
        if not correlation_id:
            import uuid
            correlation_id = str(uuid.uuid4())
            
        logger.info(f"Fetching DAM price quantity, correlation_id={correlation_id}")
        
        # 1. Get config
        config = await self.exchange_config_repo.get_bidType_market_active_config("single", "DAM")
        if not config:
            raise ValueError("No active exchange configuration found")
        
        # 2. Create API client
        api_client = ExchangeAPIClient(
            base_url=config.get("baseUrl", ""),
            login_user_id=config.get("LoginUserId", ""),
            participant_id=config.get("ParticipantId", ""),
            portfolio_id=config.get("PortfolioId", ""),
            bearer_token=config.get("BearerToken", ""),
            correlation_id=correlation_id,
            market_type="dam",
            bid_type="price-quantity"
        )
        
        # 3. Get delivery date if not provided
        if not delivery_date:
            delivery_response = await api_client.get_delivery_dates(bid_type="single-bid")
            delivery_date = delivery_response.get("DeliveryDate", 0)
        
        logger.info(f"Fetching DAM price quantity for DeliveryDate={delivery_date}")
        
        # 4. Fetch price quantity data
        pq_response = await api_client.get_dam_price_quantity(
            delivery_date=delivery_date
        )
        
        pq_details = pq_response.get("PQDetails", [])
        last_updated = pq_response.get("LastUpdatedTime", 0)
        
        logger.info(f"DAM price quantity fetched successfully: {len(pq_details)} period details")
        
        return {
            "success": True,
            "data": pq_response,
            "period_count": len(pq_details),
            "last_updated_time": last_updated,
            "delivery_date": delivery_date,
            "correlation_id": correlation_id
        }


# Singleton instance
dam_transaction_service = DAMTransactionService()
