"""The two export writers: streaming CSV and write-only XLSX.

Both of these exist because an export can be far larger than the dashboard in view.
Three properties are therefore asserted here rather than assumed:

1. **`csv_rows` is a generator.** The streaming response and the chunked export
   worker share it, and neither may hold the whole file in memory. The test proves
   this by handing it a `series` source that *raises* if it is fully consumed, then
   pulling the first lines out anyway.

2. **A missing value is an empty cell.** Never `0`, never the string `"None"`. The
   docx is explicit and so is the pivot: "nothing was scheduled for this block" and
   "zero was scheduled" are different facts, and a spreadsheet that renders the
   first as the second is a wrong number in front of a trader.

3. **Excel's limits are respected before openpyxl enforces them.** Sheet titles are
   capped at 31 characters and de-duplicated by us, deterministically, rather than
   letting openpyxl append its own suffix (which can itself exceed 31).
"""
import csv as csv_module
import inspect
import io
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.helpers import analytics_csv  # noqa: E402
from services.processing.app.v2.helpers import analytics_xlsx  # noqa: E402


FILTERS = {
    "datasets": ["ACTUAL_DEMAND", "DC_PLANT"],
    "from_date": "2026-04-01",
    "to_date": "2026-08-31",
    "from_block": 1,
    "to_block": 96,
    "date_grouping": "DATE_WISE",
    "block_grouping": "INDIVIDUAL",
    "plants": ["PLANT_A", "PLANT_B"],
    "exchanges": ["IEX"],
    "format": "csv",
}


def _series(dataset, label, values, entity=None, unit="MW", quality="complete"):
    return {"dataset": dataset, "label": label, "unit": unit, "entity": entity,
            "values": values, "quality": quality}


def _response(series, dates=("2026-04-01", "2026-04-02"), blocks=(1, 2)):
    return {
        "axes": {"dates": list(dates), "blocks": list(blocks)},
        "series": list(series),
        "meta": {"generated_at": "2026-09-17T06:00:00+00:00", "truncated": False,
                 "missing_datasets": [], "failed_datasets": []},
    }


def _lines(response, filters=None):
    return [line.rstrip("\n") for line in analytics_csv.csv_rows(response, filters or FILTERS)]


def _comments(lines):
    return [ln for ln in lines if ln.startswith("#")]


def _table(lines):
    body = [ln for ln in lines if not ln.startswith("#")]
    return list(csv_module.reader(io.StringIO("\n".join(body))))


class _ExplodingSeries:
    """Iterable that yields one series, then refuses to be fully consumed."""

    def __init__(self, first):
        self.first = first

    def __iter__(self):
        yield self.first
        raise RuntimeError("csv_rows materialised the whole response")


# ------------------------------------------------------------------ streaming

def test_csv_rows_is_a_generator():
    assert inspect.isgeneratorfunction(analytics_csv.csv_rows)
    rows = analytics_csv.csv_rows(_response([_series("ACTUAL_DEMAND", "Actual Demand",
                                                     [[1.0, 2.0], [3.0, 4.0]])]), FILTERS)
    assert inspect.isgenerator(rows)


def test_csv_rows_yields_its_first_lines_without_consuming_the_whole_response():
    one = _series("ACTUAL_DEMAND", "Actual Demand", [[1.0, 2.0], [3.0, 4.0]])
    response = _response([])
    response["series"] = _ExplodingSeries(one)

    rows = analytics_csv.csv_rows(response, FILTERS)

    first = next(rows)
    assert first.startswith("#"), first

    # Header block, column header and the first series' own cells all arrive before
    # the source blows up: nothing is buffered ahead of what was asked for.
    pulled = [first]
    for _ in range(50):
        pulled.append(next(rows))
        if pulled[-1].startswith("ACTUAL_DEMAND,"):
            break
    else:
        raise AssertionError(f"no data row was yielded lazily: {pulled}")

    with pytest.raises(RuntimeError):
        list(rows)


def test_every_line_is_newline_terminated_so_it_can_be_streamed_as_is():
    response = _response([_series("ACTUAL_DEMAND", "Actual Demand", [[1.0, 2.0], [3.0, 4.0]])])
    assert all(line.endswith("\n") for line in analytics_csv.csv_rows(response, FILTERS))


# --------------------------------------------------------------- header block

def test_header_block_records_the_applied_filters():
    lines = _lines(_response([_series("ACTUAL_DEMAND", "Actual Demand",
                                      [[1.0, 2.0], [3.0, 4.0]])]))
    header = "\n".join(_comments(lines))

    assert "2026-04-01" in header and "2026-08-31" in header      # date range
    assert "1" in header and "96" in header                        # block range
    assert "DATE_WISE" in header                                   # date grouping
    assert "INDIVIDUAL" in header                                  # block grouping
    assert "ACTUAL_DEMAND" in header and "DC_PLANT" in header      # dataset list
    assert "PLANT_A" in header                                     # plant selection
    assert "2026-09-17T06:00:00+00:00" in header                   # generated_at


