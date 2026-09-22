"""
One-shot URS backfill.

URS only reaches ``urs_serve`` when the ingestion cron happens to be running on
the day in question, so every day before the feature shipped is missing. Grid
India will serve a past ``Date`` at its final revision, so those days can be
recovered by asking again.

Two things make this awkward enough to need its own module:

  * ``gateway.grid-india.in`` IP-allowlists. Only the deployment host can fetch,
    so this cannot be run from a laptop - it has to execute inside a service.
  * The obvious route, feeding a past date through the normal ingestion
    pipeline, is wrong: ``process_raw_data_to_serve`` sends anything older than
    today down ``_process_direct_to_supply_serve_historical``, which would
    rewrite DC/SG into ``supply_serve_historical`` for days that are already
    settled. This writes ``urs_serve`` and nothing else.

Everything here runs through the live ``SupplyProcessor`` methods rather than a
reimplementation, so a backfilled row is identical to one the cron would have
written. Writes are upserts keyed on (scheduled_at, time_block, plant_id).

ON by default and configured by nobody: the people who need this have no shell
and no database access to the host, so a redeploy has to be the whole operation.

That is only safe because of two things. The window is a PINNED PAIR OF DATES,
not "the last fortnight" - so this code running unexpectedly, months later, on
another environment, still asks for the same fourteen September days and not
for whatever fortnight happens to precede it. And every date attempted is
recorded in ``urs_backfill_state``, so it happens exactly once per database no
matter how many times the service restarts.

    URS_BACKFILL_ON_START=false    # the off switch, if it ever needs one
    URS_BACKFILL_FROM=2026-09-08   # or move the window
    URS_BACKFILL_TO=2026-09-21

**This module is temporary.** Once the gap is closed - check
``urs_backfill_state`` has a document per date - delete it and its call in
``main.py``. It is inert until then, not harmless-forever by design.
"""

import asyncio
import json
import logging
import os
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from libs.database.mongo_client_factory import get_motor_client

from ..core.config import settings

logger = logging.getLogger(__name__)

#: One document per date attempted, so a restart never refetches.
STATE_COLLECTION = "urs_backfill_state"

PROVIDER_CODE = "GRID_INDIA_SUPPLY"

#: Belt and braces on top of the state collection: even inside one process this
#: can only ever run once, so nothing that re-enters the lifespan can double it.
_has_run = False

#: A guard, not a limit of the feed. Keeps a mistyped window from turning into
#: hundreds of calls on a government gateway against PTC's account.
MAX_DAYS = 31

#: The gap this exists to close: the fortnight before URS storage shipped.
#: Pinned rather than computed from today on purpose - a relative window would
#: make a late or repeated deployment fetch a different, unintended fortnight.
DEFAULT_WINDOW_FROM = date(2026, 9, 8)
DEFAULT_WINDOW_TO = date(2026, 9, 21)

#: The gateway is not ours and this is a bulk read. One at a time, with a pause.
REQUEST_GAP_SECONDS = 1.5

#: Let the service finish coming up first; the cron manager and the consumers
#: are starting in parallel and this is not urgent.
START_DELAY_SECONDS = 30


TRUTHY = {"1", "true", "yes", "on"}
FALSEY = {"0", "false", "no", "off"}


def backfill_enabled() -> bool:
    """
    On unless explicitly switched off.

    Inverted from the usual feature-flag default because the operators cannot
    set an environment variable on this host - redeploying is the only lever
    they have. The run-once state collection is what makes that acceptable.
    """
    value = str(os.getenv("URS_BACKFILL_ON_START", "")).strip().lower()
    if value in FALSEY:
        return False
    return True


def _database():
    client = get_motor_client(settings.MONGODB_URI, "shared_repository_client")
    return client[settings.MONGODB_DATABASE]


async def load_provider(code: str = PROVIDER_CODE) -> Optional[Dict[str, Any]]:
    provider = await _database()["providers"].find_one({"code": code})
    if not provider or not (provider.get("metadata") or {}).get("api_url"):
        logger.error("URS backfill: provider %s missing or has no api_url", code)
        return None
    return provider


