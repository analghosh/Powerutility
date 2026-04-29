"""
RTM Bid Lapse Processor
Marks RTM bids as lapsed when their session passes the active trading window.

Active session = current session + 3 (wraps across dates)
Sessions: 1-48 (each 30min, 00:00-00:30 = session 1, 23:30-24:00 = session 48)

Statuses lapsed:
  approved -> approved_lapse
  approved_withdrawal -> approved_withdrawal_lapse
  pending -> pending_lapse
  pending_withdrawal -> pending_withdrawal_lapse
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from services.scheduler.app.repository.rtm_single_bid_approval_repository import RTMSingleBidDataForApprovalRepository
from services.scheduler.app.repository.block_bid_current_data_repository import BlockBidCurrentDataRepository
from services.scheduler.app.repository.dicision_rtm_block_bid_iex_response_repository import DicisionRtmBlockBidIexResponseRepository

logger = logging.getLogger(__name__)

# Statuses that should be lapsed
LAPSABLE_STATUSES = ["approved", "approved_withdrawal", "pending", "pending_withdrawal"]

# Block bid uses capitalized status values (different from single bid)
BLOCK_BID_LAPSABLE_STATUSES = ["Approved", "Pending", "pending"]

SESSIONS_PER_DAY = 48
ACTIVE_SESSION_OFFSET = 3


def get_current_session_ist() -> int:
    """
    Get the current session number (1-48) based on IST time.
    
    Session mapping:
      00:00-00:30 = session 1
      00:30-01:00 = session 2
      ...
      23:30-24:00 = session 48
    """
    try:
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
    except ImportError:
        now_ist = datetime.now()
    
    total_minutes = now_ist.hour * 60 + now_ist.minute
    session = (total_minutes // 30) + 1
    return min(session, SESSIONS_PER_DAY)


def get_today_date_str_ist() -> str:
    """Get today's date string in YYYY-MM-DD format (IST)."""
    try:
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        return datetime.now(ist).strftime("%Y-%m-%d")
    except ImportError:
        return datetime.now().strftime("%Y-%m-%d")


def get_tomorrow_date_str_ist() -> str:
    """Get tomorrow's date string in YYYY-MM-DD format (IST)."""
    try:
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        return (datetime.now(ist) + timedelta(days=1)).strftime("%Y-%m-%d")
    except ImportError:
        return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")


def _session_to_time_str(session: int) -> str:
    """
    Convert a session number (1-48) to its start time string "HH:MM".
    Session 1 → "00:00", Session 2 → "00:30", ..., Session 48 → "23:30"
    """
    total_minutes = (session - 1) * 30
    h = total_minutes // 60
    m = total_minutes % 60
    return f"{h:02d}:{m:02d}"


