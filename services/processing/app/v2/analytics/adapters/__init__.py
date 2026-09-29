"""Source group adapters.

One adapter per MongoDB source group. The planner collapses every ticked dataset
onto its group and asks that group's adapter for a single aggregation pipeline, so
twelve datasets across four groups cost four queries rather than twelve.

Each concrete adapter registers itself with `base.register()` at import time, so the
imports below are what populates `base.ADAPTERS`. They are not decorative: without
them `base.get_adapter("demand")` raises `KeyError` and every analytics query fails,
which is why the planner imports this package rather than `base` directly.
"""
from . import base  # noqa: F401  (imported first: the others register against it)
from . import demand, dsm_published, exchange, peak, supply, trade  # noqa: F401

__all__ = ["base", "demand", "dsm_published", "exchange", "peak", "supply", "trade"]
