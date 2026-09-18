"""Integration tests against the real local MongoDB.

Unit tests assert on the pipeline we build; these prove it is valid MongoDB and that
the numbers are right. mongomock is not installed and does not support `$unionWith`,
so there is no offline substitute. Skipped when Mongo is unreachable.

Two halves, because they answer two different questions:

* **A seeded throwaway database** (`epms_analytics_itest`) carries hand-written
  documents whose right answers are known by construction, so the assertions are
  exact numbers rather than "something came back". It is dropped and rebuilt for
  every run, so nothing here depends on what `tools/roll-dates.sh` did last night.
* **The live `epms` database**, read-only, at real volume and real document shapes.
  This is the half that proves the pipelines survive half a million documents and
  the untidy reality of the UAT import; it discovers its own dates rather than
  hardcoding any, and skips when a collection is empty.

Three of these tests exist because this file found the defect they pin:

1. The archival window leaves the *same document in both collections* for a few
   days — the vendor archives everything older than today but only deletes from
   serve what is older than `today - days_to_keep`. Without a dedupe after the
   `$unionWith`, every `$sum` dataset reads exactly double for the most recently
   traded dates. Measured before the fix: `ACV_RTM` 9,486.18 against a stored
   4,743.09.
2. `{"$ne": ["$field", None]}` is **true** for a *missing* field in MongoDB — only
   an explicit `null` compares equal to `null` in an aggregation expression. The
   `$sum` null guard therefore counted documents that contributed nothing, and a
   block with no DSM amount rendered as ₹0 rather than as no value.
3. `HOURLY_WISE` cannot work off `scheduled_at`, which is a trading date stamped at
   midnight; the time of day lives in `time_block`. See the test at the bottom.
"""
import asyncio
import datetime as dt
import sys
from pathlib import Path

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import (  # noqa: E402
    derived, executor, pivot, planner, registry,
)
from services.processing.app.v2.analytics.adapters import base as adapter_base  # noqa: E402
from services.processing.app.v2.repository import analytics_repository  # noqa: E402

MONGO_URI = "mongodb://localhost:27017"
ITEST_DB = "epms_analytics_itest"
LIVE_DB = "epms"


def _mongo_available():
    try:
        MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500).server_info()
        return True
    except ServerSelectionTimeoutError:
        return False


pytestmark = pytest.mark.skipif(not _mongo_available(),
                                reason="local MongoDB not running")


# --------------------------------------------------------------- seed fixture

D_2025_12_29 = dt.datetime(2025, 12, 29)   # ISO 2026-W01, calendar year 2025
D_2026_01_03 = dt.datetime(2026, 1, 3)     # ISO 2026-W01 as well
D_2026_01_04 = dt.datetime(2026, 1, 4)
D_2026_01_05 = dt.datetime(2026, 1, 5)     # ISO 2026-W02; the rolling-window day

#: Every date the seed covers, oldest first.
SEED_DATES = [D_2025_12_29, D_2026_01_03, D_2026_01_04, D_2026_01_05]

#: The day that exists in BOTH serve and historical, exactly as the vendor's
#: archival leaves it: copied into historical, not yet deleted from serve.
OVERLAP_DAY = D_2026_01_05

ARCHIVED = {"archived_at": dt.datetime(2026, 1, 6, 0, 3),
            "archived_timestamp": dt.datetime(2026, 1, 6, 0, 3)}


def _demand(day, block, type_, **over):
    doc = {
        "scheduled_at": day, "time_block": block, "type": type_,
        "actual": 500.0 + block, "drawal": 490.0 + block,
        "schedule": 480.0 + block, "comp_udm": 505.0 + block,
        "rostering": 0, "frequency": 50.0, "forecast": 400.0 + block,
        "mape": 10.0 if type_ == "DAF" else 2.0,
        "net_dsm_amount": 1000.0 if type_ == "IDF" else 0.0,
        "dsm_rate": 2.0,
    }
    doc.update(over)
    return doc


def _supply(day, block, plant, dc_sg, volume, revision):
    return {"scheduled_at": day, "time_block": block, "plant_id": plant,
            "dc_sg": dc_sg, "volume": volume, "system_revision_number": revision}


