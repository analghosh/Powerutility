"""Exchange source group — market-wide ACP, ACV and bid volumes, per exchange.

`exchange_serve` ∪ `exchange_serve_historical` holds one document per
(trading date, block, market, exchange) for four markets (DAM, RTM, GDAM, HPDAM)
and three exchanges (IEX, PXIL, HPX). Every dataset here is `per_exchange`, so the
`$group` `_id` carries `px_name` and IEX, PXIL and HPX come back as separate series
rather than averaged into one meaningless national number.

Two group-specific facts the base class cannot know:

1. **`acv` is not one shape.** For GDAM it is a sub-document — `total` plus a
   renewable breakdown (`hydro`, `wind`, `other_re`, `dre` from IEX, `solar`,
   `non_solar`, `hydro` from HPX) — and for every other market it is a scalar. The
   registry therefore stores `$acv.total` as GDAM's value. But the GDAM rows
   themselves are *mixed*: measured locally, `exchange_serve` holds 384 object and
   288 scalar GDAM documents, and the historical twin 4,704 object / 3,168 scalar.
   A bare `$acv.total` resolves to missing on a scalar document, so ~40% of GDAM
   volume would quietly read as "no data". `acv_expr()` branches on the stored type
   instead, which costs nothing and cannot be wrong either way.

2. **HPDAM is legitimately sparse.** High-Price DAM clears in roughly 0.6% of
   blocks. A block with no HPDAM document must render `None`, never `0` — `$avg`
   gives that for free, and the base class's null guard gives it for the summed
   datasets. Nothing here rewrites a *stored* zero: a zero the exchange published
   is the exchange's own number, and turning it into "no data" would be a second
   lie in the opposite direction.
"""
from dataclasses import replace
from typing import List, Optional, Sequence

from ..registry import Dataset
from . import base

#: What the registry stores as GDAM's ACV value, and the marker for the shape fix.
GDAM_ACV_PATH = "$acv.total"

#: The field whose shape varies by market.
ACV_FIELD = "acv"


def acv_expr() -> dict:
    """Read `acv` whichever way this document happens to store it.

    Object -> the `total` of the renewable breakdown; scalar -> the number itself;
    missing -> missing, which the accumulators already treat as no value.
    """
    return {
        "$cond": [
            {"$eq": [{"$type": f"${ACV_FIELD}"}, "object"]},
            GDAM_ACV_PATH,
            f"${ACV_FIELD}",
        ]
    }


def shaped(dataset: Dataset) -> Dataset:
    """Swap the GDAM ACV path for the shape-tolerant expression.

    Returning a `Dataset` rather than an expression keeps every piece of base
    machinery — the per-dataset `$cond` discriminator, the accumulator, the null
    guard, the hoisted common match — working verbatim on the same codes. The
    `value` field carries an expression instead of a field path for this one
    dataset; `base.value_expr` handles both.
    """
    if dataset.value != GDAM_ACV_PATH:
        return dataset
    return replace(dataset, value=acv_expr())


class ExchangeAdapter(base.SourceGroupAdapter):
    """ACP / ACV / market bid volumes, one pipeline for all four markets."""

    group = "exchange"
    serve_collection = "exchange_serve"
    historical_collection = "exchange_serve_historical"
    entity = "px_name"
    # `idx_unique_exchange_key` on the serve collection, and the same tuple the
    # scheduler's archival dedupes on. Verified live: on the overlap day both
    # collections held 671 documents, key for key, so without this the summed
    # datasets (ACV, buy/sell bid) read exactly double.
    dedupe_key = ("scheduled_at", "time_block", "market_type", "px_name")

    def build_pipeline(self, *, datasets: Sequence[Dataset], filters: dict,
                       ctx: Optional[dict] = None) -> List[dict]:
        return self._default_pipeline(
            datasets=[shaped(d) for d in datasets], filters=filters, ctx=ctx)


base.register(ExchangeAdapter)