def test_header_block_records_custom_block_ranges_when_they_apply():
    filters = dict(FILTERS, block_grouping="CUSTOM", custom_blocks=[[1, 8], [9, 16]])
    header = "\n".join(_comments(_lines(_response([
        _series("ACTUAL_DEMAND", "Actual Demand", [[1.0, 2.0], [3.0, 4.0]])]), filters)))
    assert "CUSTOM" in header
    assert "1-8" in header and "9-16" in header


def test_column_header_follows_the_filter_block():
    lines = _lines(_response([_series("ACTUAL_DEMAND", "Actual Demand",
                                      [[1.0, 2.0], [3.0, 4.0]])]))
    table = _table(lines)
    assert table[0] == list(analytics_csv.COLUMNS)
    assert "dataset" in table[0] and "entity" in table[0]


# ------------------------------------------------------------------ the cells

def test_a_missing_value_is_an_empty_cell_never_zero_and_never_the_string_none():
    #                block 1: [d1 missing, d2 real zero]
    values = [[None, 0.0], [2850.0, None]]
    lines = _lines(_response([_series("ACTUAL_DEMAND", "Actual Demand", values)]))
    table = _table(lines)
    head, body = table[0], table[1:]
    vi, bi, di = head.index("value"), head.index("block"), head.index("date")

    cells = {(r[bi], r[di]): r[vi] for r in body}
    assert cells[("1", "2026-04-01")] == ""            # missing
    assert cells[("2", "2026-04-02")] == ""            # missing
    assert "None" not in [r[vi] for r in body]
    assert float(cells[("1", "2026-04-02")]) == 0.0    # a real zero survives
    assert float(cells[("2", "2026-04-01")]) == 2850.0


def test_entity_is_its_own_column_for_per_plant_and_per_exchange_series():
    values = [[1.0, 2.0], [3.0, 4.0]]
    response = _response([
        _series("DC_PLANT", "Plant wise DC", values, entity="PLANT_A"),
        _series("DC_PLANT", "Plant wise DC", values, entity="PLANT_B"),
        _series("ACP_DAM", "DAM ACP", values, entity="IEX", unit="Rs/MWh"),
        _series("ACTUAL_DEMAND", "Actual Demand", values),
    ])
    table = _table(_lines(response))
    head, body = table[0], table[1:]
    ei, dsi = head.index("entity"), head.index("dataset")

    assert {r[ei] for r in body if r[dsi] == "DC_PLANT"} == {"PLANT_A", "PLANT_B"}
    assert {r[ei] for r in body if r[dsi] == "ACP_DAM"} == {"IEX"}
    # a portfolio-wide series has no entity, and that is an empty cell too
    assert {r[ei] for r in body if r[dsi] == "ACTUAL_DEMAND"} == {""}


def test_a_label_containing_a_comma_is_quoted_not_split():
    response = _response([_series("ACP_DAM", "DAM ACP, weighted", [[1.0, 2.0], [3.0, 4.0]],
                                  unit="Rs/MWh")])
    table = _table(_lines(response))
    li = table[0].index("label")
    assert table[1][li] == "DAM ACP, weighted"
    assert all(len(r) == len(table[0]) for r in table)


# ----------------------------------------------------------------------- xlsx

def _sheets(path):
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _at(row, index):
    """Cell value, tolerating openpyxl trimming trailing blanks off a stored row."""
    return row[index] if index < len(row) else None


def _rows_of(path, sheet):
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        return [list(r) for r in wb[sheet].iter_rows(values_only=True)]
    finally:
        wb.close()


def test_xlsx_has_one_sheet_per_dataset_plus_a_filters_sheet(tmp_path):
    values = [[1.0, 2.0], [3.0, 4.0]]
    response = _response([
        _series("ACTUAL_DEMAND", "Actual Demand", values),
        _series("DC_PLANT", "Plant wise DC", values, entity="PLANT_A"),
        _series("DC_PLANT", "Plant wise DC", values, entity="PLANT_B"),
    ])
    out = tmp_path / "export.xlsx"
    result = analytics_xlsx.write_xlsx(response, FILTERS, str(out))

    names = _sheets(str(out))
    assert analytics_xlsx.FILTERS_SHEET in names
    # two datasets -> two data sheets; the two plant entities share DC_PLANT's sheet
    assert len(names) == 3
    assert result["path"] == str(out)
    assert result["bytes"] == out.stat().st_size > 0
    assert result["row_count"] == 6      # 2 blocks x (1 + 2 entity series)