def _exchange(day, block, market, px, **over):
    doc = {"scheduled_at": day, "time_block": block,
           "market_type": market, "px_name": px,
           "acp": 0.0, "acv": 0.0, "buy_bid": 0.0, "sell_bid": 0.0}
    doc.update(over)
    return doc


def _seed(db):
    # Belt and braces: this function drops collections, and the only database it
    # may ever be pointed at is the throwaway one.
    assert db.name == ITEST_DB, f"refusing to seed over '{db.name}'"
    for name in db.list_collection_names():
        db.drop_collection(name)

    demand_serve, demand_hist = [], []
    for day in SEED_DATES:
        for block in (1, 2, 3):
            rows = [_demand(day, block, "DAF"), _demand(day, block, "IDF")]
            if day is D_2026_01_04 and block == 2:
                # No DSM amount at all for this cell: missing on one row, an
                # explicit null on the other. It must read as None, never as 0.
                rows[0].pop("net_dsm_amount")
                rows[1]["net_dsm_amount"] = None
            if day is OVERLAP_DAY:
                demand_serve.extend(rows)
                demand_hist.extend([dict(r, **ARCHIVED) for r in rows])
            else:
                demand_hist.extend([dict(r, **ARCHIVED) for r in rows])
    db.demand_serve.insert_many(demand_serve)
    db.demand_serve_historical.insert_many(demand_hist)

    # supply: two revisions of the same key on the overlap day, and the higher
    # revision also sitting in historical as the archival copy.
    supply_serve = [
        _supply(OVERLAP_DAY, 1, "PLANT_A", "DC", 100.0, 10),
        _supply(OVERLAP_DAY, 1, "PLANT_A", "DC", 140.0, 11),   # latest revision
        _supply(OVERLAP_DAY, 1, "PLANT_B", "DC", 60.0, 11),
        _supply(OVERLAP_DAY, 1, "PLANT_A", "SG", 90.0, 11),
    ]
    supply_hist = [dict(_supply(OVERLAP_DAY, 1, "PLANT_A", "DC", 140.0, 11), **ARCHIVED),
                   dict(_supply(OVERLAP_DAY, 1, "PLANT_B", "DC", 60.0, 11), **ARCHIVED),
                   dict(_supply(D_2026_01_04, 1, "PLANT_A", "DC", 120.0, 9), **ARCHIVED)]
    db.supply_serve.insert_many(supply_serve)
    db.supply_serve_historical.insert_many(supply_hist)

    # exchange: HPDAM cleared / uncleared / absent, GDAM in both acv shapes, and
    # an RTM row duplicated across the archival window.
    ex_serve = [
        # block 1 cleared at the cap, block 2 published as a real zero,
        # block 3 has no HPDAM document at all.
        _exchange(OVERLAP_DAY, 1, "HPDAM", "IEX", acp=20000.0, acv=12.0),
        _exchange(OVERLAP_DAY, 2, "HPDAM", "IEX", acp=0.0, acv=0.0),
        _exchange(OVERLAP_DAY, 1, "GDAM", "IEX",
                  acv={"total": 7.0, "hydro": 3.0, "wind": 4.0}),
        _exchange(OVERLAP_DAY, 2, "GDAM", "IEX", acv=5.0),   # scalar on the same day
        _exchange(OVERLAP_DAY, 1, "RTM", "IEX", acp=4000.0, acv=4743.09,
                  sell_bid=4743.1),
    ]
    db.exchange_serve.insert_many(ex_serve)
    db.exchange_serve_historical.insert_many([dict(d, **ARCHIVED) for d in ex_serve])

    db.peak_demand_serve.insert_many([
        {"scheduled_at": D_2026_01_05, "time_block": 89, "peak_demand": 600.0,
         "min_demand": 300.0, "average_demand": 450.0, "total_energy_mu": 10.0},
    ])
    db.peak_demand_serve_historical.insert_many([
        dict({"scheduled_at": D_2026_01_03, "time_block": 89, "peak_demand": 500.0,
              "min_demand": 200.0, "average_demand": 400.0, "total_energy_mu": 8.0},
             **ARCHIVED),
        dict({"scheduled_at": D_2026_01_04, "time_block": 89, "peak_demand": 700.0,
              "min_demand": 100.0, "average_demand": 420.0, "total_energy_mu": 9.0},
             **ARCHIVED),
    ])

    trade = [{"scheduled_at": OVERLAP_DAY, "time_block": 1, "type": "DAM",
              "exchange": "IEX", "buy_sell_flag": "Buy", "bidded": 31.1,
              "traded": 22.0},
             {"scheduled_at": OVERLAP_DAY, "time_block": 1, "type": "DAM",
              "exchange": "IEX", "buy_sell_flag": "S", "bidded": -14.1,
              "traded": -10.0}]
    db.bid_trade_serve.insert_many(trade)
    db.bid_trade_serve_historical.insert_many([dict(d, **ARCHIVED) for d in trade])