def fetch_day(metadata: Dict[str, Any], day: date) -> Dict[str, Any]:
    """
    One payload, at the day's final revision.

    Blocking on purpose - callers hand it to a thread. ``SchdRevNo: -1`` is the
    feed's own "latest revision" sentinel; a backfill wants the settled picture,
    never whichever intraday revision we happened to catch.
    """
    body = dict(metadata.get("body") or {})
    body["Date"] = day.strftime("%d-%m-%Y")
    body["SchdRevNo"] = -1

    request = urllib.request.Request(
        metadata["api_url"],
        data=json.dumps(body).encode("utf-8"),
        headers=metadata.get("headers") or {"Content-Type": "application/json"},
        method=(metadata.get("method") or "POST").upper(),
    )
    with urllib.request.urlopen(request, timeout=metadata.get("timeout", 60)) as response:
        return json.loads(response.read().decode("utf-8"))


def feed_urs_total(payload: Dict[str, Any]) -> Optional[float]:
    """
    The feed's own URS figure, from NetScheduleSummary.

    An independent check on the parser: Grid India computes the summary, we
    compute the per-plant rows, and the two must agree. A silent divergence is
    what a parser bug looks like.
    """
    try:
        total = 0.0
        for group in payload["ResponseBody"]["GroupWiseDataList"]:
            for row in group["NetScheduleSummary"]["NetSchdDataList"]:
                if row.get("EnergyScheduleTypeName") == "URS":
                    total += sum(row.get("NetSchdAmount") or [])
        return total
    except (KeyError, IndexError, TypeError):
        return None


async def process_payload(
    processor,
    provider: Dict[str, Any],
    payload: Dict[str, Any],
    write: bool,
) -> Dict[str, Any]:
    """Parse one payload and, if asked, store its URS rows. Writes nothing else."""
    from libs.parsers.parser_factory import create_parser

    from ..repository.urs_serve_repository import urs_serve_repository

    parser_type = (provider.get("metadata") or {}).get("parser_type") or "grid_india"
    records = create_parser(parser_type=parser_type).parse(payload)

    # DC/SG deliberately discarded - see the module docstring.
    _, urs_records = processor._split_out_urs(records)

    documents: List[Dict[str, Any]] = []
    unresolved: Dict[str, int] = {}
    for record in urs_records:
        plant = await processor._resolve_plant(record)
        if not plant:
            key = str(record.get("plant_id") or "?")
            unresolved[key] = unresolved.get(key, 0) + 1
            continue
        scheduled_at = processor._parse_scheduled_at(record)
        if not scheduled_at:
            continue
        document = processor._build_urs_document(
            record, plant, scheduled_at, plant.get("plant_id"), provider
        )
        if document:
            documents.append(document)

    write_result: Dict[str, Any] = {}
    if write and documents:
        write_result = await urs_serve_repository.bulk_upsert(documents)

    parsed_total = round(sum(d["volume"] or 0.0 for d in documents), 3)
    feed_total = feed_urs_total(payload)
    return {
        "urs_records": len(urs_records),
        "documents": documents,
        "rows": len(documents),
        "plants": sorted({d["plant_id"] for d in documents}),
        "parsed_total": parsed_total,
        "feed_total": feed_total,
        # Loud on purpose: the two numbers come from different places, and a gap
        # means the per-plant rows do not add up to the feed's own total.
        "mismatch": feed_total is not None and abs(feed_total - parsed_total) > 0.5,
        "unresolved": unresolved,
        "write_result": write_result,
    }


def resolve_window() -> List[date]:
    """The days to attempt: an explicit window, or the last N ending yesterday."""
    explicit_from = os.getenv("URS_BACKFILL_FROM", "").strip()
    explicit_to = os.getenv("URS_BACKFILL_TO", "").strip()

    # Today and tomorrow belong to the live cron, and anything past that is a
    # schedule Grid India has not published yet.
    yesterday = date.today() - timedelta(days=1)

    if explicit_from and explicit_to:
        try:
            start = datetime.strptime(explicit_from, "%Y-%m-%d").date()
            end = datetime.strptime(explicit_to, "%Y-%m-%d").date()
        except ValueError:
            logger.error(
                "URS backfill: URS_BACKFILL_FROM/TO must be YYYY-MM-DD, got %r and %r",
                explicit_from, explicit_to,
            )
            return []
        end = min(end, yesterday)
        if end < start:
            logger.error("URS backfill: window %s..%s is empty", explicit_from, explicit_to)
            return []
    else:
        start = DEFAULT_WINDOW_FROM
        end = min(DEFAULT_WINDOW_TO, yesterday)
        if end < start:
            # The pinned window has not finished yet. Nothing to recover.
            return []

    span = (end - start).days + 1
    if span > MAX_DAYS:
        logger.error("URS backfill: refusing %d days; %d is the cap", span, MAX_DAYS)
        return []
    return [start + timedelta(days=i) for i in range(span)]