class RTMBidLapseProcessor:
    """
    Processor to mark RTM bids as lapsed when their session_block
    has passed the active trading window.
    """

    def __init__(self):
        self.rtm_bid_repo = RTMSingleBidDataForApprovalRepository()
        self.block_bid_repo = BlockBidCurrentDataRepository()
        self.decision_block_bid_repo = DicisionRtmBlockBidIexResponseRepository()
        logger.info("RTMBidLapseProcessor initialized")

    async def execute_cron_job(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Main cron job entry point. Runs every 15 minutes.
        """
        try:
            current_session = get_current_session_ist()
            active_session = current_session + ACTIVE_SESSION_OFFSET
            today = get_today_date_str_ist()
            tomorrow = get_tomorrow_date_str_ist()

            logger.info(
                "[RTMBidLapse] Starting — current_session=%d, active_session=%d, today=%s",
                current_session, active_session, today
            )

            # DIAGNOSTIC: Check what's in the collection for lapsable statuses
            try:
                diagnostic_filter = {"bid_status": {"$in": LAPSABLE_STATUSES}}
                diagnostic_docs = await self.rtm_bid_repo.find_by_filters(
                    diagnostic_filter,
                    {"bid_date": 1, "session_block": 1, "bid_status": 1, "_id": 0}
                )
                logger.info(
                    "[RTMBidLapse] DIAGNOSTIC: Found %d total docs with lapsable statuses",
                    len(diagnostic_docs)
                )
                if diagnostic_docs:
                    # Log first 5 sample docs for debugging
                    for doc in diagnostic_docs[:5]:
                        logger.info(
                            "[RTMBidLapse] DIAGNOSTIC sample: bid_date=%s, session_block=%s, bid_status=%s",
                            doc.get("bid_date"), doc.get("session_block"), doc.get("bid_status")
                        )
            except Exception as diag_err:
                logger.error("[RTMBidLapse] DIAGNOSTIC query failed: %s", str(diag_err))

            total_updated = 0
            block_bid_total_updated = 0

            # Step 1: Lapse ALL bids from past dates (bid_date < today)
            count_past = await self._lapse_past_dates(today)
            total_updated += count_past
            logger.info("[RTMBidLapse] Past dates lapsed: %d bids", count_past)

            # Step 2: Lapse today's bids where session has passed
            if active_session <= SESSIONS_PER_DAY:
                count_today = await self._lapse_bids(
                    bid_date=today,
                    session_block_lt=active_session
                )
                total_updated += count_today
                logger.info("[RTMBidLapse] Today (%s) lapsed: %d bids (session_block < %d)", today, count_today, active_session)
            else:
                # Wraps to tomorrow
                count_today = await self._lapse_bids(
                    bid_date=today,
                    session_block_lt=SESSIONS_PER_DAY + 1
                )
                total_updated += count_today
                logger.info("[RTMBidLapse] Today (%s) lapsed ALL: %d bids", today, count_today)

                overflow_session = active_session - SESSIONS_PER_DAY
                count_tomorrow = await self._lapse_bids(
                    bid_date=tomorrow,
                    session_block_lt=overflow_session
                )
                total_updated += count_tomorrow
                logger.info("[RTMBidLapse] Tomorrow (%s) lapsed: %d bids (session_block < %d)", tomorrow, count_tomorrow, overflow_session)

            logger.info("[RTMBidLapse] Completed — total bids lapsed: %d", total_updated)

            # Step 3: Also lapse block_bid_current_data (same session/date logic)
            block_bid_count_past = await self._lapse_block_bids_past_dates(today)
            block_bid_total_updated += block_bid_count_past
            logger.info("[RTMBidLapse] block_bid_current_data past dates lapsed: %d bids", block_bid_count_past)

            if active_session <= SESSIONS_PER_DAY:
                block_bid_count_today = await self._lapse_block_bids(
                    biding_date=today,
                    trade_session_lt=active_session
                )
                block_bid_total_updated += block_bid_count_today
                logger.info(
                    "[RTMBidLapse] block_bid_current_data today (%s) lapsed: %d bids (trade_session < %d)",
                    today, block_bid_count_today, active_session
                )
            else:
                block_bid_count_today = await self._lapse_block_bids(
                    biding_date=today,
                    trade_session_lt=SESSIONS_PER_DAY + 1
                )
                block_bid_total_updated += block_bid_count_today
                logger.info("[RTMBidLapse] block_bid_current_data today (%s) lapsed ALL: %d bids", today, block_bid_count_today)

                overflow_session = active_session - SESSIONS_PER_DAY
                block_bid_count_tomorrow = await self._lapse_block_bids(
                    biding_date=tomorrow,
                    trade_session_lt=overflow_session
                )
                block_bid_total_updated += block_bid_count_tomorrow
                logger.info(
                    "[RTMBidLapse] block_bid_current_data tomorrow (%s) lapsed: %d bids (trade_session < %d)",
                    tomorrow, block_bid_count_tomorrow, overflow_session
                )

            logger.info("[RTMBidLapse] block_bid_current_data completed — total lapsed: %d", block_bid_total_updated)

            # Step 4: Also lapse dicision_rtm_block_bid_iex_response (same single bid flow)
            decision_block_bid_total_updated = 0

            decision_count_past = await self._lapse_decision_block_bids_past_dates(today)
            decision_block_bid_total_updated += decision_count_past
            logger.info("[RTMBidLapse] dicision_rtm_block_bid_iex_response past dates lapsed: %d", decision_count_past)

            active_session_time_str = _session_to_time_str(active_session)
            if active_session <= SESSIONS_PER_DAY:
                decision_count_today = await self._lapse_decision_block_bids(
                    biding_date=today,
                    from_time_lt=active_session_time_str
                )
                decision_block_bid_total_updated += decision_count_today
                logger.info(
                    "[RTMBidLapse] dicision_rtm_block_bid_iex_response today (%s) lapsed: %d (from < %s)",
                    today, decision_count_today, active_session_time_str
                )
            else:
                decision_count_today = await self._lapse_decision_block_bids(
                    biding_date=today,
                    from_time_lt="24:00"
                )
                decision_block_bid_total_updated += decision_count_today
                logger.info("[RTMBidLapse] dicision_rtm_block_bid_iex_response today (%s) lapsed ALL: %d", today, decision_count_today)

                overflow_session = active_session - SESSIONS_PER_DAY
                overflow_time_str = _session_to_time_str(overflow_session)
                decision_count_tomorrow = await self._lapse_decision_block_bids(
                    biding_date=tomorrow,
                    from_time_lt=overflow_time_str
                )
                decision_block_bid_total_updated += decision_count_tomorrow
                logger.info(
                    "[RTMBidLapse] dicision_rtm_block_bid_iex_response tomorrow (%s) lapsed: %d (from < %s)",
                    tomorrow, decision_count_tomorrow, overflow_time_str
                )

            logger.info("[RTMBidLapse] dicision_rtm_block_bid_iex_response completed — total lapsed: %d", decision_block_bid_total_updated)

            return {
                "success": True,
                "current_session": current_session,
                "active_session": active_session,
                "today": today,
                "total_updated": total_updated,
                "block_bid_total_updated": block_bid_total_updated,
                "decision_block_bid_total_updated": decision_block_bid_total_updated
            }

        except Exception as e:
            logger.exception("[RTMBidLapse] Error during lapse processing: %s", str(e))
            return {"success": False, "error": str(e)}

    async def _lapse_past_dates(self, today: str) -> int:
        """
        Lapse ALL bids from dates before today, regardless of session_block.
        """
        total_modified = 0

        for status in LAPSABLE_STATUSES:
            lapsed_status = f"{status}_lapse"

            filter_query = {
                "bid_date": {"$lt": today},
                "bid_status": status
            }

            update_query = {
                "$set": {"bid_status": lapsed_status}
            }

            try:
                # DIAGNOSTIC: count before update
                count_docs = await self.rtm_bid_repo.find_by_filters(filter_query, {"_id": 1})
                logger.info(
                    "[RTMBidLapse] Past dates '%s': %d matching docs found before update",
                    status, len(count_docs)
                )

                result = await self.rtm_bid_repo.update_many(filter_query, update_query)
                logger.info(
                    "[RTMBidLapse] Past dates '%s' update_many result: %s",
                    status, str(result)
                )
                modified = result.get("modified_count", 0)
                if modified > 0:
                    logger.info(
                        "[RTMBidLapse] Past dates: %s -> %s: %d bids",
                        status, lapsed_status, modified
                    )
                total_modified += modified

            except Exception as e:
                logger.error(
                    "[RTMBidLapse] Failed to lapse past '%s' bids: %s",
                    status, str(e)
                )

        return total_modified

    async def _lapse_bids(self, bid_date: str, session_block_lt: int) -> int:
        """
        Find and update bids that should be lapsed for a specific date.
        """
        total_modified = 0

        for status in LAPSABLE_STATUSES:
            lapsed_status = f"{status}_lapse"

            filter_query = {
                "bid_date": bid_date,
                "session_block": {"$lt": session_block_lt},
                "bid_status": status
            }

            update_query = {
                "$set": {"bid_status": lapsed_status}
            }

            try:
                # DIAGNOSTIC: count before update
                count_docs = await self.rtm_bid_repo.find_by_filters(filter_query, {"_id": 1})
                logger.info(
                    "[RTMBidLapse] bid_date=%s, session_block < %d, status='%s': %d matching docs",
                    bid_date, session_block_lt, status, len(count_docs)
                )

                result = await self.rtm_bid_repo.update_many(filter_query, update_query)
                logger.info(
                    "[RTMBidLapse] update_many result for '%s': %s",
                    status, str(result)
                )
                modified = result.get("modified_count", 0)
                total_modified += modified

            except Exception as e:
                logger.error(
                    "[RTMBidLapse] Failed to lapse '%s' bids for date=%s: %s",
                    status, bid_date, str(e)
                )

        return total_modified


    async def execute_block_bid_lapse_job(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Cron entry point for block bid lapse processing. Runs every 15 minutes.

        Block bids use trade_session (1-48, 30-min sessions) and biding_date (YYYY-MM-DD).
        Statuses: "Approved" -> "Approved_lapse", "pending" -> "pending_lapse"
        Submitted bids are NOT lapsed — exchange already has them.
        """
        try:
            current_session = get_current_session_ist()
            active_session  = current_session + ACTIVE_SESSION_OFFSET
            today    = get_today_date_str_ist()
            tomorrow = get_tomorrow_date_str_ist()

            logger.info(
                "[BlockBidLapse] Starting — current_session=%d, active_session=%d, today=%s",
                current_session, active_session, today
            )

            total_updated = 0

            # Lapse ALL block bids from past dates
            count_past = await self._lapse_block_bids_past_dates(today)
            total_updated += count_past
            logger.info("[BlockBidLapse] Past dates lapsed: %d bids", count_past)

            # Lapse today's block bids where trade_session has passed
            if active_session <= SESSIONS_PER_DAY:
                count_today = await self._lapse_block_bids(
                    biding_date=today,
                    trade_session_lt=active_session
                )
                total_updated += count_today
                logger.info(
                    "[BlockBidLapse] Today (%s) lapsed: %d bids (trade_session < %d)",
                    today, count_today, active_session
                )
            else:
                count_today = await self._lapse_block_bids(
                    biding_date=today,
                    trade_session_lt=SESSIONS_PER_DAY + 1
                )
                total_updated += count_today
                logger.info("[BlockBidLapse] Today (%s) lapsed ALL: %d bids", today, count_today)

                overflow_session = active_session - SESSIONS_PER_DAY
                count_tomorrow = await self._lapse_block_bids(
                    biding_date=tomorrow,
                    trade_session_lt=overflow_session
                )
                total_updated += count_tomorrow
                logger.info(
                    "[BlockBidLapse] Tomorrow (%s) lapsed: %d bids (trade_session < %d)",
                    tomorrow, count_tomorrow, overflow_session
                )

            logger.info("[BlockBidLapse] Completed — total block bids lapsed: %d", total_updated)

            return {
                "success": True,
                "current_session": current_session,
                "active_session": active_session,
                "today": today,
                "total_updated": total_updated
            }

        except Exception as e:
            logger.exception("[BlockBidLapse] Error during lapse processing: %s", str(e))
            return {"success": False, "error": str(e)}

    async def _lapse_block_bids_past_dates(self, today: str) -> int:
        """Lapse all block bids from dates before today.
        Only lapses the latest revision per (biding_date, time_block).
        Skips time_blocks where the latest revision is already Submitted.
        """
        total_modified = 0
        try:
            # Find _ids of latest revision per (biding_date, time_block) where status is lapsable
            pipeline = [
                {"$match": {
                    "biding_date": {"$lt": today},
                    "status": {"$in": BLOCK_BID_LAPSABLE_STATUSES}
                }},
                {"$sort": {"revision_no": -1}},
                {"$group": {
                    "_id": {"biding_date": "$biding_date", "time_block": "$time_block"},
                    "doc_id": {"$first": "$_id"},
                    "status": {"$first": "$status"}
                }},
                {"$match": {"status": {"$in": BLOCK_BID_LAPSABLE_STATUSES}}}
            ]
            latest_docs = await self.block_bid_repo.collection.aggregate(pipeline).to_list(length=10000)
            for doc in latest_docs:
                status = doc["status"]
                lapsed_status = f"{status}_lapse"
                result = await self.block_bid_repo.collection.update_one(
                    {"_id": doc["doc_id"]},
                    {"$set": {"status": lapsed_status}}
                )
                if result.modified_count > 0:
                    total_modified += 1
                    logger.info(
                        "[BlockBidLapse] Past dates: %s -> %s for time_block=%s",
                        status, lapsed_status, doc["_id"].get("time_block")
                    )
        except Exception as e:
            logger.error("[BlockBidLapse] Failed to lapse past block bids: %s", str(e))
        return total_modified

    async def _lapse_decision_block_bids_past_dates(self, today: str) -> int:
        """Lapse all dicision_rtm_block_bid_iex_response records from dates before today."""
        total_modified = 0
        for status in LAPSABLE_STATUSES:
            lapsed_status = f"{status}_lapse"
            filter_query = {
                "Biding_date": {"$lt": today},
                "status": status
            }
            update_query = {"$set": {"status": lapsed_status}}
            try:
                result = await self.decision_block_bid_repo.update_many(filter_query, update_query)
                modified = result.get("modified_count", 0)
                if modified > 0:
                    logger.info(
                        "[RTMBidLapse] dicision_rtm_block_bid_iex_response past: %s -> %s: %d records",
                        status, lapsed_status, modified
                    )
                total_modified += modified
            except Exception as e:
                logger.error(
                    "[RTMBidLapse] Failed to lapse decision_block_bid past '%s': %s", status, str(e)
                )
        return total_modified

    async def _lapse_decision_block_bids(self, biding_date: str, from_time_lt: str) -> int:
        """
        Lapse dicision_rtm_block_bid_iex_response records for a specific date
        where 'from' time is before the active session cutoff.
        Uses same LAPSABLE_STATUSES as single bid flow.
        """
        total_modified = 0
        for status in LAPSABLE_STATUSES:
            lapsed_status = f"{status}_lapse"
            filter_query = {
                "Biding_date": biding_date,
                "from": {"$lt": from_time_lt},
                "status": status
            }
            update_query = {"$set": {"status": lapsed_status}}
            try:
                result = await self.decision_block_bid_repo.update_many(filter_query, update_query)
                modified = result.get("modified_count", 0)
                total_modified += modified
            except Exception as e:
                logger.error(
                    "[RTMBidLapse] Failed to lapse decision_block_bid '%s' for date=%s: %s",
                    status, biding_date, str(e)
                )
        return total_modified

    async def _lapse_block_bids(self, biding_date: str, trade_session_lt: int) -> int:
        """Lapse block bids for a specific date where trade_session has passed.
        Only lapses the latest revision per time_block.
        Skips time_blocks where the latest revision is already Submitted.
        """
        total_modified = 0
        try:
            pipeline = [
                {"$match": {
                    "biding_date": biding_date,
                    "trade_session": {"$lt": trade_session_lt},
                    "status": {"$in": BLOCK_BID_LAPSABLE_STATUSES}
                }},
                {"$sort": {"revision_no": -1}},
                {"$group": {
                    "_id": "$time_block",
                    "doc_id": {"$first": "$_id"},
                    "status": {"$first": "$status"}
                }},
                {"$match": {"status": {"$in": BLOCK_BID_LAPSABLE_STATUSES}}}
            ]
            latest_docs = await self.block_bid_repo.collection.aggregate(pipeline).to_list(length=10000)
            for doc in latest_docs:
                status = doc["status"]
                lapsed_status = f"{status}_lapse"
                result = await self.block_bid_repo.collection.update_one(
                    {"_id": doc["doc_id"]},
                    {"$set": {"status": lapsed_status}}
                )
                if result.modified_count > 0:
                    total_modified += 1
                    logger.info(
                        "[BlockBidLapse] %s time_block=%s: %s -> %s",
                        biding_date, doc["_id"], status, lapsed_status
                    )
        except Exception as e:
            logger.error(
                "[BlockBidLapse] Failed to lapse block bids for date=%s: %s",
                biding_date, str(e)
            )
        return total_modified


# Singleton instance
rtm_bid_lapse_processor = RTMBidLapseProcessor()