@pytest.fixture(scope="module")
def seeded():
    client = MongoClient(MONGO_URI)
    _seed(client[ITEST_DB])
    yield client[ITEST_DB]
    client.drop_database(ITEST_DB)
    client.close()


@pytest.fixture(scope="module")
def live():
    client = MongoClient(MONGO_URI)
    yield client[LIVE_DB]
    client.close()


# ------------------------------------------------------------------- helpers

async def _query(codes, filters, db_name=ITEST_DB):
    """planner -> executor -> AnalyticsRepository -> real MongoDB."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URI)
    try:
        repo = analytics_repository.AnalyticsRepository()
        repo.db = client[db_name]
        return await executor.execute(planner.plan(codes, filters), repo)
    finally:
        client.close()


def _cells(result, code):
    """{(date_key, block_key, entity): value} for one dataset."""
    return {(r["date_key"], r["block_key"], r["entity"]): r["value"]
            for r in result.rows if r["dataset"] == code}


def _filters(from_date, to_date, **over):
    f = {"from_date": from_date, "to_date": to_date,
         "from_block": 1, "to_block": 96, "date_grouping": "BLOCK_WISE"}
    f.update(over)
    return f


ONE_DAY = _filters(dt.date(2026, 1, 5), dt.date(2026, 1, 5), from_block=1, to_block=3)


# ==================================================== seeded: correct numbers

@pytest.mark.asyncio
async def test_a_single_day_of_actual_demand_matches_a_hand_written_find(seeded):
    result = await _query(["ACTUAL_DEMAND"], ONE_DAY)
    assert result.failed_datasets == []

    expected = {}
    for doc in seeded.demand_serve.find({"scheduled_at": D_2026_01_05}):
        if doc["time_block"] > 3:
            continue
        expected.setdefault(doc["time_block"], []).append(doc["actual"])
    hand = {("2026-01-05", str(b), None): sum(v) / len(v)
            for b, v in expected.items()}

    assert _cells(result, "ACTUAL_DEMAND") == hand
    # ACTUAL_DEMAND reads `actual` (user decision 2026-09-18); the seed is 500 + block.
    assert hand == {("2026-01-05", "1", None): 501.0,
                    ("2026-01-05", "2", None): 502.0,
                    ("2026-01-05", "3", None): 503.0}


@pytest.mark.asyncio
async def test_a_range_spanning_the_archival_boundary_unions_both_collections(seeded):
    """$unionWith or the query silently loses everything older than ~3 days."""
    spanning = await _query(["ACTUAL_DEMAND"],
                            _filters(dt.date(2025, 12, 29), dt.date(2026, 1, 5)))
    dates = sorted({k[0] for k in _cells(spanning, "ACTUAL_DEMAND")})
    assert dates == ["2025-12-29", "2026-01-03", "2026-01-04", "2026-01-05"]

    serve_only = seeded.demand_serve.distinct("scheduled_at")
    hist_only = seeded.demand_serve_historical.distinct("scheduled_at")
    assert len(dates) > len(serve_only)
    assert len(dates) > len(set(hist_only) - {OVERLAP_DAY})


@pytest.mark.asyncio
async def test_the_archival_overlap_does_not_double_a_summed_dataset(seeded):
    """The vendor archives a day before deleting it from serve, so for a few days
    the same document is in both collections. A `$sum` over the naive union reads
    exactly double — and these are the money columns, on the most recent dates."""
    stored = seeded.exchange_serve.find_one(
        {"scheduled_at": OVERLAP_DAY, "market_type": "RTM", "time_block": 1})
    assert seeded.exchange_serve_historical.count_documents(
        {"scheduled_at": OVERLAP_DAY, "market_type": "RTM", "time_block": 1}) == 1

    result = await _query(["ACV_RTM", "MKT_SELL_BID_RTM", "ACP_RTM"],
                          _filters(dt.date(2026, 1, 5), dt.date(2026, 1, 5),
                                   exchanges=["IEX"]))
    key = ("2026-01-05", "1", "IEX")
    assert _cells(result, "ACV_RTM")[key] == pytest.approx(stored["acv"])
    assert _cells(result, "MKT_SELL_BID_RTM")[key] == pytest.approx(stored["sell_bid"])
    assert _cells(result, "ACP_RTM")[key] == pytest.approx(stored["acp"])


@pytest.mark.asyncio
async def test_the_archival_overlap_does_not_double_the_portfolio_traded_volume(seeded):
    result = await _query(["PF_TRADED_DAM", "PF_BIDDED_DAM"],
                          _filters(dt.date(2026, 1, 5), dt.date(2026, 1, 5),
                                   direction="Buy"))
    # The portfolio datasets are registered `scalar`, not `per_exchange`, so they
    # carry no entity: `bid_trade_serve` is IEX-only.
    key = ("2026-01-05", "1", None)
    assert _cells(result, "PF_TRADED_DAM")[key] == pytest.approx(22.0)
    assert _cells(result, "PF_BIDDED_DAM")[key] == pytest.approx(31.1)


@pytest.mark.asyncio
async def test_a_cell_with_no_stored_value_is_none_and_never_zero(seeded):
    """Block 2 on 2026-01-04 has `net_dsm_amount` missing on one row and an
    explicit null on the other. PTC reads money off this column: ₹0 and "no
    amount recorded" are different statements."""
    result = await _query(["OD_UD_AMOUNT"],
                          _filters(dt.date(2026, 1, 4), dt.date(2026, 1, 4),
                                   from_block=1, to_block=3))
    cells = _cells(result, "OD_UD_AMOUNT")
    assert cells[("2026-01-04", "2", None)] is None
    # ... while a block that does have one still sums normally.
    assert cells[("2026-01-04", "1", None)] == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_a_stored_zero_is_not_turned_into_no_data(seeded):
    """The opposite lie. HPDAM publishes a real 0.0 for an uncleared block."""
    result = await _query(["ACP_HPDAM", "ACV_HPDAM"],
                          _filters(dt.date(2026, 1, 5), dt.date(2026, 1, 5),
                                   from_block=1, to_block=3, exchanges=["IEX"]))
    acp = _cells(result, "ACP_HPDAM")
    assert acp[("2026-01-05", "1", "IEX")] == 20000.0      # cleared, at the cap
    assert acp[("2026-01-05", "2", "IEX")] == 0.0          # published zero
    assert ("2026-01-05", "3", "IEX") not in acp           # no document at all


