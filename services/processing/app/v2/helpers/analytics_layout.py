"""The one table both export writers emit.

PTC asked for a single table on 2026-09-20, replacing the sheet-per-dataset workbook
and the flat `dataset,label,unit,date,block,value` CSV. One row per (day, block); the
identity columns the chosen Group Date By calls for on the left; one column per
series on the right:

    Week      Date         Time block   Actual Demand   Drawal   Plant wise DC · KKNP
    W38 2026  2026-09-14            1         4821.30  4790.10                 475.33
    W38 2026  2026-09-14            2         4805.70  4788.40                 475.08

A "series" is a dataset plus its entity, so one ticked Plant wise DC becomes 34
columns and one ticked DAM ACP becomes three. That is the point of the single table:
the columns line up on the same row, which is what makes them comparable at all. The
old workbook put each on its own sheet, where comparing two meant copying one.

Rows run date-major, block-minor — every block of 14 Sep, then every block of 15 Sep —
because that is the order a trading day is read in.

**A missing value is an empty cell**, never `0` and never the string `"None"`. The
engine keeps "nothing was scheduled" and "zero was scheduled" apart the whole way
down; collapsing them here would put a wrong number in front of a trader at the last
possible moment.
"""
from typing import Any, Dict, Iterator, List, Optional, Sequence

from ..analytics import block_bands, grouping

#: Header used when a response carries no grouping at all (an empty result, or a
#: caller that never set one). Matches the request model's default.
DEFAULT_GROUPING = grouping.BLOCK_DATE_WISE


def grouping_of(response: Optional[Dict[str, Any]],
                filters: Optional[Dict[str, Any]] = None) -> str:
    """The grouping the file was actually built with.

    `meta` wins over `filters` deliberately. Until 2026-09-20 a ticked weekly
    dataset rewrote the grouping for the whole request while the Filters sheet went
    on printing what the user had asked for, so a workbook of week columns carried
    the line `Date grouping: BLOCK_WISE`. Nothing forces the grouping any more, but
    reading the answer rather than the question is the habit that kept that wrong.
    """
    meta = (response or {}).get("meta") or {}
    for source in (meta.get("date_grouping"), (filters or {}).get("date_grouping")):
        if source:
            try:
                return grouping.canonical(source)
            except ValueError:
                continue
    return DEFAULT_GROUPING


def block_label(block: Any) -> Any:
    """A block number passes through; a band key becomes the band's own name.

    When the user groups blocks by Night Off-Peak, the Time block column says
    "Night Off-Peak" — the thing they chose — rather than `NIGHT_OFF_PEAK` or a
    block range they never typed. A CUSTOM key is already `"lo-hi"` and reads fine.
    """
    band = block_bands.DEFAULT_BANDS.get(block) if isinstance(block, str) else None
    return band.label if band else block


def column_label(series: Dict[str, Any]) -> str:
    """`"Plant wise DC · KKNP (MW)"` — dataset, entity, unit.

    The entity is part of the identity of the column, not a decoration: two plants
    of the same dataset must never share a heading in a table where they sit side
    by side.
    """
    base = str(series.get("label") or series.get("dataset") or "")
    entity = series.get("entity_label") or series.get("entity")
    unit = str(series.get("unit") or "").strip()
    label = f"{base} · {entity}" if entity not in (None, "") else base
    return f"{label} ({unit})" if unit else label


def series_list(response: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The response's series, in request order — one per table column."""
    return list((response or {}).get("series") or ())


def column_titles(response: Optional[Dict[str, Any]],
                  filters: Optional[Dict[str, Any]] = None) -> List[str]:
    """Identity headings for this grouping, then one heading per series."""
    return (list(grouping.identity_columns(grouping_of(response, filters)))
            + [column_label(s) for s in series_list(response)])


def _value_at(series: Dict[str, Any], block_index: int, date_index: int) -> Any:
    """`values` is indexed `[block][date]`; a short row is a gap, not an error."""
    matrix = series.get("values") or ()
    if block_index >= len(matrix):
        return None
    row = matrix[block_index] or ()
    return row[date_index] if date_index < len(row) else None


def data_rows(response: Optional[Dict[str, Any]],
              filters: Optional[Dict[str, Any]] = None) -> Iterator[List[Any]]:
    """One row per (day, block), lazily.

    Nothing here builds a list of rows or joins the output: the streaming CSV tier
    depends on this staying a generator, and a long range is hundreds of thousands
    of rows.
    """
    response = response or {}
    axes = response.get("axes") or {}
    dates: Sequence[Any] = list(axes.get("dates") or ())
    blocks: Sequence[Any] = list(axes.get("blocks") or ())
    series = series_list(response)
    code = grouping_of(response, filters)

    for di, day in enumerate(dates):
        for bi, block in enumerate(blocks):
            identity = grouping.identity_values(code, day, block_label(block))
            yield identity + [_value_at(s, bi, di) for s in series]


def row_count(response: Optional[Dict[str, Any]]) -> int:
    """Rows the table will hold — days x block buckets. No data is touched."""
    axes = (response or {}).get("axes") or {}
    return len(axes.get("dates") or ()) * len(axes.get("blocks") or ())
