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

Fetching and storing can be done from different machines, which matters because
the gateway answers very few of them. Save the payloads where it does answer
(--dump-dir, or Postman's "Save response"), then parse them anywhere:

    ./.venv/bin/python tools/backfill_urs.py --from-dir ./urs-payloads

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

from services.scheduler.app.core.config import settings  # noqa: E402
from services.scheduler.app.processors.supply_processor import SupplyProcessor  # noqa: E402
from services.scheduler.app.services.urs_backfill import (  # noqa: E402
    MAX_DAYS,
    REQUEST_GAP_SECONDS,
    fetch_day,
    load_provider,
    process_payload,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="date_from", help="first date, YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", help="last date, YYYY-MM-DD (inclusive)")
    p.add_argument("--provider", default="GRID_INDIA_SUPPLY", help="provider code to borrow the endpoint and credential from")
    p.add_argument("--write", action="store_true", help="upsert into urs_serve; without it nothing is written")
    p.add_argument("--dump-dir", help="also save each fetched payload here, for inspection")
    p.add_argument("--from-dir", help="read payloads already saved in this directory instead of calling the gateway")
    args = p.parse_args()
    if not args.from_dir and not (args.date_from and args.date_to):
        p.error("give --from and --to, or --from-dir")
    return args


def load_saved_payloads(directory: str) -> List[tuple]:
    """
    Payloads already on disk, from a machine the gateway does answer.

    The date is read from each payload rather than from its filename, so
    anything works - a Postman "Save response", a browser download, whatever
    the operator had to hand on the box that could reach the gateway.
    """
    folder = Path(directory)
    if not folder.is_dir():
        raise SystemExit(f"--from-dir {directory} is not a directory")

    found = []
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"skipping {path.name}: {exc}")
            continue
        stamp = ((payload.get("ResponseBody") or {}).get("Date") or "").strip()
        try:
            day = datetime.strptime(stamp, "%d-%m-%Y").date()
        except ValueError:
            print(f"skipping {path.name}: no readable ResponseBody.Date ({stamp!r})")
            continue
        found.append((day, payload, path.name))

    if not found:
        raise SystemExit(f"no usable payloads in {directory}")
    # One date can arrive twice at different revisions; the later file wins.
    latest = {}
    for day, payload, name in found:
        latest[day] = (payload, name)
    return [(day, *latest[day]) for day in sorted(latest)]


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


async def main() -> int:
    args = parse_args()
    provider = await load_provider(args.provider)
    if not provider:
        raise SystemExit(f"no usable provider with code {args.provider}")
    metadata = provider["metadata"]
    processor = SupplyProcessor()

    saved = load_saved_payloads(args.from_dir) if args.from_dir else None
    days = [row[0] for row in saved] if saved else date_range(args.date_from, args.date_to)

    dump_dir = Path(args.dump_dir) if args.dump_dir else None
    if dump_dir:
        dump_dir.mkdir(parents=True, exist_ok=True)

    print(f"database : {settings.MONGODB_DATABASE} on {settings.MONGODB_URI.split('@')[-1]}")
    print(f"provider : {provider.get('code')}  ({metadata.get('parser_type')})")
    print(f"source   : {'saved payloads in ' + args.from_dir if saved else 'gateway fetch'}")
    print(f"range    : {days[0]} .. {days[-1]}  ({len(days)} days)")
    print(f"mode     : {'WRITE to urs_serve' if args.write else 'dry run, nothing written'}")
    print("-" * 78)
    print(f"{'date':<12}{'feed URS':>11}{'parsed':>11}{'rows':>7}{'plants':>8}  note")

    written = 0
    failures = 0
    for index, day in enumerate(days):
        if saved:
            payload = saved[index][1]
        else:
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

        outcome = await process_payload(processor, provider, payload, args.write)
        feed_total = outcome["feed_total"]
        written += len(outcome["documents"]) if args.write else 0

        notes = []
        if outcome["mismatch"]:
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
