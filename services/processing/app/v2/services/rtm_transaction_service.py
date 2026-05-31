import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, time, timedelta

import json
import httpx
from fastapi import HTTPException

from services.processing.app.v2.iex.masterApi import ExchangeAPIClient
from services.processing.app.v2.repository.exchange_config_repository import exchange_config_repository
from services.processing.app.v2.repository.exchange_bid_detail_repository import exchange_bid_detail_repository
from services.processing.app.v2.repository.rtm_single_bid_data_for_approval_repository import RTMSingleBidDataForApprovalRepository
from services.processing.app.v2.repository.bid_trade_serve_repository import BidTradeServeRepository
from services.processing.app.v2.services.new_rtm_bidding_services import RTMBiddingServices
from libs.core.jwt_validator import TokenPayload

logger = logging.getLogger(__name__)

class RTMTransactionService:
    def __init__(self):
        self.exchange_config_repo = exchange_config_repository
        self.exchange_bid_detail_repo = exchange_bid_detail_repository
        self.rtm_single_bid_repo = RTMSingleBidDataForApprovalRepository()
        self.rtm_bidding_service = RTMBiddingServices()
        self.bid_trade_serve_repo = BidTradeServeRepository()
        self._api_client: Optional[ExchangeAPIClient] = None

    
    async def submit_sequential_bid(
        self,
        current_user: TokenPayload,
        bid_data: Dict[str, Any],
        correlation_id: str = None,
        start_time: time = None
    ) -> Dict[str, Any]:
        """
        Submit RTM Single Bid - Sequential (one API call per block).
        
        Same setup as submit_single_bid, but submits each time-block
        as a separate API call. This avoids 429 rate-limit issues.
        
        Flow:
        1. Fetch Exchange Config & Init API Client
        2. Fetch Delivery Dates & Select T/T+1
        3. BidCreationpost to generate bid block array
        4. Get Asset Details
        5. Construct full API payload
        6. Flatten into per-block payloads
        7. For each block:
           a. Check & cancel existing bids for that period
           b. Submit single-block payload
           c. Get bid details & update DB
        8. Return aggregated response
        """
        # 1. Get Exchange Config
        config = await self.exchange_config_repo.get_bidType_market_active_config("single", "RTM")
        if not config:
            raise ValueError("No active exchange configuration found")

        # 2. Init API Client
        api_client = ExchangeAPIClient(
            base_url=config.get("baseUrl", ""),
            login_user_id=config.get("LoginUserId", ""),
            participant_id=config.get("ParticipantId", ""),
            portfolio_id=config.get("PortfolioId", ""),
            bearer_token=config.get("BearerToken", ""),
            correlation_id=correlation_id,
            market_type="RTM",
            bid_type="single-bid"
        )

        # 3. Fetch Delivery Dates & select T/T+1
        delivery_dates_resp = await api_client.get_delivery_dates(bid_type="single-bid")
        delivery_date_list = delivery_dates_resp.get("DeliveryDates", [])
        
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        
        bidding_date_str = bid_data.get("bidingDate", "") or bid_data.get("schedule_date", "")
        try:
            if "-" in bidding_date_str and len(bidding_date_str.split("-")[0]) == 2:
                bidding_date = datetime.strptime(bidding_date_str, "%d-%m-%Y").date()
            else:
                bidding_date = datetime.strptime(bidding_date_str, "%Y-%m-%d").date()
        except (ValueError, AttributeError):
            logger.warning(f"Could not parse bidingDate '{bidding_date_str}', defaulting to T")
            bidding_date = today
        
        delivery_date_id = "T+1" if bidding_date == tomorrow else "T"
        logger.info(f"[Sequential] Bidding date: {bidding_date}, selected delivery date ID: {delivery_date_id}")
        
        selected_date_record = next((d for d in delivery_date_list if d.get("DeliveryDateId") == delivery_date_id), None)
        if not selected_date_record:
            raise ValueError(f"Delivery Date '{delivery_date_id}' not found in exchange response")
            
        delivery_date_timestamp = selected_date_record.get("DeliveryDate")
        logger.info(f"[Sequential] Selected delivery date: {delivery_date_id} (timestamp: {delivery_date_timestamp})")
        
        # 4. BidCreationpost
        bid_creation_result = await self.rtm_bidding_service.BidCreationpost(
            request_dict=bid_data,
            username=current_user.username or "system"
        )
        
        if not bid_creation_result or not isinstance(bid_creation_result, tuple):
            logger.error(f"BidCreationpost returned invalid result: {bid_creation_result}")
            raise ValueError("Invalid response from BidCreationpost")
             
        status_val, internal_payload = bid_creation_result
        if status_val != "success":
            logger.error(f"BidCreationpost failed status: {status_val}, payload: {internal_payload}")
            raise ValueError(f"BidCreationpost failed: {status_val}")
        bid_block_array = internal_payload.get("bidblockarray", {})
        
        # 5. Get Asset Details
        asset_response = await api_client.get_asset_details(bid_type="single-bid")
        logger.info(f"[Sequential] Asset Response: {asset_response}")
        asset_details_list = asset_response.get("AssetDetails", [])
        asset_details = asset_details_list[0] if asset_details_list else {}
        asset_id = asset_details.get("AssetId", "INDIA")
        lowerDPR = asset_details.get("LowerDPR", 0)
        upperDPR = asset_details.get("UpperDPR", 10000)
        biddedQtyDecimal = asset_details.get("OrderQtyDecimal", 10)
        logger.info(f"[Sequential] OrderQtyDecimal: {biddedQtyDecimal}")
        
        # 6. Construct full API payload (same as submit_single_bid)
        full_api_payload = self._construct_api_payload(
            config=config,
            delivery_date=delivery_date_timestamp,
            bid_block_array=bid_block_array,
            current_user_id=current_user.username,
            asset_id=asset_id,
            lowerDPR=lowerDPR,
            upperDPR=upperDPR,
            biddedQtyDecimal=biddedQtyDecimal
        )
        
        bid_area_id = config.get("BidAreaId", "N1")
        
        # 7. Flatten into per-block payloads (one payload per SessionDetail)
        per_block_payloads = []
        base_payload = {k: v for k, v in full_api_payload.items() if k != "BidDetails"}
        
        for session_detail in full_api_payload.get("BidDetails", []):
            session_id = session_detail.get("Session")
            for sd in session_detail.get("SessionDetails", []):
                block_payload = {
                    **base_payload,
                    "BidDetails": [{
                        "Session": session_id,
                        "SessionDetails": [sd]
                    }]
                }
                per_block_payloads.append(block_payload)
        
        logger.info(f"[Sequential] Created {len(per_block_payloads)} per-block payloads from {len(full_api_payload.get('BidDetails', []))} sessions")
        
        if not per_block_payloads:
            logger.warning("[Sequential] No blocks to submit")
            return {
                "success": False,
                "message": "No blocks to submit after payload construction"
            }
        
        # 8. Loop: cancel + submit + get details for each block
        delivery_dt = datetime.fromtimestamp(delivery_date_timestamp, tz=timezone.utc)
        delivery_date_str = delivery_dt.strftime("%Y-%m-%d")
        now = datetime.now()
        start_time_str = start_time.strftime("%H:%M") if start_time else now.strftime("%H:%M")
        
        all_block_results = []
        all_cancelled = []
        all_failed_cancellations = []
        all_submission_errors = []
        cancelled_order_ids = set()
        
        for block_idx, block_payload in enumerate(per_block_payloads):
            block_session = block_payload["BidDetails"][0]
            session_id = block_session["Session"]
            sd = block_session["SessionDetails"][0]
            from_period = sd.get("FromPeriodId")
            to_period = sd.get("ToPeriodId")
            
            logger.info(f"[Sequential] Processing block {block_idx + 1}/{len(per_block_payloads)}: Session={session_id}, Period={from_period}-{to_period}")
            
            # 8a. Check for existing bid for THIS period specifically (Strict Sequentiality)
            try:
                logger.info(f"[Sequential] Checking existing RTM bid for block {from_period}-{to_period}")
                existing_bids = await api_client.get_single_bid_rtm(
                    delivery_date=delivery_date_timestamp,
                    bid_area_id=bid_area_id
                )
                rtm_bid_details = existing_bids.get("SingleBidDetails", [])
                
                # Find matching order for this specific period
                matching_order = next((
                    bid for bid in rtm_bid_details 
                    if bid.get("Session") == session_id and 
                       bid.get("FromPeriodId") == from_period and 
                       bid.get("ToPeriodId") == to_period
                ), None)

                if matching_order and matching_order.get("OrderStatus") != "Cancelled":
                    order_id = matching_order.get("OrderId")
                    last_updated_time = matching_order.get("LastUpdatedTime")
                    
                    if order_id not in cancelled_order_ids:
                        try:
                            cancel_result = await api_client.cancel_rtm_bid(
                                order_id=order_id,
                                last_updated_time=last_updated_time,
                                delivery_date=delivery_date_timestamp
                            )
                            status_msg = cancel_result.get("StatusMessage", "")
                            
                            if cancel_result.get("Status") == "Success" or "Doesn't Exist" in status_msg:
                                cancelled_order_ids.add(order_id)
                                all_cancelled.append({
                                    "Session": session_id,
                                    "FromPeriodId": from_period,
                                    "ToPeriodId": to_period,
                                    "Status": "Success",
                                    "OrderId": order_id
                                })
                                logger.info(f"[Sequential] Cancelled existing bid: OrderId={order_id}")
                                
                                # Update DB status to reflect cancellation
                                try:
                                    await self.rtm_single_bid_repo.update_one(
                                        {
                                            "bid_date": delivery_date_str,
                                            "start_time": from_period,
                                            "end_time": to_period,
                                            "bid_status": {"$in": ["submitted", "approved"]}
                                        },
                                        {"$set": {"exchangeOrderId": order_id, "bid_status": "cancelled_for_resubmission"}}
                                    )
                                except Exception as db_err:
                                    logger.warning(f"[Sequential] Failed to update DB for cancelled bid: {db_err}")
                            else:
                                logger.warning(f"[Sequential] Failed to cancel bid OrderId={order_id}: {status_msg}")
                                # We continue to submission even if cancellation fails (exchange might have processed it already)
                        except Exception as cancel_error:
                            logger.error(f"[Sequential] Error cancelling bid for period {from_period}-{to_period}: {cancel_error}")
            except Exception as e:
                logger.warning(f"[Sequential] Error checking existing bids for block {from_period}: {e}")

            # 8b. Submit this single block
            try:
                submit_response = await api_client.submit_single_bid(block_payload)
                logger.info(f"[Sequential] Block {block_idx + 1} submission response: {json.dumps(submit_response, default=str)}")
            except Exception as e:
                logger.exception(f"[Sequential] Block {block_idx + 1} submission error: {str(e)}")
                all_submission_errors.append({
                    "block_index": block_idx + 1,
                    "Session": session_id,
                    "FromPeriodId": from_period,
                    "ToPeriodId": to_period,
                    "error": str(e)
                })
                continue
            
            transaction_id = submit_response.get("TransactionId")
            if not transaction_id:
                all_submission_errors.append({
                    "block_index": block_idx + 1,
                    "Session": session_id,
                    "FromPeriodId": from_period,
                    "ToPeriodId": to_period,
                    "error": "No TransactionId returned",
                    "response": submit_response
                })
                continue
            
            # 8c. Aggregation & status update for this block
            try:
                pipeline = [
                    {
                        "$match": {
                            "bid_date": delivery_date_str,
                            "inserted_time": {"$lt": start_time_str},
                            "bid_status": {"$in": ["approved", "cancelled_for_resubmission"]},
                            "start_time": from_period
                        }
                    },
                    {
                        "$sort": {"revision_no": -1}
                    },
                    {
                        "$limit": 1
                    }
                ]
                
                aggregated_docs = await self.rtm_single_bid_repo.aggregate(pipeline)
                for doc in aggregated_docs:
                    await self.rtm_single_bid_repo.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"bid_status": "submitted", "exchangeRevisionNo": doc["revision_no"]}}
                    )
                    data_rev = doc.get("rldc_revision_no")
                    if data_rev is not None:
                        try:
                            await self.rtm_bidding_service.capture_serve_repo.update_exchange_submission_flag(
                                schedule_date=delivery_date_str,
                                dc_sg_type="SG",
                                rldc_revision_no=data_rev,
                                flag="Y",
                                bid_block_no=doc.get("block_no"),
                                bid_revision_no=doc.get("revision_no"),
                            )
                        except Exception as cap_err:
                            logger.warning(f"[Sequential] Failed to update capture_serve exchange_submission_flag: {cap_err}")
            except Exception as e:
                logger.exception(f"[Sequential] Status update error for block {block_idx + 1}: {str(e)}")
            
            # 8d. Get bid details for this transaction
            try:
                bid_details_response = await api_client.get_bid_details(transaction_id, delivery_date_timestamp, bid_type="single-bid")

                if isinstance(bid_details_response, dict):
                    # Map response to DB
                    single_bid_details = bid_details_response.get("SingleBidDetails", [])
                    for detail in single_bid_details:
                        detail_from_period = detail.get("FromPeriodId")
                        detail_to_period = detail.get("ToPeriodId")
                        
                        pq_data = detail.get("PQData", []) or []
                        bidded_qty = 0
                        divisor = biddedQtyDecimal if biddedQtyDecimal and biddedQtyDecimal > 0 else 1
                        
                        if pq_data:
                            try:
                                buy_sell = detail.get("BuySell")
                                if buy_sell == "S":
                                    target_entry = max(pq_data, key=lambda x: x.get("Price", 0))
                                    bidded_qty = float(target_entry.get("Quantity", 0)) / divisor
                                else:
                                    target_entry = min(pq_data, key=lambda x: x.get("Price", float("inf")))
                                    bidded_qty = float(target_entry.get("Quantity", 0)) / divisor
                            except Exception:
                                bidded_qty = 0
                        
                        update_data = {
                            "exchangeStatus": detail.get("OrderStatus"),
                            "exchangeOrderId": detail.get("OrderId"),
                            "exchangeOrderTime": detail.get("OrderTime"),
                            "exchangeError": detail.get("Error"),
                            "exchangeLastUpdatedTime": detail.get("LastUpdatedTime"),
                            "exchangePQData": detail.get("PQData"),
                            "updatedAt": datetime.now(),
                            "exchangeUserId": detail.get("UserId"),
                            "bidded": bidded_qty,
                            "orderId": detail.get("OrderId"),
                        }
                        
                        await self.rtm_single_bid_repo.update_one(
                            {
                                "bid_date": delivery_date_str,
                                "start_time": detail_from_period,
                                "end_time": detail_to_period,
                                "bid_status": "submitted"
                            },
                            {"$set": update_data}
                        )
                        
                        # Update bidded in bid_trade_serve
                        try:
                            tb = BidTradeServeRepository.from_period_to_time_block(detail_from_period)
                            if tb > 0:
                                await self.bid_trade_serve_repo.update_bidded_by_block(
                                    delivery_date_ts=delivery_date_timestamp,
                                    time_block=tb,
                                    bid_type="RTM",
                                    bidded_value=bidded_qty
                                )
                        except Exception as bts_err:
                            logger.warning(f"[Sequential] Failed to update bid_trade_serve for block {detail_from_period}: {bts_err}")
                    
                    # Store in bid detail repository
                    bid_details_record = bid_details_response.copy()
                    bid_details_record["correlation_id"] = correlation_id
                    bid_details_record["market_type"] = "RTM"
                    bid_details_record["created_at"] = datetime.now()
                    await self.exchange_bid_detail_repo.create_bid_detail(bid_details_record)
                
                all_block_results.append({
                    "block_index": block_idx + 1,
                    "Session": session_id,
                    "FromPeriodId": from_period,
                    "ToPeriodId": to_period,
                    "TransactionId": transaction_id,
                    "status": "success"
                })
                
            except Exception as e:
                logger.error(f"[Sequential] Error getting details for block {block_idx + 1}: {str(e)}")
                all_block_results.append({
                    "block_index": block_idx + 1,
                    "Session": session_id,
                    "FromPeriodId": from_period,
                    "ToPeriodId": to_period,
                    "TransactionId": transaction_id,
                    "status": "submitted_details_pending"
                })
        
        # 10. Build aggregated response
        successful_blocks = [r for r in all_block_results if r.get("status") == "success"]
        
        final_response = {
            "status": "success" if successful_blocks else "failed",
            "message": f"Sequential submission: {len(successful_blocks)}/{len(per_block_payloads)} blocks submitted successfully",
            "total_blocks": len(per_block_payloads),
            "successful_blocks": len(successful_blocks),
            "failed_blocks": len(all_submission_errors),
            "block_results": all_block_results,
            "cancelled_sessions": all_cancelled if all_cancelled else None,
            "failed_cancellations": all_failed_cancellations if all_failed_cancellations else None,
            "submission_errors": all_submission_errors if all_submission_errors else None
        }
        
        logger.info(f"[Sequential] Final result: {successful_blocks.__len__()}/{len(per_block_payloads)} blocks successful")
        return final_response
    
    async def submit_single_bid(
        self,
        current_user: TokenPayload,
        bid_data: Dict[str, Any],
        correlation_id: str = None,
        start_time: time = None
    ) -> Dict[str, Any]:
        """
        Submit RTM Single Bid
        1. Fetch Exchange Config
        2. Init API Client
        3. Fetch Asset Details (RTM)
        4. Fetch Delivery Dates (RTM) & Select "T"
        5. Call BidCreationpost for generation
        6. Submit to RTM API
        7. Get Bid Details & Store
        """
        # 1. Get Exchange Config
        config = await self.exchange_config_repo.get_bidType_market_active_config("single", "RTM")
        if not config:
            raise ValueError("No active exchange configuration found")

        # 2. Init API Client
        api_client = ExchangeAPIClient(
            base_url=config.get("baseUrl", ""),
            login_user_id=config.get("LoginUserId", ""),
            participant_id=config.get("ParticipantId", ""),
            portfolio_id=config.get("PortfolioId", ""),
            bearer_token=config.get("BearerToken", ""),
            correlation_id=correlation_id,
            market_type="RTM",
            bid_type="single-bid"
        )
        
        # 3. Fetch Delivery Dates & Assets
        # We need these primarily to validate or get IDs usually, but specifically we need DeliveryDate
        # for the API submission payload if not already in bid_data.
        # RTM Flow: Select "T" delivery date.
        delivery_dates_resp = await api_client.get_delivery_dates(bid_type="single-bid")
        delivery_date_list = delivery_dates_resp.get("DeliveryDates", [])
        
        # Determine which delivery date to use based on bidingDate from request
        # If bidingDate is tomorrow, use "T+1"; otherwise use "T"
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        
        bidding_date_str = bid_data.get("bidingDate", "") or bid_data.get("schedule_date", "")
        try:
            # Try DD-MM-YYYY format first (bidingDate), then YYYY-MM-DD (schedule_date)
            if "-" in bidding_date_str and len(bidding_date_str.split("-")[0]) == 2:
                bidding_date = datetime.strptime(bidding_date_str, "%d-%m-%Y").date()
            else:
                bidding_date = datetime.strptime(bidding_date_str, "%Y-%m-%d").date()
        except (ValueError, AttributeError):
            logger.warning(f"Could not parse bidingDate '{bidding_date_str}', defaulting to T")
            bidding_date = today
        
        delivery_date_id = "T+1" if bidding_date == tomorrow else "T"
        logger.info(f"Bidding date: {bidding_date}, today: {today}, selected delivery date ID: {delivery_date_id}")
        
        selected_date_record = next((d for d in delivery_date_list if d.get("DeliveryDateId") == delivery_date_id), None)
        if not selected_date_record:
            raise ValueError(f"Delivery Date '{delivery_date_id}' not found in exchange response")
            
        delivery_date_timestamp = selected_date_record.get("DeliveryDate")  # Timestamp
        logger.info(f"Selected delivery date: {delivery_date_id} (timestamp: {delivery_date_timestamp})")
        
        # 4. Generate Bid Data Payload (BidCreationpost)
        # Calling internal service to generate the bid block array
        # Note: bid_data passed here must match request_dict expected by BidCreationpost
        bid_creation_result = await self.rtm_bidding_service.BidCreationpost(
            request_dict=bid_data,
            username=current_user.username or "system"
        )
        
        # Parse result
        # Expected format: {"data": ["success", { "bidblockarray": {...}, ... } ]}
        # Parse result - BidCreationpost returns a tuple: ("success", session_dict)
        if not bid_creation_result or not isinstance(bid_creation_result, tuple):
             logger.error(f"BidCreationpost returned invalid result: {bid_creation_result}")
             raise ValueError("Invalid response from BidCreationpost")
             
        status, internal_payload = bid_creation_result
        if status != "success":
             logger.error(f"BidCreationpost failed status: {status}, payload: {internal_payload}")
             raise ValueError(f"BidCreationpost failed: {status}")
        bid_block_array = internal_payload.get("bidblockarray", {})
        asset_response = await api_client.get_asset_details(bid_type="single-bid")
        logger.info(f"Asset Response: {asset_response}")
        asset_details_list = asset_response.get("AssetDetails", [])
        asset_details = asset_details_list[0] if asset_details_list else {}
        asset_id = asset_details.get("AssetId", "INDIA")
        lowerDPR = asset_details.get("LowerDPR", 0)
        upperDPR = asset_details.get("UpperDPR", 10000)
        biddedQtyDecimal = asset_details.get("OrderQtyDecimal", 10)
        logger.info(f"OrderQtyDecimal: {biddedQtyDecimal}")
        # 5. Transform to RTM API Format
        api_payload = self._construct_api_payload(
            config=config,
            delivery_date=delivery_date_timestamp,
            bid_block_array=bid_block_array,
            current_user_id=current_user.username,  # Or specific ID from config
            asset_id=asset_id,
            lowerDPR=lowerDPR,
            upperDPR=upperDPR,
            biddedQtyDecimal=biddedQtyDecimal
        )
        
        bid_area_id = config.get("BidAreaId", "N1")
        
        # 6. Check for existing bids and cancel matching sessions
        cancelled_sessions = []
        failed_sessions = []
        skipped_sessions_info = []
        
        try:
            logger.info(f"Checking for existing RTM bids with BidAreaId: {bid_area_id}")
            
            existing_bids = await api_client.get_single_bid_rtm(
                delivery_date=delivery_date_timestamp,
                bid_area_id=bid_area_id
            )
            
            total_record = existing_bids.get("TotalRecord", 0)
            # Use SingleBidDetails (not RTMBlockBidDetails which is always empty)
            rtm_bid_details = existing_bids.get("SingleBidDetails", [])
            # Note: No status filter - we compare by (Session, FromPeriodId, ToPeriodId)
            # and cancel any existing bid that matches the periods we're about to submit
            
            if total_record > 0 or len(rtm_bid_details) > 0:
                logger.info(f"Found {len(rtm_bid_details)} existing RTM bids to check for matching periods")
                
                # Build map of existing bids keyed by (Session, FromPeriodId, ToPeriodId)
                # This handles the case where one session has multiple different periods
                existing_bids_map = {}
                for bid in rtm_bid_details:
                    session = bid.get("Session")
                    from_period = bid.get("FromPeriodId")
                    to_period = bid.get("ToPeriodId")
                    
                    if session is not None and from_period and to_period:
                        key = (session, from_period, to_period)
                        existing_bids_map[key] = {
                            "OrderId": bid.get("OrderId"),
                            "LastUpdatedTime": bid.get("LastUpdatedTime"),
                            "Session": session,
                            "FromPeriodId": from_period,
                            "ToPeriodId": to_period
                        }
                
                logger.info(f"Existing bids to potentially cancel: {list(existing_bids_map.keys())}")
                
                # Track cancelled OrderIds to prevent duplicate cancel attempts
                cancelled_order_ids = set()
                
                # For each session detail in our request, check if exact (Session, FromPeriodId, ToPeriodId) exists
                for session_detail in api_payload.get("BidDetails", []):
                    session_id = session_detail.get("Session")
                    session_details = session_detail.get("SessionDetails", [])
                    
                    for sd in session_details:
                        req_from_period = sd.get("FromPeriodId")
                        req_to_period = sd.get("ToPeriodId")
                        key = (session_id, req_from_period, req_to_period)
                        
                        if key in existing_bids_map:
                            existing_info = existing_bids_map[key]
                            order_id = existing_info.get("OrderId")
                            last_updated_time = existing_info.get("LastUpdatedTime")
                            
                            # Skip if we already cancelled this OrderId
                            if order_id in cancelled_order_ids:
                                logger.info(f"Skipping duplicate cancel for OrderId={order_id}")
                                continue
                            
                            logger.info(f"Cancelling existing bid: Session={session_id}, Period={req_from_period}-{req_to_period}, OrderId={order_id}")
                            
                            try:
                                cancel_result = await api_client.cancel_rtm_bid(
                                    order_id=order_id,
                                    last_updated_time=last_updated_time,
                                    delivery_date=delivery_date_timestamp
                                )
                                
                                status_msg = cancel_result.get("StatusMessage", "")
                                
                                # Treat "doesn't exist" as success - bid is already gone
                                if cancel_result.get("Status") == "Success" or "Doesn't Exist" in status_msg:
                                    cancelled_order_ids.add(order_id)
                                    cancelled_sessions.append({
                                        "Session": session_id,
                                        "FromPeriodId": req_from_period,
                                        "ToPeriodId": req_to_period,
                                        "Status": "Success",
                                        "OrderId": order_id,
                                        "Note": "Already cancelled" if "Doesn't Exist" in status_msg else None
                                    })
                                    logger.info(f"Successfully cancelled bid (or already gone): Session={session_id}, Period={req_from_period}-{req_to_period}")
                                    
                                    # Update DB status to cancelled
                                    try:
                                        # Convert timestamp to YYYY-MM-DD for filter
                                        cancel_date_dt = datetime.fromtimestamp(delivery_date_timestamp)
                                        cancel_date_str = cancel_date_dt.strftime("%Y-%m-%d")
                                        
                                        await self.rtm_single_bid_repo.update_one(
                                            {
                                                "bid_date": cancel_date_str,
                                                "start_time": req_from_period,
                                                "end_time": req_to_period,
                                                "bid_status": {"$in": ["submitted", "approved"]}
                                            },
                                            {"$set": { "exchangeOrderId": order_id}}
                                        )
                                        await self.update_single_bid_rtm_in_system(delivery_date_timestamp, asset_id, bid_area_id)
                                        logger.info(f"Updated DB status to 'cancelled' for OrderId {order_id}")
                                    except Exception as db_err:
                                        logger.warning(f"Failed to update DB status to cancelled for {req_from_period}: {db_err}")
                                else:
                                    failed_sessions.append({
                                        "Session": session_id,
                                        "FromPeriodId": req_from_period,
                                        "ToPeriodId": req_to_period,
                                        "Status": cancel_result.get("Status", "Failed"),
                                        "Reason": status_msg,
                                        "OrderId": order_id
                                    })
                                    logger.warning(f"Failed to cancel bid: Session={session_id}, Period={req_from_period}-{req_to_period}: {cancel_result}")
                            except Exception as cancel_error:
                                failed_sessions.append({
                                    "Session": session_id,
                                    "FromPeriodId": req_from_period,
                                    "ToPeriodId": req_to_period,
                                    "Status": "Error",
                                    "Reason": str(cancel_error),
                                    "OrderId": order_id
                                })
                                logger.error(f"Error cancelling bid: Session={session_id}, Period={req_from_period}-{req_to_period}: {cancel_error}")
            else:
                logger.info("No existing RTM bids found, proceeding directly with submission")
                
        except httpx.HTTPStatusError as e:
            logger.warning(f"Error checking existing RTM bids: {e.response.text}. Proceeding with submission.")
        except Exception as e:
            logger.warning(f"Error checking existing RTM bids: {str(e)}. Proceeding with submission.")
        
        # 7. Log failed cancellations but do NOT remove periods from BidDetails
        # Failed cancellations should not block bidding
        if failed_sessions:
            logger.warning(f"{len(failed_sessions)} periods failed cancellation, but proceeding with full submission anyway")
            skipped_sessions_info = [
                {
                    "Session": s["Session"], 
                    "FromPeriodId": s["FromPeriodId"],
                    "ToPeriodId": s["ToPeriodId"],
                    "Reason": s["Reason"], 
                    "Status": s["Status"]
                } 
                for s in failed_sessions
            ]
        
        # 8. Submit to RTM API
        
        try:
            submit_response = await api_client.submit_single_bid(api_payload)
            logger.info(f"RTM External API Submission Response: {json.dumps(submit_response, default=str)}")
        except httpx.HTTPStatusError as e:
            logger.error(f"External API submission failed: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=f"Exchange API Error: {e.response.text}")
        except Exception as e:
            logger.exception(f"Submission error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Submission failed: {str(e)}")
        
        transaction_id = submit_response.get("TransactionId")
        logger.info(f"RTM Submission TransactionId: {transaction_id}")
        
        if not transaction_id:
            # If submission failed or no ID, return response as is (or raise error?)
            # Usually we return it so controller can show error
            response = submit_response
            if skipped_sessions_info:
                response["skipped_sessions"] = skipped_sessions_info
            return response
            
        
        # Start Aggregation and Status Update Logic
        try:
            # Prepare Filter Data
            # Convert timestamp to YYYY-MM-DD
            delivery_dt = datetime.fromtimestamp(delivery_date_timestamp, tz=timezone.utc)
            delivery_date_str = delivery_dt.strftime("%Y-%m-%d")
            
            # Use provided start_time or default to current time "HH:MM"
            # start_time_str = start_time.strftime("%H:%M") if start_time else datetime.now().strftime("%H:%M")
            # Using a slightly later time to ensure we catch documents inserted just now
            now = datetime.now()
            start_time_str = start_time.strftime("%H:%M") if start_time else now.strftime("%H:%M")
            
            logger.info(f"RTM Aggregation DEBUG: delivery_date_timestamp={delivery_date_timestamp}, delivery_date_str={delivery_date_str}, start_time={start_time}, current_now={now.strftime('%H:%M:%S')}, start_time_str={start_time_str}")

            # Extract periods actually being submitted to limit aggregation
            submitted_periods = []
            for session_obj in api_payload.get("BidDetails", []):
                for sd in session_obj.get("SessionDetails", []):
                    if sd.get("FromPeriodId"):
                        submitted_periods.append(sd.get("FromPeriodId"))
            
            # Aggregation Pipeline - Only target blocks that were in the submission payload
            pipeline = [
                {
                    "$match": {
                        "bid_date": delivery_date_str,
                        "inserted_time": {"$lt": start_time_str},
                        "bid_status": "approved",
                        "start_time": {"$in": submitted_periods}
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
                        "doc": {"$first": "$$ROOT"}
                    }
                },
                {
                    "$replaceRoot": {"newRoot": "$doc"}
                }
            ]
            
            logger.info(f"RTM Aggregation Pipeline: {json.dumps(pipeline, default=str)}")
            aggregated_docs = await self.rtm_single_bid_repo.aggregate(pipeline)
            logger.info(f"RTM Aggregation results: count={len(aggregated_docs)}, docs={[str(d.get('_id')) for d in aggregated_docs]}")

            # Mark as Submitted
            for doc in aggregated_docs:
                logger.debug(f"Marking doc {doc.get('_id')} as submitted")
                update_result = await self.rtm_single_bid_repo.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"bid_status": "submitted", "exchangeRevisionNo": doc["revision_no"]}}
                )
                logger.info(f"Update status for doc {doc.get('_id')}: {update_result}")
                data_rev = doc.get("rldc_revision_no")
                if data_rev is not None:
                    try:
                        await self.rtm_bidding_service.capture_serve_repo.update_exchange_submission_flag(
                            schedule_date=delivery_date_str,
                            dc_sg_type="SG",
                            rldc_revision_no=data_rev,
                            flag="Y",
                            bid_block_no=doc.get("block_no"),
                            bid_revision_no=doc.get("revision_no"),
                        )
                    except Exception as cap_err:
                        logger.warning(f"Failed to update capture_serve exchange_submission_flag: {cap_err}")
            
        except Exception as e:
            logger.exception(f"Unexpected error in RTM Aggregation/Status Update: {str(e)}")
            
        # 9. Get Bid Details
        # API requires TransactionId and DeliveryDate
        bid_details_response = await api_client.get_bid_details(transaction_id, delivery_date_timestamp, bid_type="single-bid")
        logger.info(f"RTM Bid Details Response: {json.dumps(bid_details_response, default=str)}")
        
        # Map Response to DB
        try:
             single_bid_details = bid_details_response.get("SingleBidDetails", [])
             logger.info(f"Mapping {len(single_bid_details)} bid details to database")
             for detail in single_bid_details:
                logger.info(f"Processing detail for OrderId: {detail.get('OrderId')}")
                from_period = detail.get("FromPeriodId")
                to_period = detail.get("ToPeriodId")
                user_id = detail.get("UserId")
                user_id_key = f"EX-{user_id}"
                buy_sell = detail.get("BuySell")
                pq_data = detail.get("PQData", []) or []
                bidded_qty = 0
                
                # Default to 1 to avoid division by zero
                divisor = biddedQtyDecimal if biddedQtyDecimal and biddedQtyDecimal > 0 else 1
                
                if pq_data:
                    try:
                        if buy_sell == "S":
                            target_entry = max(pq_data, key=lambda x: x.get("Price", 0))
                            bidded_qty = float(target_entry.get("Quantity", 0)) / divisor
                        else:  # Assume "B" if not "S" or fallback
                            target_entry = min(pq_data, key=lambda x: x.get("Price", float("inf")))
                            bidded_qty = float(target_entry.get("Quantity", 0)) / divisor
                    except Exception as calc_err:
                        logger.warning(f"Error calculating bidded_qty: {calc_err}")
                        bidded_qty = 0
                
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
                
                update_filter = {
                    "bid_date": delivery_date_str,
                    "start_time": from_period,
                    "end_time": to_period,
                    "bid_status": "submitted"
                }
                logger.debug(f"Update filter for OrderId {detail.get('OrderId')}: {json.dumps(update_filter)}")
                
                mapping_result = await self.rtm_single_bid_repo.update_one(
                    update_filter,
                    {"$set": update_data}
                )
                logger.info(f"Updated DB for OrderId {detail.get('OrderId')}: {mapping_result}")
                
                # Update bidded in bid_trade_serve
                try:
                    tb = BidTradeServeRepository.from_period_to_time_block(from_period)
                    if tb > 0:
                        await self.bid_trade_serve_repo.update_bidded_by_block(
                            delivery_date_ts=delivery_date_timestamp,
                            time_block=tb,
                            bid_type="RTM",
                            bidded_value=bidded_qty
                        )
                except Exception as bts_err:
                    logger.warning(f"Failed to update bid_trade_serve for block {from_period}: {bts_err}")
        except Exception as e:
             logger.exception(f"Error in RTM Response Mapping: {str(e)}")

        # 10. Store in Repository
        bid_details_record = bid_details_response.copy()
        bid_details_record["correlation_id"] = correlation_id
        bid_details_record["market_type"] = "RTM"
        bid_details_record["created_at"] = datetime.now()
        
        await self.exchange_bid_detail_repo.create_bid_detail(bid_details_record)
        
        # Add skipped sessions info to response if any
        final_response = bid_details_response.copy()
        
        # Ensure 'status' field exists for controller logging (unify 'Status' and 'status')
        if "status" not in final_response:
             final_response["status"] = final_response.get("Status", "success").lower()

        if skipped_sessions_info:
            final_response["skipped_sessions"] = skipped_sessions_info
            final_response["skipped_sessions_note"] = "These sessions were skipped due to cancellation failures"
        
        logger.info(f"RTM Submission Flow Final Result: {final_response.get('status')}")
        return final_response

    def _construct_api_payload(self, config: Dict, delivery_date: int, bid_block_array: Dict, current_user_id: str, asset_id: str, lowerDPR: int, upperDPR: int, biddedQtyDecimal: int) -> Dict[str, Any]:
        """
        Transform bidblockarray to RTM Single Bid API Payload with Calculated Sessions.
        
        Logic:
        1. Iterate through bid_block_array to find time blocks (e.g., "02:00-02:15").
        2. Calculate Session ID: Each 30-min interval is a session.
           (MinutesFromMidnight // 30) + 1.
           E.g., 02:00 -> 120 mins -> 120//30 = 4 -> Session 5.
        3. Group details by Session ID.
        """
        from collections import defaultdict
        
        participant_id = config.get("ParticipantId", "")
        login_user_id = config.get("LoginUserId", "")
        portfolio_id = config.get("PortfolioId", "")
        asset_id = config.get("AssetId", "INDIA") 
        bid_area_id = config.get("BidAreaId", "N1")
        
        # Group: { session_id: [ session_detail, ... ] }
        sessions_map = defaultdict(list)
        
        def calculate_session_id(start_time_str: str) -> int:
            try:
                h, m = map(int, start_time_str.split(":"))
                total_mins = h * 60 + m
                return (total_mins // 30) + 1
            except Exception:
                return 0

        def process_time_slot(time_slot: str, bids_dict: Dict):
             try:
                 from_time, to_time = time_slot.split("-")
             except ValueError:
                 return

             current_block_pqs = []
             buy_sell = "B"
             first_item = True

             for seq_key, bid_item in bids_dict.items():
                 price = int(float(bid_item.get("RTMPrice", 0)))
                 qty = int(float(bid_item.get("Volume", 0)* biddedQtyDecimal)) 
                 bid_type_str = bid_item.get("sell_buy_bid_type", "buy").lower()
                 qty = abs(qty)
                 if first_item:
                     buy_sell = "S" if "sell" in bid_type_str else "B"
                     first_item = False

                 # Skip zero-qty entries EXCEPT for boundary prices (0 and 10000) which are required by provider
                
                 if buy_sell == "S":
                     qty = -qty
                 current_block_pqs.append({
                     "Price": price,
                     "Quantity": qty
                 })
             
             if current_block_pqs:
                 # Calculate max_qty to check if there's actual bid data
                 max_qty = max(abs(pq["Quantity"]) for pq in current_block_pqs) if current_block_pqs else 0
                 
                 # Skip this block if there's no actual data (max_qty == 0)
                 # Don't add empty boundary prices - just skip the block entirely
                 if max_qty == 0:
                     logger.warning(f"Skipping time slot {time_slot} - no actual volume data")
                     return  # Skip this block entirely
                 
                 # Ensure boundary prices (0 and 10000) are always included
                 # For Buy: Price 0 = max qty, Price 10000 = 0
                 # For Sell: Price 0 = 0, Price 10000 = max qty (negative)
                 existing_prices = {pq["Price"] for pq in current_block_pqs}
                 
                 if 0 not in existing_prices:
                     if buy_sell == "B":
                         current_block_pqs.insert(0, {"Price": 0, "Quantity": max_qty})
                     else:  # Sell
                         current_block_pqs.insert(0, {"Price": 0, "Quantity": 0})
                 
                 if 10000 not in existing_prices:
                     if buy_sell == "B":
                         current_block_pqs.append({"Price": 10000, "Quantity": 0})
                     else:  # Sell
                         current_block_pqs.append({"Price": 10000, "Quantity": -max_qty})
                 
                 session_id = calculate_session_id(from_time.strip())
                 sessions_map[session_id].append({
                     "FromPeriodId": from_time.strip(),
                     "ToPeriodId": to_time.strip(),
                     "BuySell": buy_sell,
                     "PQData": current_block_pqs
                 })

        # Iterate bid_block_array
        # Handling potentially flattened or nested structure
        for key, val in bid_block_array.items():
            # Check if key is time slot "HH:MM-HH:MM"
            str_key = str(key)
            if "-" in str_key and ":" in str_key:
                 process_time_slot(str_key, val)
            elif isinstance(val, dict):
                 # Assume nested "Index" -> "TimeSlot"
                 for sub_key, sub_val in val.items():
                     str_sub_key = str(sub_key)
                     if "-" in str_sub_key and ":" in str_sub_key:
                         process_time_slot(str_sub_key, sub_val)

        # Build final structure
        bid_details = []
        for sess_id in sorted(sessions_map.keys()):
            bid_details.append({
                "Session": sess_id,
                "SessionDetails": sessions_map[sess_id]
            })

        return {
            "DeliveryDate": delivery_date,
            "AssetId": asset_id,
            "BidAreaId": bid_area_id, 
            "UserId": login_user_id,
            "ParticipantId": participant_id,
            "PortfolioId": portfolio_id,
            "InitiatedBy": login_user_id, 
            "BidDetails": bid_details
        }

    async def withdraw_bid(
        self,
        time_blocks: List[int],
        correlation_id: str = None,
        schedule_date: str = None
    ) -> Dict[str, Any]:
        """
        Withdraw RTM bids for specific time blocks.
        
        Args:
            time_blocks: List of time block integers (1-96)
            correlation_id: Request correlation ID
            
        Flow:
        1. Convert time blocks to session IDs
        2. Get existing RTM bids
        3. Match sessions with FromPeriodId/ToPeriodId
        4. Cancel matching bids
        """
        if not correlation_id:
            import uuid
            correlation_id = str(uuid.uuid4())
            
        logger.info(f"Starting RTM bid withdrawal for blocks {time_blocks}, correlation_id={correlation_id}")
        
        # 1. Get config
        config = await self.exchange_config_repo.get_bidType_market_active_config("single", "RTM")
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
            market_type="RTM",
            bid_type="single-bid"
        )
        isLiveConnection = config.get("isLiveConnection", False) 
        
        # 3. Get delivery date
        # If schedule_date is tomorrow, use "T+1"; otherwise use "T"
        delivery_dates_resp = await api_client.get_delivery_dates(bid_type="single-bid")
        delivery_date_list = delivery_dates_resp.get("DeliveryDates", [])
        
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        
        delivery_date_id = "T"
        if schedule_date:
            try:
                sched_date = datetime.strptime(schedule_date, "%Y-%m-%d").date()
                if sched_date == tomorrow:
                    delivery_date_id = "T+1"
                    logger.info(f"Schedule date ({sched_date}) is tomorrow, using T+1")
            except (ValueError, AttributeError):
                logger.warning(f"Could not parse schedule_date '{schedule_date}', defaulting to T")
        
        selected_date_record = next((d for d in delivery_date_list if d.get("DeliveryDateId") == delivery_date_id), None)
        if not selected_date_record:
            raise ValueError(f"Delivery Date '{delivery_date_id}' not found in exchange response")
        delivery_date_timestamp = selected_date_record.get("DeliveryDate")
        delivery_dt = datetime.fromtimestamp(delivery_date_timestamp, tz=timezone.utc)
        delivery_date_str = delivery_dt.strftime("%Y-%m-%d")
        
        logger.info(f"Withdraw using delivery date: {delivery_date_id} ({delivery_date_str})")
        
        bid_area_id = config.get("BidAreaId", "N1")
        
        # 4. Convert time blocks to sessions and time periods
        # Each block is 15 minutes: block 1 = 00:00-00:15, block 2 = 00:15-00:30, etc.
        # Session is 30 minutes: session 1 = blocks 1,2, session 2 = blocks 3,4, etc.
        target_sessions = set()
        target_periods = []
        
        for block in time_blocks:
            if block < 1 or block > 96:
                continue
            
            start_minutes = (block - 1) * 15
            end_minutes = block * 15
            
            from_hour = start_minutes // 60
            from_min = start_minutes % 60
            end_hour = end_minutes // 60
            end_min = end_minutes % 60
            
            from_period = f"{from_hour:02d}:{from_min:02d}"
            to_period = f"{end_hour:02d}:{end_min:02d}"
            
            # Session = (start_minutes // 30) + 1
            session_id = (start_minutes // 30) + 1
            target_sessions.add(session_id)
            target_periods.append({
                "session": session_id,
                "from_period": from_period,
                "to_period": to_period,
                "block": block
            })
        
        logger.info(f"Target periods for withdrawal: {target_periods}")
        
        # 5. Get existing RTM bids
        existing_bids = await api_client.get_single_bid_rtm(
            delivery_date=delivery_date_timestamp,
            bid_area_id=bid_area_id,
            isLiveConnection=isLiveConnection
        )
        
        total_record = existing_bids.get("TotalRecord", 0)
        rtm_bid_details = existing_bids.get("SingleBidDetails", [])
        
        if total_record == 0 and len(rtm_bid_details) == 0:
            logger.info("No existing RTM bids found to withdraw")
            return {
                "success": True,
                "message": "No existing bids found to withdraw",
                "withdrawn_count": 0,
                "correlation_id": correlation_id
            }
        
        # 6. Match by exact (FromPeriodId, ToPeriodId) block-wise, not session-wise
        # Build set of target period keys from the requested blocks
        target_period_keys = {
            (tp["from_period"], tp["to_period"]) for tp in target_periods
        }
        
        orders_to_cancel = []
        for bid in rtm_bid_details:
            from_period = bid.get("FromPeriodId")
            to_period = bid.get("ToPeriodId")
            if (from_period, to_period) in target_period_keys:
                if bid.get("OrderStatus") != "Cancelled": 
                    orders_to_cancel.append({
                        "OrderId": bid.get("OrderId"),
                        "LastUpdatedTime": bid.get("LastUpdatedTime"),
                        "Session": bid.get("Session"),
                        "FromPeriodId": from_period,
                        "ToPeriodId": to_period
                    })
        
        if not orders_to_cancel:
            logger.info("No matching bids found for specified time blocks")
            return {
                "success": True,
                "message": "No matching bids found for specified time blocks",
                "withdrawn_count": 0,
                "correlation_id": correlation_id
            }
        
        # 7. Cancel each matching order
        cancelled = []
        failed = []
        
        for order in orders_to_cancel:
            try:
                cancel_result = await api_client.cancel_rtm_bid(
                    order_id=order["OrderId"],
                    last_updated_time=order["LastUpdatedTime"],
                    delivery_date=delivery_date_timestamp
                )
                
                if cancel_result.get("Status") == "Success":
                    cancelled.append({
                        "Session": order["Session"],
                        "OrderId": order["OrderId"],
                        "Status": "Success"
                    })
                    
                    # 7a. Immediate status update in DB to 'withdrawn'
                    try:
                        await self.rtm_single_bid_repo.update_one(
                            {
                                "bid_date": delivery_date_str,
                                "start_time": order["FromPeriodId"],
                                "end_time": order["ToPeriodId"],
                                # Only update if it was in a pending or submitted state
                                "bid_status": {"$in": ["submitted", "approved", "approved_withdrawal"]}
                            },
                            {"$set": {
                                "bid_status": "withdrawn",
                                "updatedAt": datetime.now(),
                                "exchangeOrderId": order["OrderId"]
                            }}
                        )
                    except Exception as db_err:
                        logger.warning(f"Failed to update status to 'withdrawn' for block {order['FromPeriodId']}: {db_err}")

                    # 7b. Update bid_trade_serve: set bidded=0 for withdrawn block
                    try:
                        tb = BidTradeServeRepository.from_period_to_time_block(order["FromPeriodId"])
                        if tb > 0:
                            await self.bid_trade_serve_repo.update_bidded_by_block(
                                delivery_date_ts=delivery_date_timestamp,
                                time_block=tb,
                                bid_type="RTM",
                                bidded_value=0
                            )
                    except Exception as bts_err:
                        logger.warning(f"Failed to update bid_trade_serve on withdrawal for block {order['FromPeriodId']}: {bts_err}")
                    
                    # 7c. Enrichment: Fetch latest bid details from the exchange
                    cancel_transaction_id = cancel_result.get("TransactionId")
                    if cancel_transaction_id:
                        try:
                            logger.info(f"Fetching bid details for cancellation TransactionId={cancel_transaction_id}")
                            bid_details_response = await api_client.get_cancel_bid_details(cancel_transaction_id, delivery_date_timestamp, bid_type="single-bid")
                            
                            if isinstance(bid_details_response, dict):
                                # Store for audit
                                bid_details_record = bid_details_response.copy()
                                bid_details_record["correlation_id"] = correlation_id
                                bid_details_record["market_type"] = "RTM"
                                bid_details_record["bid_type"] = "withdrawal"
                                bid_details_record["created_at"] = datetime.now()
                                await self.exchange_bid_detail_repo.create_bid_detail(bid_details_record)
                                
                                # Enrich the bid record
                                exchange_details = bid_details_response.get("SingleBidDetails", [])
                                for detail in exchange_details:
                                    if detail.get("FromPeriodId") == order["FromPeriodId"]:
                                        update_data = {
                                            "exchangeStatus": detail.get("OrderStatus"),
                                            "exchangeOrderTime": detail.get("OrderTime"),
                                            "exchangeError": detail.get("Error"),
                                            "exchangeLastUpdatedTime": detail.get("LastUpdatedTime"),
                                            "exchangePQData": detail.get("PQData"),
                                            "updatedAt": datetime.now(),
                                            "exchangeUserId": detail.get("UserId"),
                                            "cancel_transaction_id": cancel_transaction_id
                                        }
                                        await self.rtm_single_bid_repo.update_one(
                                            {
                                                "bid_date": delivery_date_str,
                                                "start_time": order["FromPeriodId"],
                                                "end_time": order["ToPeriodId"]
                                            },
                                            {"$set": update_data}
                                        )
                            else:
                                logger.warning(f"Unexpected response type from get_bid_details: {type(bid_details_response)}")
                        except Exception as details_err:
                            logger.warning(f"Failed to enrich bid details after withdrawal for block {order['FromPeriodId']}: {details_err}")
                else:
                    failed.append({
                        "Session": order["Session"],
                        "OrderId": order["OrderId"],
                        "Status": cancel_result.get("Status", "Failed"),
                        "Reason": cancel_result.get("StatusMessage", "Unknown error")
                    })
            except Exception as e:
                failed.append({
                    "Session": order["Session"],
                    "OrderId": order["OrderId"],
                    "Status": "Error",
                    "Reason": str(e)
                })
        
        return {
            "success": len(failed) == 0,
            "message": f"Withdrawn {len(cancelled)} bids, {len(failed)} failed" if failed else f"Successfully withdrawn {len(cancelled)} bids",
            "withdrawn_count": len(cancelled),
            "withdrawn_sessions": cancelled,
            "failed_sessions": failed if failed else None,
            "correlation_id": correlation_id
        }

    async def update_single_bid_rtm_in_system(self, delivery_date: int, asset_id: str, bid_area_id: str) -> Dict[str, Any]:
        """
        Fetch RTM Single Bid Book from exchange and update cancelled bids in DB.
        """
        config = await self.exchange_config_repo.get_bidType_market_active_config("single", "RTM")
        if not config:
            raise ValueError("No active exchange configuration found")
            
        api_client = ExchangeAPIClient(
            base_url=config.get("baseUrl", ""),
            login_user_id=config.get("LoginUserId", ""),
            participant_id=config.get("ParticipantId", ""),
            portfolio_id=config.get("PortfolioId", ""),
            bearer_token=config.get("BearerToken", ""),
            market_type="RTM",
            bid_type="single-bid",
        )
        rtm_bid_response = await api_client.get_single_bid_rtm(
            delivery_date=delivery_date,
            bid_area_id=bid_area_id
        )

        single_bid_details = rtm_bid_response.get("SingleBidDetails", [])
        for bid in single_bid_details:
            order_id = bid.get("OrderId")
            order_status = bid.get("OrderStatus")
            if order_status == "Cancelled":
                # Find the matching RTM bid in our DB
                matching_docs = await self.rtm_single_bid_repo.find_by_filters({"exchangeOrderId": order_id})
                rtm_bid_data = matching_docs[0] if matching_docs else None
                if rtm_bid_data:
                    current_revision = rtm_bid_data.get("revision_no", 0)
                    current_exchange_revision = rtm_bid_data.get("exchangeRevisionNo", 0)
                    await self.rtm_single_bid_repo.update_one(
                        {"_id": rtm_bid_data["_id"]},
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
        session: int = 0,
        from_period: str = "ALL",
        to_period: str = "ALL",
        buy_sell: str = "ALL",
        bid_area_id: str = "ALL",
        trade_id: int = 0,
        order_id: int = 0,
        sorting_type: int = 2,
        page_number: int = 0,
        page_size: int = 0,
        correlation_id: str = None
    ) -> Dict[str, Any]:
        """
        Fetch RTM trade book data from exchange API.
        """
        if not correlation_id:
            import uuid
            correlation_id = str(uuid.uuid4())
            
        logger.info(f"Fetching RTM trade book, correlation_id={correlation_id}")
        
        # 1. Get config
        config = await self.exchange_config_repo.get_bidType_market_active_config("single", "RTM")
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
            market_type="rtm",
            bid_type="trade-book"
        )
        
        # 3. Get delivery date if not provided
        if not delivery_date:
            delivery_dates_resp = await api_client.get_delivery_dates(bid_type="single-bid")
            delivery_date_list = delivery_dates_resp.get("DeliveryDates", [])
            
            # If current time < 22:30, use "T" (today); otherwise use "T+1" (tomorrow)
            current_time = datetime.now()
            cutoff_time = current_time.replace(hour=22, minute=30, second=0, microsecond=0)
            delivery_date_id = "T" if current_time < cutoff_time else "T+1"
            
            selected_date_record = next((d for d in delivery_date_list if d.get("DeliveryDateId") == delivery_date_id), None)
            if not selected_date_record:
                raise ValueError(f"Delivery Date '{delivery_date_id}' not found in exchange response")
            delivery_date = selected_date_record.get("DeliveryDate")
        
        # 4. Fetch trade book
        trade_book_response = await api_client.get_rtm_trade_book(
            delivery_date=delivery_date,
            session=session,
            from_period=from_period,
            to_period=to_period,
            buy_sell=buy_sell,
            bid_area_id=bid_area_id,
            trade_id=trade_id,
            order_id=order_id,
            sorting_type=sorting_type,
            page_number=page_number,
            page_size=page_size
        )
        
        total_records = trade_book_response.get("TotalRecord", 0)
        trade_details = trade_book_response.get("TradeDetails", [])
        
        logger.info(f"RTM trade book fetched successfully: {total_records} records")
        
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
        Fetch RTM Price Quantity (MCP) data from exchange API.
        """
        if not correlation_id:
            import uuid
            correlation_id = str(uuid.uuid4())
            
        logger.info(f"Fetching RTM price quantity, correlation_id={correlation_id}")
        
        # 1. Get config
        config = await self.exchange_config_repo.get_bidType_market_active_config("single", "RTM")
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
            market_type="rtm",
            bid_type="price-quantity"
        )
        
        # 3. Get delivery date if not provided
        if not delivery_date:
            delivery_dates_resp = await api_client.get_delivery_dates(bid_type="single-bid")
            delivery_date_list = delivery_dates_resp.get("DeliveryDates", [])
            
            # If current time < 22:30, use "T" (today); otherwise use "T+1" (tomorrow)
            current_time = datetime.now()
            cutoff_time = current_time.replace(hour=22, minute=30, second=0, microsecond=0)
            delivery_date_id = "T" if current_time < cutoff_time else "T+1"
            
            selected_date_record = next((d for d in delivery_date_list if d.get("DeliveryDateId") == delivery_date_id), None)
            if not selected_date_record:
                raise ValueError(f"Delivery Date '{delivery_date_id}' not found in exchange response")
            delivery_date = selected_date_record.get("DeliveryDate")
        
        return await self._fetch_price_quantity_data(api_client, delivery_date, correlation_id)

    async def _fetch_price_quantity_data(self, api_client: ExchangeAPIClient, delivery_date: int, correlation_id: str) -> Dict[str, Any]:
         logger.info(f"Fetching RTM price quantity for DeliveryDate={delivery_date}")
         pq_response = await api_client.get_rtm_price_quantity(delivery_date)
         return {
            "success": True,
            "data": pq_response,
            "correlation_id": correlation_id
        }

    async def get_rtm_book(self) -> List[Dict[str, Any]]:
        """
        Get RTM Single Bid Book details from repository.
        """
        try:
            # 1. Get Exchange Config for RTM
            config = await self.exchange_config_repo.get_bidType_market_active_config("single", "RTM")
            if not config:
                 logger.error("No active exchange configuration found for RTM")
                 return []

            # 2. Init Client
            api_client = ExchangeAPIClient(
                base_url=config.get("baseUrl", ""),
                login_user_id=config.get("LoginUserId", ""),
                participant_id=config.get("ParticipantId", ""),
                portfolio_id=config.get("PortfolioId", ""),
                bearer_token=config.get("BearerToken", ""),
                market_type="RTM",
                bid_type="single-bid"
            )
            
            # 3. Fetch Delivery Dates
            delivery_dates_resp = await api_client.get_delivery_dates(bid_type="single-bid")
            
            # 4. Iterate and Fetch Books
            results = []
            d_list = delivery_dates_resp.get("DeliveryDates", [])
            
            from ..repository.rtm_book_repository import rtm_book_repository
            
            for d in d_list:
                d_id = d.get("DeliveryDateId")
                d_timestamp = d.get("DeliveryDate")
                
                if d_timestamp:
                    logger.info(f"Fetching RTM Book for {d_id} ({d_timestamp})")
                    book_data = await rtm_book_repository.get_book_by_delivery_date(d_timestamp)
                    results.append({
                        "DeliveryDateId": d_id,
                        "DeliveryDate": d_timestamp,
                        "Book": book_data
                    })
            
            return results
            
        except Exception as e:
             logger.exception(f"Error fetching RTM books: {e}")
             return []

rtm_transaction_service = RTMTransactionService()
