"""Exchange and trade adapters — the two market source groups.

Both carry a shape trap the base class cannot know about:

* `exchange_serve.acv` is a **sub-document** for GDAM (`total`, plus a renewable
  breakdown) and a scalar everywhere else. Locally the GDAM rows are mixed —
  384 object / 288 scalar in `exchange_serve`, 4,704 / 3,168 in the historical
  twin — so a bare `$acv.total` silently drops ~40% of GDAM rows to null.
* `bid_trade_serve.buy_sell_flag` holds `'B'`, `'Buy'` and `'S'` for the same
  concept, so anything keyed on the raw flag splits one direction into two.

These tests assert on the pipeline built, as the rest of the analytics suite does;
Task 18 proves the pipelines are valid MongoDB against the real database.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import block_bands as bb  # noqa: E402
from services.processing.app.v2.analytics import registry  # noqa: E402
from services.processing.app.v2.analytics.adapters import base  # noqa: E402
from services.processing.app.v2.analytics.adapters import exchange  # noqa: E402
from services.processing.app.v2.analytics.adapters import trade  # noqa: E402

FILTERS = {
    "from_date": date(2026, 8, 1),
    "to_date": date(2026, 8, 4),
    "from_block": 1,
    "to_block": 96,
    "date_grouping": "DATE_WISE",
    "block_band": bb.BAND_INDIVIDUAL,
}
CTX = {"plant_count": 34, "exchange_count": 3}

EXCHANGE = exchange.ExchangeAdapter()
TRADE = trade.TradeAdapter()


def ds(*codes):
    return [registry.get(c) for c in codes]


def stages(pipeline, key):
    return [s for s in pipeline if key in s]


def group_of(pipeline):
    return stages(pipeline, "$group")[-1]["$group"]


def value_fields(group):
    """Dataset accumulators, excluding the `_n_*` null-guard counters."""
    return [k for k in group
            if k != "_id" and not k.startswith(base.NULL_GUARD_PREFIX)]


def text(expr):
    return json.dumps(expr, sort_keys=True, default=str)


# ------------------------------------------------------------------ exchange

def test_exchange_adapter_declares_its_collections_and_entity():
    assert EXCHANGE.group == "exchange"
    assert EXCHANGE.serve_collection == "exchange_serve"
    assert EXCHANGE.historical_collection == "exchange_serve_historical"
    assert EXCHANGE.entity_field() == "px_name"


def test_exchange_adapter_is_registered_for_its_group():
    assert isinstance(base.get_adapter("exchange"), exchange.ExchangeAdapter)


def test_registering_a_second_exchange_adapter_raises():
    with pytest.raises(ValueError):
        base.register(exchange.ExchangeAdapter)


def test_acv_gdam_reads_the_subdocument_total():
    pipeline = EXCHANGE.build_pipeline(datasets=ds("ACV_GDAM"), filters=FILTERS)
    assert "$acv.total" in text(group_of(pipeline)["ACV_GDAM"])


def test_acv_dam_reads_the_scalar_field():
    pipeline = EXCHANGE.build_pipeline(datasets=ds("ACV_DAM"), filters=FILTERS)
    expr = text(group_of(pipeline)["ACV_DAM"])
    assert "$acv" in expr
    assert "$acv.total" not in expr


def test_acv_gdam_falls_back_to_the_scalar_when_acv_is_not_a_subdocument():
    # Real GDAM rows come both ways; a bare $acv.total nulls the scalar ones.
    assert exchange.acv_expr() == {
        "$cond": [{"$eq": [{"$type": "$acv"}, "object"]}, "$acv.total", "$acv"],
    }


def test_acv_gdam_shape_guard_stays_inside_the_market_discriminator():
    pipeline = EXCHANGE.build_pipeline(datasets=ds("ACV_GDAM"), filters=FILTERS)
    cond = group_of(pipeline)["ACV_GDAM"]["$sum"]["$cond"]
    assert cond[0] == {"$and": [{"$eq": ["$market_type", "GDAM"]}]}
    assert cond[1] == exchange.acv_expr()
    assert cond[2] is None


def test_four_markets_produce_one_pipeline_with_four_accumulators():
    codes = ["ACP_DAM", "ACP_RTM", "ACP_GDAM", "ACP_HPDAM"]
    pipeline = EXCHANGE.build_pipeline(datasets=ds(*codes), filters=FILTERS)
    # One query: one prefilter, one read of the pair, one analytics $group. (The
    # earlier $group is the archival dedupe, not a second read.)
    assert len(stages(pipeline, "$match")) == 1
    assert len(stages(pipeline, "$unionWith")) == 1
    assert value_fields(group_of(pipeline)) == codes


def test_each_market_accumulator_carries_its_own_market_type_guard():
    codes = ["ACP_DAM", "ACP_RTM", "ACP_GDAM", "ACP_HPDAM"]
    group = group_of(EXCHANGE.build_pipeline(datasets=ds(*codes), filters=FILTERS))
    for code in codes:
        market = code.split("_", 1)[1]
        cond = group[code]["$avg"]["$cond"]
        assert cond[0] == {"$and": [{"$eq": ["$market_type", market]}]}
        assert cond[1] == "$acp"
        assert cond[2] is None


def test_market_discriminators_never_reach_the_shared_match():
    # market_type=DAM in the top-level $match would delete every RTM/GDAM/HPDAM
    # row from the same query. This is the whole reason for the $cond above.
    codes = ["ACP_DAM", "ACP_RTM", "ACP_GDAM", "ACP_HPDAM"]
    pipeline = EXCHANGE.build_pipeline(datasets=ds(*codes), filters=FILTERS)
    assert "market_type" not in pipeline[0]["$match"]


def test_a_single_market_hoists_its_discriminator_as_a_prefilter():
    pipeline = EXCHANGE.build_pipeline(datasets=ds("ACP_HPDAM"), filters=FILTERS)
    assert pipeline[0]["$match"]["market_type"] == "HPDAM"


def test_exchange_group_id_keeps_the_exchanges_apart():
    pipeline = EXCHANGE.build_pipeline(datasets=ds("ACP_DAM"), filters=FILTERS)
    assert group_of(pipeline)["_id"][base.ID_ENTITY] == "$px_name"


def test_exchange_unions_the_historical_twin_with_the_same_match():
    pipeline = EXCHANGE.build_pipeline(datasets=ds("ACP_DAM"), filters=FILTERS)
    unions = stages(pipeline, "$unionWith")
    assert len(unions) == 1
    union = unions[0]["$unionWith"]
    assert union["coll"] == "exchange_serve_historical"
    assert union["pipeline"] == [{"$match": pipeline[0]["$match"]}]


def test_exchange_filter_restricts_the_exchange_list():
    filters = dict(FILTERS, exchanges=["IEX", "PXIL"])
    pipeline = EXCHANGE.build_pipeline(datasets=ds("ACP_DAM"), filters=filters)
    assert pipeline[0]["$match"]["px_name"] == {"$in": ["IEX", "PXIL"]}


def test_an_uncleared_hpdam_block_is_none_never_zero():
    # HPDAM clears in ~0.6% of blocks. A block with no HPDAM document leaves $avg
    # null and $sum at 0 — the null guard is what keeps the sum honest.
    datasets = ds("ACP_HPDAM", "ACV_HPDAM")
    doc = {
        "_id": {base.ID_DATE: "2026-08-04", base.ID_BLOCK: "5",
                base.ID_ENTITY: "HPX"},
        "ACP_HPDAM": None,
        "ACV_HPDAM": 0,
        base.NULL_GUARD_PREFIX + "ACV_HPDAM": 0,
    }
    rows = EXCHANGE.rows_from([doc], datasets)
    assert [r["value"] for r in rows] == [None, None]
    assert [r["entity"] for r in rows] == ["HPX", "HPX"]


def test_a_genuine_zero_survives_the_null_guard():
    datasets = ds("ACV_HPDAM")
    doc = {
        "_id": {base.ID_DATE: "2026-08-04", base.ID_BLOCK: "5",
                base.ID_ENTITY: "HPX"},
        "ACV_HPDAM": 0.0,
        base.NULL_GUARD_PREFIX + "ACV_HPDAM": 96,
    }
    assert EXCHANGE.rows_from([doc], datasets)[0]["value"] == 0.0


def test_summed_exchange_datasets_carry_a_null_guard():
    group = group_of(EXCHANGE.build_pipeline(
        datasets=ds("ACV_DAM", "MKT_BUY_BID_DAM"), filters=FILTERS))
    assert base.NULL_GUARD_PREFIX + "ACV_DAM" in group
    assert base.NULL_GUARD_PREFIX + "MKT_BUY_BID_DAM" in group


def test_averaged_exchange_datasets_carry_no_null_guard():
    group = group_of(EXCHANGE.build_pipeline(datasets=ds("ACP_DAM"), filters=FILTERS))
    assert base.NULL_GUARD_PREFIX + "ACP_DAM" not in group


# --------------------------------------------------------------------- trade

def test_trade_adapter_declares_its_collections_and_entity():
    assert TRADE.group == "trade"
    assert TRADE.serve_collection == "bid_trade_serve"
    assert TRADE.historical_collection == "bid_trade_serve_historical"
    assert TRADE.entity_field() == "exchange"


def test_trade_adapter_is_registered_for_its_group():
    assert isinstance(base.get_adapter("trade"), trade.TradeAdapter)


def test_registering_a_second_trade_adapter_raises():
    with pytest.raises(ValueError):
        base.register(trade.TradeAdapter)


@pytest.mark.parametrize("stored,expected", [
    ("B", "Buy"),
    ("Buy", "Buy"),
    ("BUY", "Buy"),
    ("buy", "Buy"),
    ("S", "Sell"),
    ("Sell", "Sell"),
    (" s ", "Sell"),
])
def test_every_stored_flag_normalises(stored, expected):
    assert trade.normalise_direction(stored) == expected


@pytest.mark.parametrize("empty", [None, ""])
def test_an_absent_direction_filter_is_not_a_direction(empty):
    assert trade.normalise_direction(empty) is None


def test_an_unknown_direction_raises():
    with pytest.raises(ValueError):
        trade.normalise_direction("both")


def test_direction_expr_is_generated_from_the_same_alias_table():
    # The expression and normalise_direction must not drift: one alias table.
    branches = trade.direction_expr()["$switch"]["branches"]
    mapped = {}
    for branch in branches:
        aliases = branch["case"]["$in"][1]
        for alias in aliases:
            mapped[alias] = branch["then"]
    assert mapped == trade.DIRECTION_ALIASES
    assert trade.direction_expr()["$switch"]["default"] is None


def test_direction_expr_reads_the_raw_flag_field():
    assert "buy_sell_flag" in text(trade.direction_expr())


def test_trade_normalises_the_flag_before_grouping():
    pipeline = TRADE.build_pipeline(datasets=ds("PF_TRADED_DAM"), filters=FILTERS)
    keys = [k for s in pipeline for k in s]
    # After the union so one stage normalises both collections, and before the
    # analytics $group — which is the LAST one; the first is the archival dedupe.
    assert keys.index("$set") > keys.index("$unionWith")
    assert keys.index("$set") < len(keys) - 1 - keys[::-1].index("$group")
    assert pipeline[keys.index("$set")]["$set"] == {
        trade.DIRECTION_FIELD: trade.direction_expr()}


def test_a_direction_filter_matches_on_the_normalised_field():
    filters = dict(FILTERS, direction="S")
    pipeline = TRADE.build_pipeline(datasets=ds("PF_TRADED_DAM"), filters=filters)
    keys = [k for s in pipeline for k in s]
    # the direction $match sits after the $set, never in the indexed prefilter
    assert keys.index("$set") < keys.index("$match", keys.index("$set"))
    assert {"$match": {trade.DIRECTION_FIELD: "Sell"}} in pipeline
    assert trade.DIRECTION_FIELD not in pipeline[0]["$match"]


def test_no_direction_filter_adds_no_second_match():
    pipeline = TRADE.build_pipeline(datasets=ds("PF_TRADED_DAM"), filters=FILTERS)
    assert len(stages(pipeline, "$match")) == 1


def test_an_unknown_direction_filter_raises_rather_than_returning_everything():
    filters = dict(FILTERS, direction="long")
    with pytest.raises(ValueError):
        TRADE.build_pipeline(datasets=ds("PF_TRADED_DAM"), filters=filters)


def test_trade_unions_the_historical_twin():
    pipeline = TRADE.build_pipeline(datasets=ds("PF_TRADED_DAM"), filters=FILTERS)
    unions = stages(pipeline, "$unionWith")
    assert len(unions) == 1
    assert unions[0]["$unionWith"]["coll"] == "bid_trade_serve_historical"


def test_trade_markets_share_one_pipeline_with_one_accumulator_each():
    codes = ["PF_TRADED_DAM", "PF_BIDDED_DAM", "PF_TRADED_RTM", "PF_BIDDED_RTM"]
    pipeline = TRADE.build_pipeline(datasets=ds(*codes), filters=FILTERS)
    assert len(stages(pipeline, "$match")) == 1
    assert len(stages(pipeline, "$unionWith")) == 1
    assert value_fields(group_of(pipeline)) == codes
    assert "type" not in pipeline[0]["$match"]


def test_each_trade_accumulator_carries_its_own_market_guard():
    group = group_of(TRADE.build_pipeline(
        datasets=ds("PF_TRADED_DAM", "PF_TRADED_RTM"), filters=FILTERS))
    for code, market in (("PF_TRADED_DAM", "DAM"), ("PF_TRADED_RTM", "RTM")):
        cond = group[code]["$sum"]["$cond"]
        assert cond[0] == {"$and": [{"$eq": ["$type", market]}]}
        assert cond[1] == "$traded"
        assert cond[2] is None


def test_portfolio_datasets_are_not_split_per_exchange():
    # PF_* are scalar series: bid_trade_serve is IEX-only today, and keying the
    # group on `exchange` would turn one series into one per exchange later.
    pipeline = TRADE.build_pipeline(datasets=ds("PF_TRADED_DAM"), filters=FILTERS)
    assert base.ID_ENTITY not in group_of(pipeline)["_id"]


def test_exchange_filter_still_restricts_the_trade_query():
    filters = dict(FILTERS, exchanges=["IEX"])
    pipeline = TRADE.build_pipeline(datasets=ds("PF_TRADED_DAM"), filters=filters)
    assert pipeline[0]["$match"]["exchange"] == {"$in": ["IEX"]}


def test_an_empty_market_still_builds_a_pipeline():
    # PF_*_GDAM is availability="empty": the collection has no GDAM rows yet, but
    # the query is legitimate and must come back as "no data in range", not as an
    # error the way an `absent` dataset does.
    assert registry.get("PF_TRADED_GDAM").availability == "empty"
    pipeline = TRADE.build_pipeline(datasets=ds("PF_TRADED_GDAM"), filters=FILTERS)
    assert "PF_TRADED_GDAM" in group_of(pipeline)


def test_an_absent_trade_dataset_is_refused():
    with pytest.raises(ValueError):
        TRADE.build_pipeline(datasets=ds("TAM_TRADED"), filters=FILTERS)


def test_trade_rows_preserve_a_negative_traded_volume():
    # Sell rows are stored negative (traded -14.1), so the portfolio sum is a net
    # position. Clamping it at zero would hide every sell.
    doc = {
        "_id": {base.ID_DATE: "2026-08-30", base.ID_BLOCK: "3"},
        "PF_TRADED_DAM": -14.1,
        base.NULL_GUARD_PREFIX + "PF_TRADED_DAM": 1,
    }
    rows = TRADE.rows_from([doc], ds("PF_TRADED_DAM"))
    assert rows[0]["value"] == -14.1
    assert rows[0]["entity"] is None


def test_trade_rows_report_an_unmatched_sum_as_none():
    doc = {
        "_id": {base.ID_DATE: "2026-08-30", base.ID_BLOCK: "3"},
        "PF_TRADED_RTM": 0,
        base.NULL_GUARD_PREFIX + "PF_TRADED_RTM": 0,
    }
    assert TRADE.rows_from([doc], ds("PF_TRADED_RTM"))[0]["value"] is None
