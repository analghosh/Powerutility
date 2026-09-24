"""
Read-only watcher for Day Ahead Forecast (DAF) rows in demand_serve.

Written to catch, in the act, whatever removes or zeroes DAF rows (the 23/24-09-2026
Demand Forecast incident). It opens a change stream on epms.demand_serve and logs,
with the exact time in IST:

  DELETE   a DAF row was deleted            (date, block, forecast it had)
  ZERO     a DAF row was written with forecast 0
  CHANGED  a DAF row's forecast changed     (old -> new)
  INSERT   a DAF row was created            (date, block, forecast, inserted_at)

A change stream delete carries only the _id, so the watcher keeps its own map of
_id -> (date, block, forecast) for DAF rows dated yesterday..day-after-tomorrow,
loaded at start and kept current from the stream.

It never writes to the database. The resume token is kept in a local file so a
restart continues where it stopped instead of missing events.

Usage (needs the prod connection string; our read access is enough):
    MONGODB_URI='mongodb+srv://…' .venv/bin/python tools/watch_daf_changes.py
    # optional: MONGODB_DATABASE (default epms), DAF_WATCH_LOG (default daf_watch.log)
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

from bson import json_util
from pymongo import MongoClient

IST = timezone(timedelta(hours=5, minutes=30))
DB_NAME = os.getenv("MONGODB_DATABASE", "epms")
LOG_PATH = os.getenv("DAF_WATCH_LOG", "daf_watch.log")
TOKEN_PATH = LOG_PATH + ".resume"


def log(kind, **fields):
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    line = f"{now}  {kind:<8} " + "  ".join(f"{k}={v}" for k, v in fields.items())
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def day(value):
    return value.strftime("%Y-%m-%d") if isinstance(value, datetime) else str(value)


def cluster_time_ist(change):
    ts = change.get("clusterTime")
    return datetime.fromtimestamp(ts.time, IST).strftime("%H:%M:%S") if ts else "?"


def load_known(coll):
    today = datetime.combine(datetime.now(IST).date(), datetime.min.time())
    window = {"$gte": today - timedelta(days=1), "$lte": today + timedelta(days=2)}
    known = {}
    for doc in coll.find({"type": "DAF", "scheduled_at": window},
                         {"scheduled_at": 1, "time_block": 1, "forecast": 1}):
        known[doc["_id"]] = (day(doc["scheduled_at"]), doc.get("time_block"), doc.get("forecast"))
    return known


def main():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        sys.exit("MONGODB_URI is required")
    coll = MongoClient(uri)[DB_NAME]["demand_serve"]

    known = load_known(coll)
    per_day = {}
    for d, _, _ in known.values():
        per_day[d] = per_day.get(d, 0) + 1
    log("START", tracking=json.dumps(per_day, sort_keys=True))

    resume_after = None
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, encoding="utf-8") as f:
            resume_after = json_util.loads(f.read())
        log("RESUME", token_file=TOKEN_PATH)

    pipeline = [{"$match": {"operationType": {"$in": ["insert", "replace", "update", "delete"]}}}]
    with coll.watch(pipeline, full_document="updateLookup", resume_after=resume_after) as stream:
        for change in stream:
            op = change["operationType"]
            _id = change["documentKey"]["_id"]
            at = cluster_time_ist(change)

            if op == "delete":
                if _id in known:
                    d, tb, fc = known.pop(_id)
                    log("DELETE", at=at, date=d, block=tb, forecast=fc, id=_id)
            else:
                doc = change.get("fullDocument") or {}
                if doc.get("type") == "DAF":
                    d, tb, fc = day(doc.get("scheduled_at")), doc.get("time_block"), doc.get("forecast")
                    prev = known.get(_id)
                    if op == "insert":
                        log("INSERT", at=at, date=d, block=tb, forecast=fc,
                            inserted_at=doc.get("inserted_at"), source=doc.get("source"))
                    elif prev and prev[2] != fc:
                        log("CHANGED", at=at, op=op, date=d, block=tb, old=prev[2], new=fc)
                    if fc == 0:
                        log("ZERO", at=at, op=op, date=d, block=tb,
                            inserted_at=doc.get("inserted_at"), updated_at=doc.get("updated_at"))
                    known[_id] = (d, tb, fc)

            with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                f.write(json_util.dumps(change["_id"]))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("STOP")
