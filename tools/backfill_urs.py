#!/usr/bin/env python
"""
Backfill ``urs_serve`` from the Grid India feed for a range of past dates.

Why this exists: URS only reaches ``urs_serve`` when the ingestion cron happens
to be running on the day in question, so every day before the feature shipped is
missing. Two of the three ways to recover those days do not work:

  * ``supply_serve`` history is useless - the parser used to sum URS into SG
    with no marker, and that cannot be un-merged.
  * ``*_integration_apilog`` holds the raw payloads but is a timeseries
    collection created with ``expireAfterSeconds=604800``, and a nightly
    ``integration_apilog_cleanup`` job deletes anything older. Seven days, at
    most, and only if the collection has not just been trimmed.

What does work is asking Grid India again. The provider body already carries a
``Date``; the gateway honours a past one and returns that day's schedule at its
final revision (``SchdRevNo: -1``). This re-fetches those payloads and runs them
through the same parser and the same document builder the live pipeline uses -
not a reimplementation, the actual ``SupplyProcessor`` methods - so a backfilled
row is byte-identical to one the cron would have written.

Only ``urs_serve`` is touched. The DC/SG records in the same payload are parsed
and then dropped on the floor: re-running them would overwrite historical
``supply_serve`` rows with a later revision and silently move the PLF/PAF
numbers for days PTC may already have signed off.

Run it ONCE, by hand, for the days that are missing. It is deliberately not
wired into the scheduler and has no cron entry: the live pipeline already keeps
today and tomorrow current, so a repeating backfill would spend calls on PTC's
gateway credential to rewrite rows that are already right. Future dates are
refused outright. Writes are upserts keyed on (scheduled_at, time_block,
plant_id), so if it does get run twice over the same range, the second run
changes nothing.

    # look first - fetches and parses, writes nothing
    ./.venv/bin/python tools/backfill_urs.py --from 2026-09-08 --to 2026-09-21

    # then commit it
    ./.venv/bin/python tools/backfill_urs.py --from 2026-09-08 --to 2026-09-21 --write

Must run on a host the gateway accepts: gateway.grid-india.in TLS-resets any
address that is not allow-listed, so this works on the UAT/PROD box and not on a
laptop. Run it from the backend root with the service venv.
"""

import argparse
import asyncio
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient  # noqa: E402

from libs.parsers.parser_factory import create_parser  # noqa: E402
from services.scheduler.app.core.config import settings  # noqa: E402
from services.scheduler.app.processors.supply_processor import SupplyProcessor  # noqa: E402
from services.scheduler.app.repository.urs_serve_repository import (  # noqa: E402
    urs_serve_repository,
)

#: A guard, not a limit of the feed. A fat-fingered year turns into hundreds of
#: calls against a government gateway on PTC's credential; refuse instead.
MAX_DAYS = 31

#: The gateway is not ours and this is a bulk read. One request at a time, with
#: a pause, so a backfill never looks like a flood from PTC's account.
REQUEST_GAP_SECONDS = 1.5


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="date_from", required=True, help="first date, YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", required=True, help="last date, YYYY-MM-DD (inclusive)")
    p.add_argument("--provider", default="GRID_INDIA_SUPPLY", help="provider code to borrow the endpoint and credential from")
    p.add_argument("--write", action="store_true", help="upsert into urs_serve; without it nothing is written")
    p.add_argument("--dump-dir", help="also save each raw payload here, for inspection")
    return p.parse_args()


def date_range(first: str, last: str) -> List[date]:
    try:
        start = datetime.strptime(first, "%Y-%m-%d").date()
        end = datetime.strptime(last, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"dates must be YYYY-MM-DD: {exc}")
    if end < start:
        raise SystemExit("--to is before --from")
    span = (end - start).days + 1
    if span > MAX_DAYS:
        raise SystemExit(f"refusing {span} days; {MAX_DAYS} is the cap, run it in chunks")

    # Past and present only. Tomorrow is already the live cron's job via
    # GRID_INDIA_SUPPLY_TOMMOROW, and anything beyond it is a schedule that has
    # not been published yet - backfilling it would write a guess that nothing
    # later corrects. This tool exists to close a gap behind us, once.
    today = date.today()
    if end > today:
        raise SystemExit(
            f"--to {end} is in the future; this backfills past days only "
            f"(today is {today}, and today and tomorrow are the cron's job)"
        )
    return [start + timedelta(days=i) for i in range(span)]


