"""
Shared single-bid withdrawal flow for the day-ahead markets (DAM / GDAM / HPDAM).

Withdrawal cancels *specific* orders via `PUT {market}/api/v2/singlebid/cancel`
(per IEX DAM/GDAM 2.1 and HPDAM 2.0 spec section "Single Bid Cancellation"),
as opposed to `singlebid/cancelall`, which wipes the whole portfolio for a
delivery date.

DAM, GDAM and HPDAM share an identical route surface and cancellation request
body, so the three market services delegate here instead of cloning the flow.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..iex.masterApi import (
    ExchangeAPIClient,
    block_to_time_period,
    select_delivery_date_record,
)

logger = logging.getLogger(__name__)


def block_to_periods(block: int) -> Optional[Dict[str, str]]:
    """
    Map a 1-96 time block to its FromPeriodId/ToPeriodId, returning None for
    out-of-range blocks. Delegates to the canonical exchange helper so the
    period format cannot drift from what submission sends (block 96 = 23:45-24:00).
    """
    try:
        from_period, to_period = block_to_time_period(int(block))
    except (ValueError, TypeError):
        return None

    return {"from_period": from_period, "to_period": to_period, "block": int(block)}


async def withdraw_day_ahead_bids(
    market: str,
    exchange_config_repo,
    time_blocks: List[int],
    correlation_id: Optional[str] = None,
    schedule_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Withdraw (cancel) day-ahead single bids for specific time blocks.

    Args:
        market: "DAM" | "GDAM" | "HPDAM" - drives both the exchange config
            lookup and the market segment of the exchange URL.
        exchange_config_repo: repository used to resolve the active config.
        time_blocks: time blocks (1-96) to withdraw.
        correlation_id: request correlation id (generated when absent).
        schedule_date: YYYY-MM-DD; defaults to today.

    Returns:
        Summary dict with withdrawn/failed counts and per-order details.
    """
    market_upper = market.upper()

    if not correlation_id:
        correlation_id = str(uuid.uuid4())

    if not time_blocks:
        raise ValueError("time_blocks is required for withdrawal")

    logger.info(
        f"Starting {market_upper} bid withdrawal for blocks {time_blocks}, "
        f"correlation_id={correlation_id}"
    )

    # 1. Active exchange config for this market
    config = await exchange_config_repo.get_bidType_market_active_config("single", market_upper)
    if not config:
        raise ValueError(f"No active exchange configuration found for {market_upper}")

    api_client = ExchangeAPIClient(
        base_url=config.get("baseUrl", ""),
        login_user_id=config.get("LoginUserId", ""),
        participant_id=config.get("ParticipantId", ""),
        portfolio_id=config.get("PortfolioId", ""),
        bearer_token=config.get("BearerToken", ""),
        correlation_id=correlation_id,
        market_type=market_upper,
        bid_type="single-bid",
    )

    # 2. Resolve the delivery date to act on
    delivery_dates_resp = await api_client.get_delivery_dates(bid_type="single-bid")
    delivery_date_list = delivery_dates_resp.get("DeliveryDates", [])

    target_date = datetime.now().date()
    if schedule_date:
        try:
            target_date = datetime.strptime(schedule_date, "%Y-%m-%d").date()
        except (ValueError, AttributeError):
            logger.warning(
                f"Could not parse schedule_date '{schedule_date}', defaulting to today"
            )

    selected_date_record = select_delivery_date_record(delivery_date_list, target_date)
    if not selected_date_record:
        raise ValueError(f"No exchange delivery date matches {target_date}")

    delivery_date_timestamp = selected_date_record.get("DeliveryDate")
    delivery_date_str = datetime.fromtimestamp(
        delivery_date_timestamp, tz=timezone.utc
    ).strftime("%Y-%m-%d")
    bid_area_id = config.get("BidAreaId", "S2")

    logger.info(
        f"{market_upper} withdrawal using delivery date "
        f"{selected_date_record.get('DeliveryDateId')} ({delivery_date_str})"
    )

    # 3. Requested blocks -> (FromPeriodId, ToPeriodId) keys
    target_period_keys = set()
    for block in time_blocks:
        periods = block_to_periods(int(block))
        if periods:
            target_period_keys.add((periods["from_period"], periods["to_period"]))

    if not target_period_keys:
        raise ValueError("No valid time blocks supplied (expected 1-96)")

    # 4. Existing bids on the exchange for this delivery date
    existing_bids = await api_client.get_single_bid_dam(
        delivery_date=delivery_date_timestamp,
        bid_area_id=bid_area_id,
    )

    bid_details = existing_bids.get("SingleBidDetails", []) or []
    if existing_bids.get("TotalRecord", 0) == 0 and not bid_details:
        logger.info(f"No existing {market_upper} bids found to withdraw")
        return {
            "success": True,
            "market": market_upper,
            "message": "No existing bids found to withdraw",
            "withdrawn_count": 0,
            "failed_count": 0,
            "correlation_id": correlation_id,
        }

    # 5. Match the requested blocks, skipping anything already cancelled
    orders_to_cancel = []
    for bid in bid_details:
        key = (bid.get("FromPeriodId"), bid.get("ToPeriodId"))
        if key in target_period_keys and bid.get("OrderStatus") != "Cancelled":
            orders_to_cancel.append(
                {
                    "OrderId": bid.get("OrderId"),
                    "LastUpdatedTime": bid.get("LastUpdatedTime"),
                    "FromPeriodId": bid.get("FromPeriodId"),
                    "ToPeriodId": bid.get("ToPeriodId"),
                    "UserId": bid.get("UserId"),
                }
            )

    if not orders_to_cancel:
        logger.info(f"No matching {market_upper} bids for the specified time blocks")
        return {
            "success": True,
            "market": market_upper,
            "message": "No matching bids found for specified time blocks",
            "withdrawn_count": 0,
            "failed_count": 0,
            "correlation_id": correlation_id,
        }

    # 6. Cancel each matching order individually
    cancelled, failed = [], []
    for order in orders_to_cancel:
        try:
            result = await api_client.cancel_rtm_bid(
                order_id=order["OrderId"],
                user_id=order["UserId"],
                last_updated_time=order["LastUpdatedTime"],
                delivery_date=delivery_date_timestamp,
            )

            if result.get("Status") == "Success":
                cancelled.append(
                    {
                        "OrderId": order["OrderId"],
                        "FromPeriodId": order["FromPeriodId"],
                        "ToPeriodId": order["ToPeriodId"],
                        "TransactionId": result.get("TransactionId"),
                        "Status": "Success",
                    }
                )
            else:
                failed.append(
                    {
                        "OrderId": order["OrderId"],
                        "FromPeriodId": order["FromPeriodId"],
                        "ToPeriodId": order["ToPeriodId"],
                        "Status": result.get("Status", "Failed"),
                        "StatusMessage": result.get("StatusMessage"),
                    }
                )
        except Exception as e:
            logger.error(
                f"{market_upper} withdrawal failed for OrderId={order['OrderId']}: {e}"
            )
            failed.append(
                {
                    "OrderId": order["OrderId"],
                    "FromPeriodId": order["FromPeriodId"],
                    "ToPeriodId": order["ToPeriodId"],
                    "Status": "Failed",
                    "StatusMessage": str(e),
                }
            )

    logger.info(
        f"{market_upper} withdrawal complete: {len(cancelled)} cancelled, {len(failed)} failed"
    )

    return {
        "success": len(failed) == 0,
        "market": market_upper,
        "message": f"Withdrew {len(cancelled)} of {len(orders_to_cancel)} bid(s)",
        "withdrawn_count": len(cancelled),
        "failed_count": len(failed),
        "delivery_date": delivery_date_str,
        "cancelled": cancelled,
        "failed": failed,
        "correlation_id": correlation_id,
    }
