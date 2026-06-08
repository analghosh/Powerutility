import asyncio
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Dict, Any, List, Optional

import httpx
import pandas as pd
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from ..repository.block_bid_current_data_repository import BlockBidCurrentDataRepository
from ..repository.block_bid_book_repository import BlockBidBookRepository
from ..repository.decision_rtm_block_bid_iex_response_repository import DecisionRtmBlockBidIexResponseRepository
from ..repository.decision_dam_block_bid_iex_response_repository import DecisionDamBlockBidIexResponseRepository
from ..repository.exchange_config_repository import ExchangeConfigRepository
from ..repository.integration_api_log_repository import IntegrationApiLogRepository
from ..repository.limit_values_repository import LimitValuesRepository
from ..iex.masterApi import ExchangeAPIClient

logger = logging.getLogger(__name__)

# IEX RTM API v2.0 returns string OrderStatus values directly — no integer mapping needed.
# Valid values: "Submitted", "Executed", "Rejected", "Cancelled", "Excluded", "Provisional"
VALID_ORDER_STATUSES = {"Submitted", "Executed", "Rejected", "Cancelled", "Excluded", "Provisional"}


class BlockBidServices:
    def __init__(self, collection_name: str = "block_bid_current_data"):
        self.block_bid_repo = BlockBidCurrentDataRepository()
        self.block_bid_book_repo = BlockBidBookRepository()
        self.iex_response_repo = DecisionRtmBlockBidIexResponseRepository()
        self.dam_iex_response_repo = DecisionDamBlockBidIexResponseRepository()
        self.exchange_config_repo = ExchangeConfigRepository()
        self.api_log_repo = IntegrationApiLogRepository()
        self.limit_values_repo = LimitValuesRepository()


    async def insert_block_bid(self, request: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now()

        # Determine inserted date
        date = (now + timedelta(days=1)).strftime("%d-%m-%Y") if now.time() > datetime.strptime("22:30", "%H:%M").time() else now.strftime("%d-%m-%Y")
        dateObj = datetime.strptime(date, "%d-%m-%Y")  # type: ignore # parse string to datetime
        formattedDate = dateObj.strftime("%Y-%m-%d")
        if not request:
            raise HTTPException(status_code=400, detail="Invalid request")

        bidding_date = request.get("dates")
        date_obj = datetime.strptime(bidding_date, "%d-%m-%Y")  # type: ignore # parse string to datetime
        formatted_date = date_obj.strftime("%Y-%m-%d")
        time_block_range = []
        data = []

        market_type_list = request.get("market_type", [])
        from_list    = request.get("from", [])
        trade_sessions_raw = request.get("trade_session", []) or []
        trade_sessions = []
        # DAM block bid has no session concept — use trade_session=0
        for i in range(len(from_list)):
            market_type = market_type_list[i].upper()

            if market_type == "DAM":
                session = 0
            else:
                session = (
                    int(trade_sessions_raw[i])
                    if i < len(trade_sessions_raw) and trade_sessions_raw[i] not in (None, "")
                    else 0
                )

            trade_sessions.append(session)

        # Fetch current max revision per (session, time_block) so each new insert gets max+1
        session_ints = [int(s) for s in trade_sessions]
        max_revisions = await self.block_bid_repo.get_max_revisions(formatted_date, market_type, session_ints)

        for i in range(len(from_list)):
            session = int(trade_sessions[i])
            time_block = f"{request['from'][i]}-{request['to'][i]}"
            revision_no = max_revisions.get((session, time_block), 0) + 1
            record = {
                "trade_session": session,
                "from": request["from"][i],
                "to": request["to"][i],
                "time_block": time_block,
                "quantum": int(request["quantum"][i]),
                "bid_price": float(request["bid_price"][i]),
                "number_of_bids": int(request["number_of_bids"][i]),
                "inserted_date": formattedDate,
                "created_by": request.get("current_user") or request.get("user_id") or request.get("username") or "",
                "created_time": now.strftime("%d-%m-%Y %H:%M:%S"),
                "approve_by": "",  # Empty initially, set only when approved
                "approve_time": "",
                "status": "pending",
                "biding_date": formatted_date,
                "reason": "",
                "type": request["type"],
                "market_type": request["market_type"][i],
                "exchange": request["exchange"][i],
                "selected_for": request["selected_for"][i],
                "ex_split_string": f"IEX-{request['iex_price'][i]}_PXIL-{request['pxil_price'][i]}",
                "revision_no": revision_no,
            }
            data.append(record)
            time_block_range.append(time_block)

        # Insert into MongoDB
        try:
            insert_result = await self.block_bid_repo.insert_many(data)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        # Notification placeholder
        notification_data = {
            "bid_status": "submitForApproval Block Bid",
            "module_name": "Block Bid",
            "title": "RTM Block Bid Submitted for Approval",
            "description": "RTM Block bid submitted for approval of selected time blocks",
            "time_block": time_block_range,
            "submitted_time": now.strftime("%d-%m-%Y %H:%M"),
            "notification_type": "bid_for_Approvel"
        }
        logger.info(f"Notification placeholder: {insert_result}")

        if insert_result:
            return {"message": "Bid Submitted Successfully For Approval."}

        raise HTTPException(status_code=500, detail="Data could not be created")


    async def get_block_bid_submitted_for_approval_list(self, date):
       data = await self.block_bid_repo.get_data(date)
       logger.info(f" placeholder: {data}")
       return data
    
    async def update_block_bid_status(self, request: Dict[str, Any]):
        try:
            
            statuses = request.get("status", [])
            reasons = request.get("reason", [])
            rej_reasons = request.get("rej_reason", [])
            other_remarks = request.get("other_remark", [])
            other_remark_rej = request.get("other_remark_rej", [])
            ids = request.get("id", [])

            for i in range(len(statuses)):
                if statuses[i] is None:
                    continue

                # ✅ Determine remark
                if statuses[i] == "Approved":
                    remark = reasons[i]
                    if remark == "other":
                        remark = other_remarks[i]
                else:
                    remark = rej_reasons[i]
                    if remark == "other":
                        remark = other_remark_rej[i]

                # ✅ Prepare data for update
                data = {
                    "status": statuses[i],
                    "approve_by": request.get("current_user") or request.get("user_id") or request.get("username") or "",
                    "approve_time": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
                    "remark": remark,
                }
                
                # ✅ Update DB record
                await self.block_bid_repo.updatedata(ids[i], data)

                # ✅ Notification data
                status_label = "Approved" if statuses[i] == "Approved" else "Rejected"
                notification_data = {
                    "value_status": statuses[i],
                    "module_name": "Block Bid",
                    "title": f"RTM Block Bid {status_label} by PTC",
                    "description": f"RTM Block Bid {status_label} for , {remark}",
                    "action": "#",
                    "notification_type": "bid_for_approve_reject",
                }

               
            return {"message": "Bid Approved Successfully"}

        except Exception as e:
            logger.error(f"Failed to get pending approvals in : {str(e)}")
            raise

    async def submit_to_exchange(self, biding_date: str, submitted_by: str = "", correlation_id: str = "") -> Dict[str, Any]:
        """
        Submit all Approved block bids for biding_date to IEX exchange.

        Flow (IEX RTM API v2.0):
          1. Load exchange config (BearerToken, LoginUserId, ParticipantId, PortfolioId, BidAreaId)
          2. Fetch AssetId dynamically via get_asset_details() — same as single bid
          3. Fetch DeliveryDate Unix timestamp via get_delivery_dates() — T or T+1
          4. Fetch approved bids from block_bid_current_data
          5. Validate total number_of_bids <= 60
          6. Build BidDetails[] with PascalCase fields — expand number_of_bids per record
          7. POST to exchange POST /rtm/api/v2/blockbid
             → Response: {TransactionId, Status, StatusMessage}
          7. Wait 4 seconds then GET /rtm/api/v2/biddetails/{...} for per-bid order data
             → Response: {RTMBlockBidDetails: [{OrderId, Session, FromPeriodId, ToPeriodId,
                          BuySell, Price, Quantity, BidReferenceId, OrderStatus, LastUpdatedTime}]}
          8. Bulk update block_bid_current_data status -> "Submitted"
          9. Update existing docs + insert per-OrderId docs into iex_response collection
        """
        # 1. Exchange config
        config = await self.exchange_config_repo.get_bidType_market_active_config("block", "RTM")
        if not config:
            raise HTTPException(status_code=503, detail="No active RTM block bid exchange configuration found")

        base_url       = config.get("baseUrl", "")
        user_id        = config.get("LoginUserId", "")
        participant_id = config.get("ParticipantId", "")
        portfolio_id   = config.get("PortfolioId", "")
        bearer_token   = config.get("BearerToken", "")
        bid_area_id    = config.get("BidAreaId", "")

        api_client = ExchangeAPIClient(
            base_url=base_url,
            login_user_id=user_id,
            participant_id=participant_id,
            portfolio_id=portfolio_id,
            bearer_token=bearer_token,
            correlation_id=correlation_id,
            market_type="rtm",
            bid_type="block-bid",
        )

        # 2 & 3. Fetch AssetId and DeliveryDate in parallel — both are independent exchange calls
        try:
            asset_response, delivery_dates_resp = await asyncio.gather(
                api_client.get_asset_details(bid_type="block-bid"),
                api_client.get_delivery_dates(bid_type="block-bid"),
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Exchange API timed out fetching asset/delivery info.")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Exchange API error: {e.response.text}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to reach exchange API: {type(e).__name__}: {e}")

        asset_details_list = asset_response.get("AssetDetails", [])
        asset_id = asset_details_list[0].get("AssetId", "") if asset_details_list else ""
        logger.info(f"Asset details fetched: AssetId={asset_id}")
        biddedQtyDecimal = asset_details_list[0].get("OrderQtyDecimal", 10) if asset_details_list else 10
        delivery_date_list = delivery_dates_resp.get("DeliveryDates", [])

        today    = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        try:
            biding_date_obj = datetime.strptime(biding_date, "%Y-%m-%d").date()
        except ValueError:
            biding_date_obj = today

        delivery_date_id = "T+1" if biding_date_obj == tomorrow else "T"
        selected_date = next((d for d in delivery_date_list if d.get("DeliveryDateId") == delivery_date_id), None)
        if not selected_date:
            raise HTTPException(status_code=404, detail=f"Delivery date '{delivery_date_id}' not found from exchange")

        delivery_date_ts = selected_date.get("DeliveryDate")
        logger.info(f"DeliveryDate fetched from exchange: {delivery_date_id} → {delivery_date_ts}")

        # 4. Approved bids
        bids = await self.block_bid_repo.get_approved_bids(biding_date, market_type="RTM")
        if not bids:
            raise HTTPException(status_code=404, detail=f"No approved block bids found for {biding_date}")

        # 5. Validate 60-bid exchange limit
        total_bids = sum(int(b.get("number_of_bids", 1)) for b in bids)
        if total_bids > 60:
            raise HTTPException(status_code=400, detail="Bid Quota Exists! Total number_of_bids exceeds exchange limit of 60")

        now = datetime.now()
        day_str = now.strftime("%d")
        user_id_prefix = str(user_id)[:5].ljust(5)

        try:
            bid_ref_base = await api_client.get_bid_reference_id()
            bid_ref_start = bid_ref_base.get("RTMBlockBidRefCount", 0)
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Exchange API timed out fetching bid reference ID.")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Exchange API error fetching bid reference: {e.response.text}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch bid reference ID: {type(e).__name__}: {e}")

        all_transaction_ids: List[str] = []
        all_response_docs: List[Dict[str, Any]] = []
        blck_count = 0

        # 6. Submit each approved bid individually — exchange only processes one BidDetails entry at a time
        for bid in bids:
            bid_id      = bid.get("_id", "")
            from_period_id = bid.get("from", "00:00")
            to_period_id   = bid.get("to", "00:00")

            ex_split = bid.get("ex_split_string", "IEX-100_PXIL-0")
            try:
                iex_value = int(ex_split.split("_")[0].split("-")[1])
            except (IndexError, ValueError):
                iex_value = 100

            quantum  = int(bid.get("quantum", 0))
            buy_sell = "S" if bid.get("type", "Sell") == "Sell" else "B"
            iex_quantity = (quantum * iex_value) / 100
            quantity_exchange = iex_quantity * biddedQtyDecimal * (-1 if buy_sell == "S" else 1)

            num_bids = int(bid.get("number_of_bids", 1))
            bid_details_for_this_bid: List[Dict[str, Any]] = []
            for _ in range(num_bids):
                blck_count += 1
                bid_ref_id = f"{user_id_prefix}{day_str}{bid_ref_start + blck_count}"
                bid_details_for_this_bid.append({
                    "Session":        int(bid.get("trade_session", 1)),
                    "FromPeriodId":   from_period_id,
                    "ToPeriodId":     to_period_id,
                    "BuySell":        buy_sell,
                    "Price":          int(bid.get("bid_price", 0)),
                    "Quantity":       int(quantity_exchange),
                    "BidReferenceId": bid_ref_id,
                })

            request_body = {
                "DeliveryDate":  delivery_date_ts,
                "AssetId":       asset_id,
                "BidAreaId":     bid_area_id,
                "UserId":        user_id,
                "ParticipantId": participant_id,
                "PortfolioId":   portfolio_id,
                "InitiatedBy":   user_id,
                "BidDetails":    bid_details_for_this_bid,
            }

            try:
                response_data = await api_client.submit_block_bid(request_body)
            except Exception as e:
                logger.error(f"Exchange API error for bid from={from_period_id} to={to_period_id}: {e}")
                continue

            transaction_id = response_data.get("TransactionId") if isinstance(response_data, dict) else None
            submit_status  = response_data.get("Status", "Unknown") if isinstance(response_data, dict) else "Unknown"

            if submit_status != "Success" or not transaction_id:
                logger.error(f"Bid submission failed from={from_period_id} to={to_period_id}: {response_data}")
                continue

            logger.info(f"Bid submitted from={from_period_id} to={to_period_id}, TransactionId={transaction_id}")

            # Wait 4 seconds then fetch per-bid order details from exchange
            await asyncio.sleep(4)

            try:
                bid_details_response = await api_client.get_bid_details(
                    transaction_id=str(transaction_id),
                    delivery_date=delivery_date_ts,
                    bid_type="block-bid",
                )
            except Exception as e:
                logger.warning(f"get_bid_details failed (TransactionId={transaction_id}): {e}")
                bid_details_response = {}

            rtm_bid_details = bid_details_response.get("RTMBlockBidDetails", []) if isinstance(bid_details_response, dict) else []
            updated_at = datetime.now()

            if rtm_bid_details:
                last_val   = rtm_bid_details[-1]
                order_id   = last_val.get("OrderId", "")
                order_status = last_val.get("OrderStatus", "Submitted")
            else:
                order_id     = ""
                order_status = "Submitted"

            # Mark this specific bid as Submitted
            await self.block_bid_repo.updatedata(bid_id, {
                "status":                  "Submitted",
                "exchangeStatus":          order_status,
                "exchangeOrderId":         order_id,
                "exchangeLastUpdatedTime": last_val.get("LastUpdatedTime", "") if rtm_bid_details else "",
                "orderId":                 order_id,
                "updatedAt":               updated_at,
            })

            for val in rtm_bid_details:
                from_time = val.get("FromPeriodId", "")
                to_time   = val.get("ToPeriodId", "")
                oid       = val.get("OrderId", "")
                all_response_docs.append({
                    "trade_session":     val.get("Session"),
                    "from":              from_time,
                    "to":                to_time,
                    "time_block":        f"{from_time}-{to_time}",
                    "quantum":           abs(float(val.get("Quantity", 0))) / 10,
                    "bid_price":         float(val.get("Price", 0)),
                    "order_id":          oid,
                    "ref_id":            val.get("BidReferenceId", ""),
                    "type":              "Sell" if val.get("BuySell") == "S" else "Buy",
                    "status":            val.get("OrderStatus", "Submitted"),
                    "last_updated_time": val.get("LastUpdatedTime", ""),
                    "transaction_id":    transaction_id,
                    "Biding_date":       biding_date,
                    "inserted_date":     biding_date,
                    "created_by":        submitted_by,
                    "created_time":      now.strftime("%d-%m-%Y %H:%M:%S"),
                })

            all_transaction_ids.append(str(transaction_id))

        if all_response_docs:
            await self.iex_response_repo.bulk_insert_response_docs(all_response_docs)

        total_submitted = len(all_transaction_ids)
        logger.info(f"Block bid submission complete: {total_submitted}/{len(bids)} bids submitted, {len(all_response_docs)} exchange records")
        return {
            "status": "success",
            "message": f"{total_submitted} bid(s) submitted to exchange!",
            "transaction_ids": all_transaction_ids,
            "bids_submitted": total_submitted,
        }

    async def export_iex_excel(self, biding_date: str, market_type: str = "RTM") -> StreamingResponse:
        """
        Generate IEX Excel file from approved block bids.

        Column headers use exchange parameter values:
          Portfolio_Id_IEX | User_Id_IEX | Participant_Id_IEX | INDIA-RTM

        Each bid is expanded into number_of_bids identical rows.
        Quantity = (quantum * -1 * iex_split%) / 100
        """
        bids = await self.block_bid_repo.get_approved_bids(biding_date, market_type)

        if not bids:
            raise HTTPException(status_code=404, detail=f"No approved block bids found for {biding_date}")

        # Fetch column header values from exchange config (block RTM)
        config = await self.exchange_config_repo.get_bidType_market_active_config("block", market_type)
        if not config:
            raise HTTPException(status_code=503, detail="No active RTM block bid exchange configuration found")

        portfolio_id   = config.get("PortfolioId", "PortfolioId")
        user_id_iex    = config.get("LoginUserId", "LoginUserId")
        participant_id = config.get("ParticipantId", "ParticipantId")

        rows = []
        for bid in bids:
            ex_split = bid.get("ex_split_string", "IEX-100_PXIL-0")
            try:
                iex_part  = ex_split.split("_")[0]           # "IEX-60"
                iex_value = int(iex_part.split("-")[1])       # 60
            except (IndexError, ValueError):
                iex_value = 100

            quantum      = int(bid.get("quantum", 0))
            quantity_iex = (quantum * -1 * iex_value) / 100
            num_bids     = int(bid.get("number_of_bids", 1))

            for _ in range(num_bids):
                rows.append({
                    portfolio_id:   bid.get("from", ""),
                    user_id_iex:    bid.get("to", ""),
                    participant_id: bid.get("bid_price", 0),
                    "INDIA-RTM":    quantity_iex,
                })

        df = pd.DataFrame(rows)
        buf = BytesIO()
        df.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)

        filename = f"IEX_BlockBid_{biding_date}.xlsx"
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


    # ── Withdrawal / Cancellation ─────────────────────────────────────────────

    async def mark_bid_for_withdrawal(self, bid_id: str, remark: str, cancelled_by: str) -> Dict[str, Any]:
        """
        Step 1 of cancellation: mark bid as Cancelled locally (no exchange call).

        Updates block_bid_current_data:
          - status -> "Cancelled"
          - withdrawal_remark, withdrawal_time, cancelled_by

        Args:
            bid_id: MongoDB ObjectId string of the block_bid_current_data document
            remark: Withdrawal reason provided by user
            cancelled_by: Username performing the action
        """
        update_data = {
            "status": "Cancelled",
            "withdrawal_remark": remark,
            "withdrawal_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "cancelled_by": cancelled_by,
        }
        modified = await self.block_bid_repo.updatedata(bid_id, update_data)
        if modified == 0:
            raise HTTPException(status_code=404, detail=f"No bid found with id={bid_id}")
        logger.info(f"Bid {bid_id} marked for withdrawal by {cancelled_by}")
        return {"success": True, "message": "Bid marked as Cancelled", "bid_id": bid_id}

    async def cancel_block_bid_on_exchange(
        self,
        order_ids: List[int],
        biding_date: str,
        cancelled_by: str = "",
        correlation_id: str = "",
    ) -> Dict[str, Any]:
        """
        Step 2 of cancellation: submit cancellation request to IEX exchange for each order_id.

        Flow (IEX RTM API v2.0):
          1. Load exchange config
          2. For each order_id fetch last_updated_time from dicision_rtm_block_bid_iex_response
          3. PUT to /rtm/api/v2/blockbid/cancel with {OrderId, UserId, ParticipantId, PortfolioId, LastUpdatedTime}
          4. On success (Status=="Success"): update dicision_rtm_block_bid_iex_response status -> "Cancelled"
          5. Return per-order results

        Args:
            order_ids: List of exchange order IDs to cancel
            biding_date: YYYY-MM-DD bidding date (used for delivery_date logging)
            cancelled_by: Username performing the action
            correlation_id: Request correlation id
        """
        if not order_ids:
            raise HTTPException(status_code=400, detail="order_ids list is required")

        config = await self.exchange_config_repo.get_bidType_market_active_config("block", "RTM")
        if not config:
            raise HTTPException(status_code=503, detail="No active RTM block bid exchange configuration found")

        base_url       = config.get("baseUrl", "")
        user_id        = config.get("LoginUserId", "")
        participant_id = config.get("ParticipantId", "")
        portfolio_id   = config.get("PortfolioId", "")
        bearer_token   = config.get("BearerToken", "")

        # Resolve delivery_date unix timestamp from biding_date
        try:
            delivery_ts = int(datetime.strptime(biding_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc).timestamp())
        except Exception:
            delivery_ts = None

        api_client = ExchangeAPIClient(
            base_url=base_url,
            login_user_id=user_id,
            participant_id=participant_id,
            portfolio_id=portfolio_id,
            bearer_token=bearer_token,
            correlation_id=correlation_id,
            market_type="rtm",
            bid_type="block-bid",
        )

        results = []
        for order_id in order_ids:
            item = {"order_id": order_id, "success": False, "message": "", "exchange_response": None}
            try:
                # Get last_updated_time from stored IEX response
                iex_doc = await self.iex_response_repo.get_by_order_id(order_id)
                if not iex_doc:
                    item["message"] = f"No exchange response doc found for order_id={order_id}"
                    logger.warning(item["message"])
                    results.append(item)
                    continue

                last_updated_time = iex_doc.get("last_updated_time", 0)

                # Call exchange cancel API
                exchange_response = await api_client.cancel_block_bid(
                    order_id=order_id,
                    last_updated_time=last_updated_time,
                    delivery_date=delivery_ts,
                )
                item["exchange_response"] = exchange_response

                # IEX RTM API v2.0: success indicated by Status=="Success"
                if exchange_response.get("Status") == "Success":
                    cancel_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    await self.iex_response_repo.update_status_by_order_id(
                        order_id,
                        "Cancelled",
                        extra_fields={"cancelled_by": cancelled_by, "cancelled_time": cancel_time}
                    )
                    item["success"] = True
                    item["message"] = "Cancelled successfully on exchange"
                    logger.info(f"order_id={order_id} cancelled on exchange by {cancelled_by}")
                else:
                    item["message"] = exchange_response.get("StatusMessage", exchange_response.get("message", "Exchange returned error"))
                    logger.warning(f"Exchange cancellation failed for order_id={order_id}: {exchange_response}")

            except Exception as e:
                item["message"] = str(e)
                logger.error(f"Error cancelling order_id={order_id}: {e}")

            results.append(item)

        success_count = sum(1 for r in results if r["success"])
        return {
            "success": success_count > 0,
            "message": f"{success_count}/{len(order_ids)} bids cancelled on exchange",
            "results": results,
        }

    @staticmethod
    def _time_to_block(time_str: str) -> int:
        """Convert "HH:MM" period string to block number (1-96)."""
        try:
            parts = time_str.split(":")
            h, m = int(parts[0]), int(parts[1])
            if h == 24:
                return 96
            return (h * 60 + m) // 15 + 1
        except Exception:
            return 1

    async def _get_ocf_values_for_block(self, delivery_date_ts: int, block: int) -> Dict[str, Any]:
        """
        Fetch OCF Premium/Discount Price and Max OCF Qty from limit_values for a given block.
        Mirrors dam_transaction_service._get_ocf_values logic for block bid context.
        """
        dt = datetime.fromtimestamp(delivery_date_ts, tz=timezone.utc)
        scheduled_at_str = dt.strftime("%Y-%m-%d")

        premium_result = await self.limit_values_repo.get_all_data_with_filters(
            "limit_values",
            filters={
                "id": "ocfPremiumDiscountPrice",
                "block": block,
                "category": "Limits",
                "market": "DAM",
                "scheduled_at": {"$regex": f"^{scheduled_at_str}"},
            },
        )
        max_qty_result = await self.limit_values_repo.get_all_data_with_filters(
            "limit_values",
            filters={
                "id": "ocfMaxOCFQty",
                "block": block,
                "category": "Limits",
                "market": "DAM",
                "scheduled_at": {"$regex": f"^{scheduled_at_str}"},
            },
        )

        premium_value = (premium_result[0].get("values", 0) or 0) if premium_result else 0
        max_qty_value = (max_qty_result[0].get("values", 0) or 0) if max_qty_result else 0
        ocf_opted = "Y" if (premium_value != 0 or max_qty_value != 0) else "N"

        return {
            "OCFOpted": ocf_opted,
            "PremiumDiscountPrice": premium_value,
            "MaxOCFQty": max_qty_value,
        }

    async def submit_dam_to_exchange(self, biding_date: str, submitted_by: str = "", correlation_id: str = "") -> Dict[str, Any]:
        """
        Submit all Approved DAM block bids for biding_date to IEX exchange.

        Flow (IEX DAM API v2.2, Section 9.15):
          1. Load exchange config (block, DAM)
          2. Fetch AssetId via get_asset_details()
          3. Fetch DeliveryDate Unix timestamp via get_delivery_dates()
          4. Fetch approved DAM bids from block_bid_current_data
          5. For each bid: check if same (FromPeriodId, ToPeriodId) exists on exchange
             → If yes, cancel existing bid(s) first
          6. Build BidDetails[] (no Session field — DAM is full-day)
          7. POST to /dam/api/v2/blockbid
          8. Wait 4 seconds then GET bid details for per-bid order data
          9. Update block_bid_current_data status → Submitted
         10. Bulk insert into dicision_dam_block_bid_iex_response
        """
        # 1. Exchange config
        config = await self.exchange_config_repo.get_bidType_market_active_config("block", "DAM")
        if not config:
            raise HTTPException(status_code=503, detail="No active DAM block bid exchange configuration found")

        base_url       = config.get("baseUrl", "")
        user_id        = config.get("LoginUserId", "")
        participant_id = config.get("ParticipantId", "")
        portfolio_id   = config.get("PortfolioId", "")
        bearer_token   = config.get("BearerToken", "")
        bid_area_id    = config.get("BidAreaId", "S2")

        api_client = ExchangeAPIClient(
            base_url=base_url,
            login_user_id=user_id,
            participant_id=participant_id,
            portfolio_id=portfolio_id,
            bearer_token=bearer_token,
            correlation_id=correlation_id,
            market_type="dam",
            bid_type="block-bid",
        )

        # 2 & 3. Fetch AssetId and DeliveryDate in parallel
        try:
            asset_response, delivery_dates_resp = await asyncio.gather(
                api_client.get_asset_details(bid_type="block-bid"),
                api_client.get_delivery_dates(bid_type="block-bid"),
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Exchange API timed out fetching DAM asset/delivery info.")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Exchange API error: {e.response.text}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to reach DAM exchange API: {type(e).__name__}: {e}")

        asset_details_list = asset_response.get("AssetDetails", [])
        asset_id = asset_details_list[0].get("AssetId", "") if asset_details_list else ""
        biddedQtyDecimal = float(asset_details_list[0].get("OrderQtyDecimal", 10)) if asset_details_list else 10.0
        logger.info(f"DAM block bid: AssetId={asset_id}, QtyDecimal={biddedQtyDecimal}")

        # DAM exchange returns a single object {DeliveryDateId, DeliveryDate} (not wrapped in a list)
        delivery_date_ts = delivery_dates_resp.get("DeliveryDate")
        if not delivery_date_ts:
            raise HTTPException(status_code=404, detail="DeliveryDate not found from DAM exchange")
        logger.info(f"DAM block bid: DeliveryDate={delivery_date_ts}")

        # 4. Approved DAM bids
        bids = await self.block_bid_repo.get_approved_bids(biding_date, market_type="DAM")
        if not bids:
            raise HTTPException(status_code=404, detail=f"No approved DAM block bids found for {biding_date}")

        # 5. Cancel-then-resubmit: fetch existing bids on exchange, cancel any with matching (from, to)
        try:
            existing_resp = await api_client.get_dam_block_bids(delivery_date=delivery_date_ts)
            existing_bids = existing_resp.get("BlockBidDetails", [])
        except Exception as ex:
            logger.warning(f"Could not fetch existing DAM block bids (will skip cancel step): {ex}")
            existing_bids = []

        # Build set of (from, to) periods we are about to submit
        new_periods = set()
        for bid in bids:
            new_periods.add((bid.get("from", ""), bid.get("to", "")))

        # Cancel any existing exchange bids whose (FromPeriodId, ToPeriodId) overlaps
        cancelled_order_ids = set()
        for ex_bid in existing_bids:
            ex_from = ex_bid.get("FromPeriodId", "")
            ex_to   = ex_bid.get("ToPeriodId", "")
            ex_status = ex_bid.get("OrderStatus", "")
            # Only cancel active/submitted bids
            if ex_status in ("Cancelled", "Rejected", "Excluded"):
                continue
            if (ex_from, ex_to) in new_periods:
                ex_order_id = ex_bid.get("OrderId")
                ex_last_updated = ex_bid.get("LastUpdatedTime", 0)
                if ex_order_id and ex_order_id not in cancelled_order_ids:
                    try:
                        cancel_resp = await api_client.cancel_dam_block_bid(
                            order_id=int(ex_order_id),
                            last_updated_time=int(ex_last_updated),
                            delivery_date=delivery_date_ts,
                        )
                        if cancel_resp.get("Status") == "Success":
                            cancelled_order_ids.add(ex_order_id)
                            logger.info(f"Cancelled existing DAM block bid OrderId={ex_order_id} before resubmit")
                        else:
                            logger.warning(f"Could not cancel existing DAM block bid OrderId={ex_order_id}: {cancel_resp}")
                    except Exception as ce:
                        logger.warning(f"Error cancelling existing DAM block bid OrderId={ex_order_id}: {ce}")

        # 6. Build BidDetails[] — DAM block bid has no Session field
        now = datetime.now()
        day_str = now.strftime("%d")
        user_id_prefix = str(user_id)[:5].ljust(5)

        # Fetch DAM block bid reference count
        try:
            ref_count_resp = await api_client.get_dam_block_bid_ref_count()
            bid_ref_start = ref_count_resp.get("DAMBlockBidRefCount", 0)
        except Exception:
            bid_ref_start = 0

        bid_details: List[Dict[str, Any]] = []
        blck_count = 0

        for bid in bids:
            ex_split = bid.get("ex_split_string", "IEX-100_PXIL-0")
            try:
                iex_value = int(ex_split.split("_")[0].split("-")[1])
            except (IndexError, ValueError):
                iex_value = 100

            quantum  = int(bid.get("quantum", 0))
            bid_type = bid.get("type", "Sell")
            buy_sell = "S" if bid_type == "Sell" else "B"

            iex_quantity = (quantum * iex_value) / 100
            quantity_exchange = iex_quantity * biddedQtyDecimal * (-1 if buy_sell == "S" else 1)

            from_period_id = bid.get("from", "00:00")
            to_period_id   = bid.get("to", "00:00")

            # OCF values are looked up per start-block of the time range
            start_block = self._time_to_block(from_period_id)
            try:
                ocf = await self._get_ocf_values_for_block(delivery_date_ts, start_block)
            except Exception:
                ocf = {"OCFOpted": "N", "PremiumDiscountPrice": 0, "MaxOCFQty": 0}

            num_bids = int(bid.get("number_of_bids", 1))
            for _ in range(num_bids):
                blck_count += 1
                bid_ref_id = f"{user_id_prefix}{day_str}{bid_ref_start + blck_count}"
                detail: Dict[str, Any] = {
                    "FromPeriodId": from_period_id,
                    "ToPeriodId":   to_period_id,
                    "OCFOpted":     ocf["OCFOpted"],
                    "BuySell":      buy_sell,
                    "Price":        int(bid.get("bid_price", 0)),
                    "Quantity":     int(quantity_exchange),
                    "BidRef":       bid_ref_id,
                    "LinkedBidRef": "",
                    "LinkType":     "",
                }
                if ocf["OCFOpted"] == "Y":
                    detail["PremiumDiscountPrice"] = ocf["PremiumDiscountPrice"]
                    detail["MaxOCFQty"]            = ocf["MaxOCFQty"]
                bid_details.append(detail)

        request_body = {
            "DeliveryDate":  delivery_date_ts,
            "AssetId":       asset_id,
            "BidAreaId":     bid_area_id,
            "UserId":        user_id,
            "ParticipantId": participant_id,
            "PortfolioId":   portfolio_id,
            "InitiatedBy":   user_id,
            "BidDetails":    bid_details,
        }

        # 7. POST to exchange
        try:
            response_data = await api_client.submit_dam_block_bid(request_body)
        except Exception as e:
            logger.error(f"DAM exchange API error during block bid submission: {str(e)}")
            raise HTTPException(status_code=502, detail=f"Exchange API error: {str(e)}")

        transaction_id = response_data.get("TransactionId") if isinstance(response_data, dict) else None
        submit_status  = response_data.get("Status", "Unknown") if isinstance(response_data, dict) else "Unknown"

        if submit_status != "Success" or not transaction_id:
            logger.error(f"DAM block bid submission failed — full exchange response: {response_data}")
            raise HTTPException(status_code=502, detail={"exchange_response": response_data})

        logger.info(f"DAM block bid submitted: TransactionId={transaction_id}, waiting for bid details...")

        # 8. Wait 4 seconds then fetch per-bid order details
        await asyncio.sleep(4)

        try:
            bid_details_response = await api_client.get_bid_details(
                transaction_id=str(transaction_id),
                delivery_date=delivery_date_ts,
                bid_type="block-bid",
            )
        except Exception as e:
            logger.warning(f"get_bid_details failed after DAM submission (TransactionId={transaction_id}): {e}")
            bid_details_response = {}

        # 9. Mark approved DAM bids → Submitted
        await self.block_bid_repo.bulk_update_status(biding_date, "Approved", "Submitted", market_type="DAM")

        # 10. Map BlockBidDetails → store in iex_response
        dam_bid_details = bid_details_response.get("BlockBidDetails", []) if isinstance(bid_details_response, dict) else []
        response_docs: List[Dict[str, Any]] = []
        final_status = "Submitted"

        async def _update_dam_bid_detail(val):
            order_status = val.get("OrderStatus", "Submitted")
            from_time    = val.get("FromPeriodId", "")
            to_time      = val.get("ToPeriodId", "")
            order_id_val = val.get("OrderId", "")
            await self.block_bid_repo.update_one(
                {
                    "biding_date": biding_date,
                    "from":        from_time,
                    "to":          to_time,
                    "status":      "Submitted",
                    "market_type": "DAM",
                },
                {"$set": {
                    "exchangeStatus":          order_status,
                    "exchangeOrderId":         order_id_val,
                    "exchangeLastUpdatedTime": val.get("LastUpdatedTime", ""),
                    "orderId":                 order_id_val,
                    "updatedAt":               datetime.now(),
                }},
            )

        if dam_bid_details:
            final_status = dam_bid_details[-1].get("OrderStatus", "Submitted")
            await asyncio.gather(*[_update_dam_bid_detail(v) for v in dam_bid_details], return_exceptions=True)

        for val in dam_bid_details:
            from_time    = val.get("FromPeriodId", "")
            to_time      = val.get("ToPeriodId", "")
            response_docs.append({
                "from":              from_time,
                "to":                to_time,
                "time_block":        f"{from_time}-{to_time}",
                "quantum":           abs(float(val.get("Quantity", 0))) / biddedQtyDecimal,
                "bid_price":         float(val.get("Price", 0)),
                "order_id":          val.get("OrderId", ""),
                "ref_id":            val.get("BidRef", ""),
                "type":              "Sell" if val.get("BuySell") == "S" else "Buy",
                "status":            val.get("OrderStatus", "Submitted"),
                "last_updated_time": val.get("LastUpdatedTime", ""),
                "transaction_id":    transaction_id,
                "Biding_date":       biding_date,
                "inserted_date":     biding_date,
                "created_by":        submitted_by,
                "created_time":      now.strftime("%d-%m-%Y %H:%M:%S"),
                "market_type":       "DAM",
            })

        if response_docs:
            await self.dam_iex_response_repo.bulk_insert_response_docs(response_docs)

        logger.info(f"DAM block bid submission complete: TransactionId={transaction_id}, {len(response_docs)} bids, status={final_status}")
        return {
            "status": "success",
            "message": f"DAM Bid {final_status} in exchange!",
            "transaction_id": transaction_id,
            "bids_submitted": len(response_docs),
        }

    async def cancel_dam_block_bid_on_exchange(
        self,
        order_ids: List[int],
        biding_date: str,
        cancelled_by: str = "",
        correlation_id: str = "",
    ) -> Dict[str, Any]:
        """
        Cancel submitted DAM block bids on IEX exchange for each order_id.

        Flow:
          1. Load DAM block bid exchange config
          2. For each order_id fetch last_updated_time from dicision_dam_block_bid_iex_response
          3. PUT to /dam/api/v2/blockbid/cancel
          4. On success: update dicision_dam_block_bid_iex_response status → Cancelled
        """
        if not order_ids:
            raise HTTPException(status_code=400, detail="order_ids list is required")

        config = await self.exchange_config_repo.get_bidType_market_active_config("block", "DAM")
        if not config:
            raise HTTPException(status_code=503, detail="No active DAM block bid exchange configuration found")

        base_url       = config.get("baseUrl", "")
        user_id        = config.get("LoginUserId", "")
        participant_id = config.get("ParticipantId", "")
        portfolio_id   = config.get("PortfolioId", "")
        bearer_token   = config.get("BearerToken", "")

        try:
            from datetime import timezone
            delivery_ts = int(datetime.strptime(biding_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc).timestamp())
        except Exception:
            delivery_ts = None

        api_client = ExchangeAPIClient(
            base_url=base_url,
            login_user_id=user_id,
            participant_id=participant_id,
            portfolio_id=portfolio_id,
            bearer_token=bearer_token,
            correlation_id=correlation_id,
            market_type="dam",
            bid_type="block-bid",
        )

        results = []
        for order_id in order_ids:
            item = {"order_id": order_id, "success": False, "message": "", "exchange_response": None}
            try:
                iex_doc = await self.dam_iex_response_repo.get_by_order_id(order_id)
                if not iex_doc:
                    item["message"] = f"No DAM exchange response doc found for order_id={order_id}"
                    logger.warning(item["message"])
                    results.append(item)
                    continue

                last_updated_time = iex_doc.get("last_updated_time", 0)

                exchange_response = await api_client.cancel_dam_block_bid(
                    order_id=order_id,
                    last_updated_time=last_updated_time,
                    delivery_date=delivery_ts,
                )
                item["exchange_response"] = exchange_response

                if exchange_response.get("Status") == "Success":
                    cancel_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    await self.dam_iex_response_repo.update_status_by_order_id(
                        order_id,
                        "Cancelled",
                        extra_fields={"cancelled_by": cancelled_by, "cancelled_time": cancel_time}
                    )
                    item["success"] = True
                    item["message"] = "Cancelled successfully on exchange"
                    logger.info(f"DAM block bid order_id={order_id} cancelled on exchange by {cancelled_by}")
                else:
                    item["message"] = exchange_response.get("StatusMessage", exchange_response.get("message", "Exchange returned error"))
                    logger.warning(f"DAM exchange cancellation failed for order_id={order_id}: {exchange_response}")

            except Exception as e:
                item["message"] = str(e)
                logger.error(f"Error cancelling DAM order_id={order_id}: {e}")

            results.append(item)

        success_count = sum(1 for r in results if r["success"])
        return {
            "success": success_count > 0,
            "message": f"{success_count}/{len(order_ids)} DAM bids cancelled on exchange",
            "results": results,
        }

    # ── Bid Submission Detail ──────────────────────────────────────────────────

    async def get_bid_submission_details(self, date_str: str) -> List[Dict[str, Any]]:
        """
        Return RTM block bid submission log for a date.
        Source: iex_integration_apilog (bid_type="block-bid", method="submitBlockBid")
        Grouped by correlation_id — one entry per exchange call.
        """
        return await self.api_log_repo.get_block_bid_submission_details(date_str)

    # ── Bid-Book ───────────────────────────────────────────────────────────────

    async def get_bid_book(self, biding_date: str) -> List[Dict[str, Any]]:
        """
        Return block bids for a date from the scheduler-synced collection.
        Source: rtm_block_bid_book_status (kept live by BlockBidBookProcessor every 15 min).
        Falls back to dicision_rtm_block_bid_iex_response if the synced collection has no data yet.
        """
        results = await self.block_bid_book_repo.get_bid_book(biding_date)
        if not results:
            # Fallback: submission-time snapshot until scheduler has synced
            logger.info(f"Bid-book: no scheduler-synced data for {biding_date}, falling back to iex_response collection")
            try:
                docs = await self.iex_response_repo.collection.find(
                    {"inserted_date": biding_date}, sort=[("from", 1)]
                ).to_list(10000)
                for doc in docs:
                    if "_id" in doc:
                        doc["_id"] = str(doc["_id"])
                    results.append(doc)
            except Exception as e:
                logger.error(f"get_bid_book fallback failed: {e}")
        return results

    # ── Trade-Book ─────────────────────────────────────────────────────────────

    async def get_trade_book(self, schedule_date: str) -> Dict[str, Any]:
        """
        Return executed/provisional block bids for a delivery date.
        Source: rtm_block_bid_book_status (kept live by BlockBidBookProcessor every 15 min).
        Only Executed and Provisional orders are returned — these are the matched trades.
        """
        results = await self.block_bid_book_repo.get_trade_book(schedule_date)
        return {
            "success":       True,
            "schedule_date": schedule_date,
            "total_records": len(results),
            "data":          results,
        }


block_bid_service = BlockBidServices()