def load_provider(code: str) -> Dict[str, Any]:
    client = MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=8000)
    provider = client[settings.MONGODB_DATABASE]["providers"].find_one({"code": code})
    if not provider:
        raise SystemExit(f"no provider with code {code} in {settings.MONGODB_DATABASE}.providers")
    if not (provider.get("metadata") or {}).get("api_url"):
        raise SystemExit(f"provider {code} has no metadata.api_url")
    return provider


def fetch_day(metadata: Dict[str, Any], day: date) -> Dict[str, Any]:
    """One payload, at the day's final revision."""
    body = dict(metadata.get("body") or {})
    body["Date"] = day.strftime("%d-%m-%Y")
    # -1 is the feed's own "latest revision" sentinel. A backfill wants the
    # settled picture, never whichever intraday revision we happened to catch.
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

    Kept as an independent check on the parser: the summary is computed by Grid
    India, the per-plant rows are computed here, and the two must agree. A
    silent divergence is what a parser bug looks like.
    """
    try:
        groups = payload["ResponseBody"]["GroupWiseDataList"]
        total = 0.0
        for group in groups:
            for row in group["NetScheduleSummary"]["NetSchdDataList"]:
                if row.get("EnergyScheduleTypeName") == "URS":
                    total += sum(row.get("NetSchdAmount") or [])
        return total
    except (KeyError, IndexError, TypeError):
        return None


async def backfill_day(
    processor: SupplyProcessor,
    provider: Dict[str, Any],
    payload: Dict[str, Any],
    write: bool,
) -> Dict[str, Any]:
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

    result = {}
    if write and documents:
        result = await urs_serve_repository.bulk_upsert(documents)

    return {
        "urs_records": len(urs_records),
        "documents": documents,
        "plants": sorted({d["plant_id"] for d in documents}),
        "parsed_total": round(sum(d["volume"] or 0.0 for d in documents), 3),
        "unresolved": unresolved,
        "write_result": result,
    }


async def main() -> int:
    args = parse_args()
    days = date_range(args.date_from, args.date_to)
    provider = load_provider(args.provider)
    metadata = provider["metadata"]
    processor = SupplyProcessor()

    dump_dir = Path(args.dump_dir) if args.dump_dir else None
    if dump_dir:
        dump_dir.mkdir(parents=True, exist_ok=True)

    print(f"database : {settings.MONGODB_DATABASE} on {settings.MONGODB_URI.split('@')[-1]}")
    print(f"provider : {provider.get('code')}  ({metadata.get('parser_type')})")
    print(f"range    : {days[0]} .. {days[-1]}  ({len(days)} days)")
    print(f"mode     : {'WRITE to urs_serve' if args.write else 'dry run, nothing written'}")
    print("-" * 78)
    print(f"{'date':<12}{'feed URS':>11}{'parsed':>11}{'rows':>7}{'plants':>8}  note")

    written = 0
    failures = 0
    for index, day in enumerate(days):
        if index:
            time.sleep(REQUEST_GAP_SECONDS)
        try:
            payload = fetch_day(metadata, day)
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, json.JSONDecodeError) as exc:
            failures += 1
            print(f"{day.isoformat():<12}{'-':>11}{'-':>11}{'-':>7}{'-':>8}  FETCH FAILED: {exc}")
            continue

        if dump_dir:
            (dump_dir / f"{day.isoformat()}.json").write_text(json.dumps(payload))

        outcome = await backfill_day(processor, provider, payload, args.write)
        feed_total = feed_urs_total(payload)
        written += len(outcome["documents"]) if args.write else 0

        notes = []
        if feed_total is not None and abs(feed_total - outcome["parsed_total"]) > 0.5:
            # Loud on purpose. The two numbers come from different places and a
            # gap means the per-plant rows do not add up to the feed's own total.
            notes.append(f"MISMATCH vs feed (delta {feed_total - outcome['parsed_total']:+.2f})")
        if outcome["unresolved"]:
            missing = ", ".join(f"{k}x{v}" for k, v in sorted(outcome["unresolved"].items()))
            notes.append(f"no plant_master match: {missing}")
        if outcome["write_result"]:
            notes.append(str(outcome["write_result"]))

        print(
            f"{day.isoformat():<12}"
            f"{'-' if feed_total is None else f'{feed_total:.2f}':>11}"
            f"{outcome['parsed_total']:>11.2f}"
            f"{len(outcome['documents']):>7}"
            f"{len(outcome['plants']):>8}"
            f"  {'; '.join(notes)}"
        )

    print("-" * 78)
    if args.write:
        print(f"upserted {written} urs_serve rows across {len(days) - failures} days")
    else:
        print(f"dry run over {len(days) - failures} days - re-run with --write to store")
    if failures:
        print(f"{failures} day(s) could not be fetched")
    return 1 if failures == len(days) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