@pytest.mark.asyncio
async def test_gdam_acv_reads_both_the_object_and_the_scalar_shape(seeded):
    result = await _query(["ACV_GDAM"],
                          _filters(dt.date(2026, 1, 5), dt.date(2026, 1, 5),
                                   from_block=1, to_block=3, exchanges=["IEX"]))
    cells = _cells(result, "ACV_GDAM")
    assert cells[("2026-01-05", "1", "IEX")] == pytest.approx(7.0)   # $acv.total
    assert cells[("2026-01-05", "2", "IEX")] == pytest.approx(5.0)   # scalar $acv


@pytest.mark.asyncio
async def test_plant_wise_dc_returns_one_value_per_key_at_the_latest_revision(seeded):
    result = await _query(["DC_PLANT"],
                          _filters(dt.date(2026, 1, 5), dt.date(2026, 1, 5),
                                   from_block=1, to_block=1,
                                   plants=["PLANT_A", "PLANT_B"]))
    cells = _cells(result, "DC_PLANT")
    assert len(cells) == 2, cells
    # revision 11 (140.0) supersedes revision 10 (100.0); an $avg across the two
    # would read 120.0, which is a smear of the day's revision history.
    assert cells[("2026-01-05", "1", "PLANT_A")] == pytest.approx(140.0)
    assert cells[("2026-01-05", "1", "PLANT_B")] == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_a_plant_total_sums_the_latest_revision_of_each_plant_once(seeded):
    result = await _query(["DECLARED_CAPACITY_TOTAL"],
                          _filters(dt.date(2026, 1, 5), dt.date(2026, 1, 5),
                                   from_block=1, to_block=1))
    # 140 (PLANT_A, rev 11) + 60 (PLANT_B) — not 240 with the superseded revision,
    # and not 400 with the archival duplicate counted as well.
    assert _cells(result, "DECLARED_CAPACITY_TOTAL")[("2026-01-05", "1", None)] \
        == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_one_failed_group_still_serves_the_others(seeded):
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URI)
    try:
        repo = analytics_repository.AnalyticsRepository()
        repo.db = client[ITEST_DB]
        # The seed's only SG row is PLANT_A on OVERLAP_DAY (block 1, 90 MW).
        plans = planner.plan(
            ["ACTUAL_DEMAND", "SG_PLANT"],
            _filters(OVERLAP_DAY.date(), OVERLAP_DAY.date(), plants=["PLANT_A"]))
        broken = next(p for p in plans if p["group"] == "demand")
        broken["pipeline"] = [{"$notAStage": {}}]

        result = await executor.execute(plans, repo)
        assert result.failed_datasets == ["ACTUAL_DEMAND"]
        assert result.partial is True
        assert {r["dataset"] for r in result.rows} == {"SG_PLANT"}
        assert len(result.rows) > 0
    finally:
        client.close()


