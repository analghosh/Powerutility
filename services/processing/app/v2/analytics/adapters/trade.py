"""Trade source group — PTC's own portfolio, bidded and traded.

`bid_trade_serve` ∪ `bid_trade_serve_historical` is what *we* bid and what cleared,
as opposed to the exchange group's market-wide numbers. It carries DAM and RTM only
(the GDAM/HPDAM datasets are registered `empty`, not `absent`: the query is
legitimate, there are simply no rows yet) and, locally, IEX only.

The group-specific fact is the flag vocabulary. `buy_sell_flag` holds `'B'`, `'Buy'`
and `'S'` for two concepts — measured locally in `bid_trade_serve_historical`:
410 `'S'`, 96 `'Buy'`, 28 `'B'`. Anything keyed or filtered on the raw flag splits
one direction in two and silently under-reports it, so the adapter normalises the
flag into a computed `direction` field before the `$group` and filters on that.

Sell rows are stored with a **negative** `bidded`/`traded` (verified: -14.1 MW), so
with no direction filter the sum is a net position rather than a double count. A
caller that wants one side passes `direction="Buy"` / `"Sell"` (or the raw `'B'` /
`'S'` — they normalise the same way).
"""
from typing import List, Optional, Sequence

from ..registry import Dataset
from . import base

#: Raw field as stored.
FLAG_FIELD = "buy_sell_flag"
#: Computed field the pipeline groups and filters on.
DIRECTION_FIELD = "direction"

BUY = "Buy"
SELL = "Sell"

#: Every spelling seen in the data, upper-cased, mapped to its canonical value.
#: `direction_expr()` is generated from this table so the Mongo expression and the
#: Python filter normalisation cannot drift apart.
DIRECTION_ALIASES = {
    "B": BUY,
    "BUY": BUY,
    "S": SELL,
    "SELL": SELL,
}


def normalise_direction(value) -> Optional[str]:
    """Canonicalise a caller-supplied direction filter, or None when unset.

    An unrecognised direction raises rather than being dropped: silently ignoring
    it would return both sides and read as a working filter.
    """
    if value is None:
        return None
    key = str(value).strip().upper()
    if not key:
        return None
    try:
        return DIRECTION_ALIASES[key]
    except KeyError:
        raise ValueError(
            f"Unknown trade direction: {value!r}; expected one of "
            f"{sorted(set(DIRECTION_ALIASES.values()))}"
        ) from None


def direction_expr() -> dict:
    """Mongo expression normalising `buy_sell_flag` to Buy / Sell.

    `$toString` + `$ifNull` make the comparison total: a missing, null or
    non-string flag becomes "", falls through every branch and keys to None rather
    than erroring mid-pipeline or landing in the wrong direction.
    """
    flag = {"$toUpper": {"$ifNull": [{"$toString": f"${FLAG_FIELD}"}, ""]}}
    branches = []
    for canonical in (BUY, SELL):
        aliases = sorted(a for a, c in DIRECTION_ALIASES.items() if c == canonical)
        branches.append({"case": {"$in": [flag, aliases]}, "then": canonical})
    return {"$switch": {"branches": branches, "default": None}}


class TradeAdapter(base.SourceGroupAdapter):
    """Portfolio bidded/traded, with the flag vocabulary normalised."""

    group = "trade"
    serve_collection = "bid_trade_serve"
    historical_collection = "bid_trade_serve_historical"
    entity = "exchange"
    # The scheduler archives on (scheduled_at, time_block, type, group, bid_type);
    # `exchange` and `buy_sell_flag` are carried as well because this adapter
    # reports per exchange and per direction, and a key that is finer than the
    # natural one is always safe while a coarser one would merge a Buy row into a
    # Sell row and lose it. Without the dedupe the portfolio traded volume read
    # 44 MW against a stored 22 on the overlap day.
    dedupe_key = ("scheduled_at", "time_block", "type", "exchange",
                  "buy_sell_flag", "group", "bid_type")

    def build_pipeline(self, *, datasets: Sequence[Dataset], filters: dict,
                       ctx: Optional[dict] = None) -> List[dict]:
        stages = self._default_pipeline(datasets=datasets, filters=filters, ctx=ctx)
        group = stages.pop()  # the analytics $group, always last

        # After the $unionWith so both collections are normalised by one stage, and
        # before the $group so the direction filter applies to the raw documents.
        stages.append({"$set": {DIRECTION_FIELD: direction_expr()}})

        direction = normalise_direction(
            filters.get(DIRECTION_FIELD, filters.get("buy_sell")))
        if direction:
            stages.append({"$match": {DIRECTION_FIELD: direction}})

        stages.append(group)
        return stages


base.register(TradeAdapter)