def test_filters_sheet_records_the_applied_filters(tmp_path):
    out = tmp_path / "export.xlsx"
    analytics_xlsx.write_xlsx(
        _response([_series("ACTUAL_DEMAND", "Actual Demand", [[1.0, 2.0], [3.0, 4.0]])]),
        FILTERS, str(out))

    text = "\n".join(
        " ".join("" if c is None else str(c) for c in row)
        for row in _rows_of(str(out), analytics_xlsx.FILTERS_SHEET))
    for expected in ("2026-04-01", "2026-08-31", "96", "DATE_WISE", "INDIVIDUAL",
                     "ACTUAL_DEMAND", "2026-09-17T06:00:00+00:00"):
        assert expected in text, f"{expected!r} missing from Filters sheet:\n{text}"


def test_sheet_names_are_truncated_to_31_characters_and_deduplicated(tmp_path):
    long_a = "Intra Day Forecast MAPE Northern Region A"
    long_b = "Intra Day Forecast MAPE Northern Region B"
    assert long_a[:31] == long_b[:31]
    values = [[1.0, 2.0], [3.0, 4.0]]
    response = _response([
        _series("MAPE_IDF_A", long_a, values),
        _series("MAPE_IDF_B", long_b, values),
    ])
    out = tmp_path / "export.xlsx"
    analytics_xlsx.write_xlsx(response, FILTERS, str(out))

    data_sheets = [n for n in _sheets(str(out)) if n != analytics_xlsx.FILTERS_SHEET]
    assert len(data_sheets) == 2
    assert len(set(data_sheets)) == 2, data_sheets
    assert all(len(n) <= 31 for n in data_sheets), data_sheets


def test_xlsx_missing_value_is_an_empty_cell_never_zero(tmp_path):
    values = [[None, 0.0], [2850.0, None]]
    out = tmp_path / "export.xlsx"
    analytics_xlsx.write_xlsx(
        _response([_series("ACTUAL_DEMAND", "Actual Demand", values)]), FILTERS, str(out))

    sheet = [n for n in _sheets(str(out)) if n != analytics_xlsx.FILTERS_SHEET][0]
    rows = _rows_of(str(out), sheet)
    head, body = rows[0], rows[1:]
    d1, d2 = head.index("2026-04-01"), head.index("2026-04-02")

    assert _at(body[0], d1) is None     # missing, not 0 and not "None"
    assert _at(body[0], d2) == 0.0      # a real zero survives
    assert _at(body[1], d1) == 2850.0
    # A trailing blank is not stored in the sheet at all, which is the strongest
    # possible form of "empty": Excel shows no value, and no 0 was ever written.
    assert _at(body[1], d2) is None
    assert all(c != "None" for row in body for c in row)


def test_write_xlsx_creates_a_missing_export_directory(tmp_path):
    out = tmp_path / "exports" / "nested" / "export.xlsx"
    assert not out.parent.exists()
    analytics_xlsx.write_xlsx(
        _response([_series("ACTUAL_DEMAND", "Actual Demand", [[1.0, 2.0], [3.0, 4.0]])]),
        FILTERS, str(out))
    assert out.exists()


def test_write_xlsx_reports_an_unwritable_directory_clearly(tmp_path):
    blocker = tmp_path / "exports"
    blocker.write_text("not a directory")           # makedirs cannot win here
    out = blocker / "export.xlsx"
    with pytest.raises(analytics_xlsx.ExportWriteError) as exc:
        analytics_xlsx.write_xlsx(
            _response([_series("ACTUAL_DEMAND", "Actual Demand", [[1.0, 2.0], [3.0, 4.0]])]),
            FILTERS, str(out))
    assert str(blocker) in str(exc.value)
    assert "ANALYTICS_EXPORT_DIR" in str(exc.value)


# ------------------------------------------------------------------- filename

def test_filename_for_embeds_the_module_name_and_the_date_range():
    name = analytics_csv.filename_for(FILTERS, "csv")
    assert name.endswith(".csv")
    assert "analytics" in name
    assert "2026-04-01" in name and "2026-08-31" in name
    assert " " not in name


def test_filename_for_uses_the_module_when_the_filters_name_one():
    name = analytics_csv.filename_for(dict(FILTERS, module="Demand Analytics"), "xlsx")
    assert name.endswith(".xlsx")
    assert "demand_analytics" in name


def test_filename_for_rejects_an_unsupported_format():
    with pytest.raises(ValueError):
        analytics_csv.filename_for(FILTERS, "pdf")


def test_xlsx_reuses_the_same_filename_helper():
    assert analytics_xlsx.filename_for is analytics_csv.filename_for
