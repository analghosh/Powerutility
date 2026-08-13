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
from ..iex.masterApi import ExchangeAPIClient, select_delivery_date_record
from libs.notifications import notify
from libs.notifications.events import (
    approval_permission_market,
    approval_screen_path,
    market_display_name,
    submission_screen_path,
)

logger = logging.getLogger(__name__)

# Statuses that represent an actual maker-checker DECISION, and so are worth
# announcing. Compared lower-cased: the collection carries both "Pending" and
# "pending".
#
# The approval screen submits every row it is displaying, not just the rows the
# checker changed -- untouched rows arrive as "Pending". Those are not decisions,
# and notifying on them produced a second, nonsensical notification next to the
# real one ("RTM block bid pending / <user> pending 2 RTM block bid(s)").
DECISION_STATUSES = {"approved", "rejected"}

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
            # DAM only: persist ocf / ocf_config from parallel request arrays
            if market_type == "DAM":
                ocf_list = request.get("ocf") or []
                ocf_config_list = request.get("ocf_config") or []
                ocf_config_val = ocf_config_list[i] if i < len(ocf_config_list) else None
                ocf_val = ocf_list[i] if i < len(ocf_list) else None
                chosen = ocf_config_val if ocf_config_val not in (None, "") else ocf_val
                ocf_stored = str(chosen) if chosen not in (None, "") else ""
                record["ocf"] = ocf_stored
                record["ocf_config"] = ocf_stored
            data.append(record)
            time_block_range.append(time_block)

        # Insert into MongoDB
        try:
            insert_result = await self.block_bid_repo.insert_many(data)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        # Notify every user who can see this market's approval screen.
        # Action-level: one notification per distinct market, each carrying
        # that market's own bid count -- never one per bid, and never one
        # notification labelling a mixed batch with a single market's name.
        #
        # SubmitForApprovalRequest.market_type is a List[str] parallel to the
        # bid arrays, so a submission can span more than one market. This used
        # to read `request.get("market_type", ["RTM"])[0]` -- the first
        # element standing in for the whole batch, plus a hardcoded RTM
        # default -- which is the exact first-element/hardcoded-market pattern
        # already fixed once on the status-change path below. Grouping is done
        # over `data` (the records actually inserted) rather than the raw
        # request array, so the counts can never disagree with what was
        # persisted. When no market can be determined, nothing is emitted and
        # the fact is logged, exactly as _notify_status_change does: a missing
        # notification is recoverable, one sent to the wrong market's
        # approvers is not.
        counts_by_market: Dict[str, int] = {}
        for record in data:
            record_market = str(record.get("market_type") or "").strip().upper()
            if not record_market:
                continue
            counts_by_market[record_market] = counts_by_market.get(record_market, 0) + 1

        if not counts_by_market:
            logger.info(
                "No market found for the submitted bids; skipping "
                "submitted-for-approval notification"
            )

        submit_actor = {
            "user_id": request.get("user_id"),
            "username": request.get("current_user") or request.get("username"),
        }
        for market, market_bid_count in counts_by_market.items():
            # Guarded per market, matching _notify_status_change: one market's
            # failure (including an unknown market raising out of
            # approval_screen_path) must not stop another market's
            # notification, and none of them may fail the bids already
            # inserted above.
            try:
                await notify(
                    "block_bid.submitted_for_approval",
                    {
                        "market_type": market,
                        "market_name": market_display_name(market),
                        "approval_market": approval_permission_market(market),
                        "approval_path": approval_screen_path(market),
                        "bid_count": market_bid_count,
                        "schedule_date": formatted_date,
                    },
                    actor=submit_actor,
                )
            except Exception:
                logger.exception(
                    "Notification emit failed for block bid submission (market=%s)",
                    market,
                )

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

            updated = []
            skipped = []

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

                # Only update bids currently in pending status (approve and reject).
                matched = await self.block_bid_repo.updatedata_if_pending(ids[i], data)
                if matched:
                    updated.append({
                        "id": ids[i],
                        "status": statuses[i],
                    })
                else:
                    logger.info(
                        "Bid %s skipped: cannot update a bid that is not in pending status",
                        ids[i],
                    )
                    skipped.append({
                        "id": ids[i],
                        "status": statuses[i],
                        "reason": "Bid is no longer pending (already processed by another approver)",
                    })

            # After the loop: one notification per distinct market spanned by the
            # updated bids (never per bid). See _notify_status_change for why market
            # and schedule_date come from the bids, not the request.
            if updated:
                await self._notify_status_change(updated, request)

            total = len(updated) + len(skipped)
            updated_count = len(updated)
            skipped_count = len(skipped)

            if updated_count == total and total > 0:
                message = "All bids updated successfully"
            elif updated_count == 0 and skipped_count > 0:
                message = "No bids updated — all were already processed"
            elif updated_count > 0 and skipped_count > 0:
                message = f"Partial update: {updated_count} updated, {skipped_count} skipped"
            else:
                message = "Bid Approved Successfully"

            return {
                "message": message,
                "summary": {
                    "total": total,
                    "updated_count": updated_count,
                    "skipped_count": skipped_count,
                },
                "updated": updated,
                "skipped": skipped,
            }

        except Exception as e:
            logger.error(f"Failed to get pending approvals in : {str(e)}")
            raise

    async def _notify_status_change(self, updated: List[Dict[str, Any]], request: Dict[str, Any]) -> None:
        """Emit one block_bid.status_changed notification per distinct
        (market, status) pair spanned by the just-updated bids.

        market_type and schedule_date must come from the bid documents, not the
        request: UpdateStatusRequest (block_bid_routes.py) carries neither field, and
        the RTM/DAM approval screen that submits it never sends either -- so falling
        back to request.get("market_type") mislabelled every DAM/GDAM/HPDAM decision
        as RTM, notifying the wrong approvers entirely, and schedule_date always
        rendered empty. get_bid_decision_context reads both from the bids themselves,
        which unlike the request cannot be absent.

        A batch can span more than one market (the API accepts arbitrary ids even
        though the UI only ever submits one market at a time), so this emits once per
        distinct market found, each carrying only that market's bid count and makers
        -- never a single notification mislabelling a mixed batch. If no market is
        found at all, no notification is sent rather than guessing a default: a
        missing notification is recoverable, a wrong one sent to the wrong approvers
        is not.

        The same is true of the STATUS. UpdateStatusRequest.status is a
        List[Optional[str]], so one batch can approve some bids and reject others.
        This used to read `updated[0]["status"]` -- the first decided bid's status
        applied to every notification -- which told the makers and every checker that
        all N bids were whatever the first one happened to be, including in a maker's
        notification about their own bid. The ids are therefore grouped by status
        first and the per-market lookup is run once per status group, so each
        notification is truthful about both market and status.

        Nothing here may fail the bid decision already persisted by the caller: each
        status group's lookup failure is caught and skips only that group (so one
        Mongo hiccup can't lose the other status's notifications), and each
        per-market notify() failure is caught independently so one market's failure
        can't stop another market's notification from going out.
        """
        ids_by_status: Dict[str, List[str]] = {}
        for entry in updated:
            status_label = str(entry.get("status") or "").strip()
            if not status_label:
                continue
            # Drop non-decisions rather than coercing them into one. The vendor
            # line this replaced (`"Approved" if statuses[i] == "Approved" else
            # "Rejected"`) coerced, which relabelled an untouched Pending row as a
            # rejection -- a worse lie than the duplicate notification it hid.
            if status_label.lower() not in DECISION_STATUSES:
                continue
            ids_by_status.setdefault(status_label, []).append(entry["id"])

        if not ids_by_status:
            logger.info(
                "No status found for the updated bids; skipping status-change notification"
            )
            return

        actor = {
            "user_id": request.get("user_id"),
            "username": request.get("current_user") or request.get("username"),
        }

        for status_label, status_ids in ids_by_status.items():
            # get_bid_decision_context groups by market, so running it once per
            # status group yields (market, status) groups overall -- each with only
            # that pair's own bid count and makers.
            try:
                market_groups = await self.block_bid_repo.get_bid_decision_context(
                    status_ids
                )
            except Exception:
                logger.exception(
                    "Failed to look up bid decision context for notification (status=%s)",
                    status_label,
                )
                continue

            if not market_groups:
                logger.info(
                    "No market found for the updated %s bids; skipping status-change "
                    "notification",
                    status_label,
                )
                continue

            for group in market_groups:
                market = group["market_type"]
                try:
                    await notify(
                        "block_bid.status_changed",
                        {
                            "market_type": market,
                            "market_name": market_display_name(market),
                            "approval_market": approval_permission_market(market),
                            "submission_path": submission_screen_path(market),
                            "status_lower": status_label.lower(),
                            "maker_usernames": group["maker_usernames"],
                            "bid_count": group["bid_count"],
                            "schedule_date": group["biding_date"],
                        },
                        actor=actor,
                    )
                except Exception:
                    logger.exception(
                        "Notification emit failed for block bid status change "
                        "(market=%s status=%s)",
                        market,
                        status_label,
                    )

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

        today = datetime.now().date()
        try:
            biding_date_obj = datetime.strptime(biding_date, "%Y-%m-%d").date()
        except ValueError:
            biding_date_obj = today

        selected_date = select_delivery_date_record(delivery_date_list, biding_date_obj)
        if not selected_date:
            raise HTTPException(status_code=404, detail=f"No exchange delivery date matches bid date {biding_date_obj}")

        delivery_date_ts = selected_date.get("DeliveryDate")
        logger.info(f"DeliveryDate fetched from exchange: {selected_date.get('DeliveryDateId')} → {delivery_date_ts}")

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

            rtm_update_fields: Dict[str, Any] = {
                "status":                  "Submitted",
                "exchangeStatus":          order_status,
                "exchangeOrderId":         order_id,
                "exchangeLastUpdatedTime": last_val.get("LastUpdatedTime", "") if rtm_bid_details else "",
                "orderId":                 order_id,
                "updatedAt":               updated_at,
            }
            # Only mirror the rejection — don't touch status for any other exchange state.
            if order_status == "Rejected":
                rtm_update_fields["status"] = "exchange_rejected"

            await self.block_bid_repo.updatedata(bid_id, rtm_update_fields)

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
                # Get stored IEX response doc (has transaction_id + stale last_updated_time)
                iex_doc = await self.iex_response_repo.get_by_order_id(order_id)
                if not iex_doc:
                    item["message"] = f"No exchange response doc found for order_id={order_id}"
                    logger.warning(item["message"])
                    results.append(item)
                    continue

                # Fetch fresh LastUpdatedTime from exchange — the stored value is stale.
                # IEX rejects the cancel if LastUpdatedTime doesn't match its current book value.
                last_updated_time = iex_doc.get("last_updated_time", 0)  # fallback
                transaction_id = iex_doc.get("transaction_id")
                if transaction_id and delivery_ts:
                    try:
                        fresh = await api_client.get_bid_details(transaction_id, delivery_ts, bid_type="block-bid")
                        for detail in fresh.get("RTMBlockBidDetails", []):
                            if int(detail.get("OrderId", 0)) == int(order_id):
                                last_updated_time = detail.get("LastUpdatedTime", last_updated_time)
                                logger.info(f"Refreshed LastUpdatedTime for order_id={order_id}: {last_updated_time}")
                                break
                    except Exception as fetch_err:
                        logger.warning(f"Could not refresh LastUpdatedTime for order_id={order_id}, using stored value: {fetch_err}")

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

                    # 1. Update submission snapshot (dicision_rtm_block_bid_iex_response)
                    await self.iex_response_repo.update_status_by_order_id(
                        order_id,
                        "Cancelled",
                        extra_fields={"cancelled_by": cancelled_by, "cancelled_time": cancel_time}
                    )

                    # 2. Immediately update block_bid_current_data so bid-listing
                    #    endpoints reflect the cancellation without waiting for the
                    #    next scheduler sync.
                    await self.block_bid_repo.update_one(
                        {"exchangeOrderId": order_id},
                        {"$set": {
                            "status": "Cancelled",
                            "exchangeStatus": "Cancelled",
                            "cancelled_by": cancelled_by,
                            "cancelled_time": cancel_time,
                        }}
                    )

                    # 3. Immediately update rtm_block_bid_book_status so the
                    #    bid-book endpoint shows the new status right away (the
                    #    scheduler refreshes every 15 min — too slow for UX).
                    await self.block_bid_book_repo.update_order_status_by_order_id(
                        order_id,
                        "Cancelled",
                        extra_fields={"cancelled_by": cancelled_by, "cancelled_time": cancel_time},
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
            set_fields: Dict[str, Any] = {
                "exchangeStatus":          order_status,
                "exchangeOrderId":         order_id_val,
                "exchangeLastUpdatedTime": val.get("LastUpdatedTime", ""),
                "orderId":                 order_id_val,
                "updatedAt":               datetime.now(),
            }
            # Only mirror the rejection — don't touch status for any other exchange state.
            if order_status == "Rejected":
                set_fields["status"] = "exchange_rejected"
            await self.block_bid_repo.update_one(
                {
                    "biding_date": biding_date,
                    "from":        from_time,
                    "to":          to_time,
                    "status":      "Submitted",
                    "market_type": "DAM",
                },
                {"$set": set_fields},
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

    async def get_bid_submission_details(
        self, date_str: str, market_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Return block bid submission log for a date (RTM and DAM/GDAM).
        Source: iex_integration_apilog (bid_type="block-bid").
        One entry per exchange call.
        """
        return await self.api_log_repo.get_block_bid_submission_details(date_str, market_type)

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