@pytest.mark.asyncio
async def test_max_time_ms_reaches_the_server(live):
    """The ceiling has to be enforced by MongoDB, not by a Python timeout that
    leaves the query running on a connection nobody is waiting for."""
    if live.demand_serve_historical.estimated_document_count() < 1_000:
        pytest.skip("live demand_serve_historical too small to time out")
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo.errors import ExecutionTimeout
    client = AsyncIOMotorClient(MONGO_URI)
    try:
        repo = analytics_repository.AnalyticsRepository()
        repo.db = client[LIVE_DB]
        with pytest.raises(ExecutionTimeout):
            await repo.run_pipeline(
                "demand_serve_historical",
                [{"$group": {"_id": "$time_block", "n": {"$sum": 1}}}],
                max_time_ms=1)
    finally:
        client.close()


# ==================================================== live database, read-only

def _latest_date(collection):
    doc = collection.find_one(sort=[("scheduled_at", -1)])
    return None if doc is None else doc["scheduled_at"].date()


LIVE_PROBE = [
    ("demand", "ACTUAL_DEMAND", "demand_serve_historical", {}),
    ("supply", "DC_PLANT", "supply_serve_historical", {}),
    ("exchange", "ACP_DAM", "exchange_serve_historical", {}),
    ("peak", "PEAK_DEMAND", "peak_demand_serve_historical", {}),
    ("trade", "PF_TRADED_DAM", "bid_trade_serve_historical", {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("group,code,probe_collection,extra", LIVE_PROBE,
                         ids=[p[0] for p in LIVE_PROBE])
async def test_every_source_group_pipeline_is_accepted_by_the_live_database(
        live, group, code, probe_collection, extra):
    """The syntax check the unit tests structurally cannot do: real collections,
    real document shapes, real volume."""
    day = _latest_date(live[probe_collection])
    if day is None:
        pytest.skip(f"{probe_collection} is empty in the live database")
    result = await _query([code], _filters(day, day, **extra), db_name=LIVE_DB)
    assert result.failed_groups == [], result.failed_groups


@pytest.mark.asyncio
async def test_live_actual_demand_for_one_day_matches_a_hand_written_find(live):
    day = _latest_date(live.demand_serve_historical)
    if day is None:
        pytest.skip("demand_serve_historical is empty in the live database")
    start = dt.datetime(day.year, day.month, day.day)

    hand = {}
    for doc in live.demand_serve_historical.find(
            {"scheduled_at": start, "time_block": {"$lte": 4}}):
        value = doc.get("actual")
        if value is not None:
            hand.setdefault(doc["time_block"], []).append(value)
    if not hand:
        pytest.skip(f"no `actual` values on {day} in the live database")

    result = await _query(["ACTUAL_DEMAND"],
                          _filters(day, day, from_block=1, to_block=4),
                          db_name=LIVE_DB)
    cells = _cells(result, "ACTUAL_DEMAND")
    for block, values in hand.items():
        assert cells[(day.isoformat(), str(block), None)] == pytest.approx(
            sum(values) / len(values))


@pytest.mark.asyncio
async def test_the_live_archival_overlap_is_deduplicated(live):
    """The same document really is in both collections in the live database — this
    is what the vendor's archival leaves behind, not an artefact of the seed."""
    overlap = (set(live.exchange_serve.distinct("scheduled_at"))
               & set(live.exchange_serve_historical.distinct("scheduled_at")))
    if not overlap:
        pytest.skip("no serve/historical overlap in the live exchange data today")
    day = max(overlap)

    stored = live.exchange_serve.find_one(
        {"scheduled_at": day, "acv": {"$nin": [0, 0.0, None]}})
    if stored is None or isinstance(stored.get("acv"), dict):
        pytest.skip("no scalar non-zero acv on the overlap day")
    twin = live.exchange_serve_historical.find_one(
        {"scheduled_at": day, "time_block": stored["time_block"],
         "market_type": stored["market_type"], "px_name": stored["px_name"]})
    if twin is None:
        pytest.skip("the overlap day is not duplicated key-for-key")

    code = f"ACV_{stored['market_type']}"
    result = await _query([code],
                          _filters(day.date(), day.date(),
                                   from_block=stored["time_block"],
                                   to_block=stored["time_block"],
                                   exchanges=[stored["px_name"]]),
                          db_name=LIVE_DB)
    key = (day.date().isoformat(), str(stored["time_block"]), stored["px_name"])
    assert _cells(result, code)[key] == pytest.approx(stored["acv"])


# ------------------------------------------------------------ known limitation

@pytest.mark.asyncio
async def test_hourly_grouping_derives_the_hour_from_the_block(seeded):
    """The hour of day is only ever in `time_block`; `scheduled_at` is a trading
    date stamped at midnight.

    Formatting the date field with %H used to label every row of a day with the
    same hour, collapsing 24 buckets into one. The key now appends an hour derived
    from the block (4 blocks per hour), so a full 96-block day buckets into 24.
    This writes its own collection rather than touching the shared seed, which only
    carries blocks 1-3.
    """
    from services.processing.app.v2.analytics import budget
    from services.processing.app.v2.analytics.grouping import date_key_expr

    coll = seeded["_hourly_probe"]
    coll.drop()
    day = dt.datetime(2026, 1, 5)
    coll.insert_many([
        {"scheduled_at": day, "time_block": b, "actual": float(b)}
        for b in range(1, 97)
    ])
    try:
        rows = list(coll.aggregate([
            {"$group": {"_id": date_key_expr("HOURLY_WISE"), "n": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]))
    finally:
        coll.drop()

    keys = [r["_id"] for r in rows]
    assert len(keys) == 24, keys
    assert keys[0] == "2026-01-05 00"
    assert keys[-1] == "2026-01-05 23"
    assert keys == sorted(keys), "hour must be zero-padded so keys sort lexically"
    assert all(r["n"] == 4 for r in rows), "four 15-minute blocks per hour"
    assert budget.date_bucket_count(dt.date(2026, 1, 5), dt.date(2026, 1, 5),
                                    "HOURLY_WISE") == len(keys)