async def _already_done(day: date) -> bool:
    return await _database()[STATE_COLLECTION].find_one({"_id": day.isoformat()}) is not None


async def _record(day: date, outcome: Dict[str, Any]) -> None:
    await _database()[STATE_COLLECTION].update_one(
        {"_id": day.isoformat()},
        {"$set": {**outcome, "completed_at": datetime.now()}},
        upsert=True,
    )


async def run_backfill(days: List[date], write: bool = True) -> Dict[str, Any]:
    """Attempt each day that has not been attempted before."""
    from ..processors.supply_processor import SupplyProcessor

    provider = await load_provider()
    if not provider:
        return {"ran": False, "reason": "provider not found"}

    metadata = provider["metadata"]
    processor = SupplyProcessor()
    attempted = skipped = failed = rows = 0

    for day in days:
        if await _already_done(day):
            skipped += 1
            continue

        if attempted:
            await asyncio.sleep(REQUEST_GAP_SECONDS)
        attempted += 1

        try:
            payload = await asyncio.to_thread(fetch_day, metadata, day)
        except Exception as exc:
            # Not recorded as done, so the next restart retries this day.
            failed += 1
            logger.warning("URS backfill %s: fetch failed: %s", day, exc)
            continue

        try:
            outcome = await process_payload(processor, provider, payload, write)
        except Exception as exc:
            failed += 1
            logger.error("URS backfill %s: parse failed: %s", day, exc, exc_info=True)
            continue

        rows += outcome["rows"]
        logger.info(
            "URS backfill %s: feed=%s parsed=%s rows=%d plants=%d%s%s",
            day, outcome["feed_total"], outcome["parsed_total"],
            outcome["rows"], len(outcome["plants"]),
            "  MISMATCH" if outcome["mismatch"] else "",
            f"  unresolved={outcome['unresolved']}" if outcome["unresolved"] else "",
        )

        await _record(day, {
            "feed_total": outcome["feed_total"],
            "parsed_total": outcome["parsed_total"],
            "rows": outcome["rows"],
            "plants": outcome["plants"],
            "mismatch": outcome["mismatch"],
            "unresolved": outcome["unresolved"],
            "written": bool(write),
        })

    summary = {
        "ran": True, "attempted": attempted, "skipped": skipped,
        "failed": failed, "rows": rows,
    }
    logger.info("URS backfill finished: %s", summary)
    return summary


async def pending_days() -> List[date]:
    """The days in the window that have not been done yet. Usually empty."""
    return [day for day in resolve_window() if not await _already_done(day)]


async def run_startup_backfill() -> None:
    """
    Entry point for the scheduler's lifespan. Never raises into startup.

    Runs the window ONCE, ever - not once per restart. Every date attempted is
    recorded in ``urs_backfill_state``, so the second restart finds nothing
    pending, makes no gateway call and returns. Leaving the flag set is
    therefore harmless; the only way to make it fetch a date again is to delete
    that date's state document by hand.

    Deliberately not a cron job: this closes a one-time gap behind us, and the
    live pipeline already keeps today and tomorrow current.
    """
    global _has_run

    if not backfill_enabled():
        logger.info("URS backfill disabled by URS_BACKFILL_ON_START")
        return
    if _has_run:
        return
    _has_run = True

    try:
        await asyncio.sleep(START_DELAY_SECONDS)

        window = resolve_window()
        if not window:
            return

        days = await pending_days()
        if not days:
            logger.info(
                "URS backfill: %s .. %s already done on an earlier run, nothing to do",
                window[0], window[-1],
            )
            return

        logger.info(
            "URS backfill starting: %d of %d days pending (%s .. %s); "
            "today and later are never included",
            len(days), len(window), window[0], window[-1],
        )
        await run_backfill(days, write=True)
    except Exception as exc:
        logger.error("URS backfill aborted: %s", exc, exc_info=True)
