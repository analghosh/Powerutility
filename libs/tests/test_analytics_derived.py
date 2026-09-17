"""Derived datasets, resolved in Python after the source groups return.

`OD_UD_QUANTUM` is the only derived dataset PTC asked for today: drawal minus
schedule, both fields on the same `demand_serve` document, so there is no join to
get wrong. What there *is* to get wrong is the arithmetic around absence:

* A block where one side never arrived must read `None`, not `drawal - 0`. Treating
  an absent schedule as zero would report the entire drawal as over-drawal, and the
  DSM money column reads off that number.
* A genuine `0` (drawal exactly equal to schedule) must survive as `0`, because that
  is the one block that actually settled clean.
* A negative result is not an error — under-drawal is legitimately negative. The
  verified real sample is drawal 533.96 - schedule 535.44 = -1.48.

And the dependency rows themselves are an artefact of the query, not of the user's
selection: ticking OD/UD Quantum makes the executor fetch Drawal, but the user never
asked to see a Drawal column, so it must not appear in the table or the export.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import derived  # noqa: E402
from services.processing.app.v2.analytics import registry  # noqa: E402


QUANTUM = "OD_UD_QUANTUM"
DRAWAL = "DRAWAL"
SCHEDULE = "FINAL_SCHEDULE_STATE"


def _row(dataset, value, date_key="2026-08-26", block_key="81", entity=None):
    return {
        "dataset": dataset,
        "date_key": date_key,
        "block_key": block_key,
        "entity": entity,
        "value": value,
    }


def _by_dataset(rows, code):
    return [r for r in rows if r["dataset"] == code]


def _one(rows, code):
    hits = _by_dataset(rows, code)
    assert len(hits) == 1, f"expected exactly one {code} row, got {len(hits)}"
    return hits[0]


# ----------------------------------------------------------------- arithmetic

def test_quantum_is_drawal_minus_schedule():
    rows = [_row(DRAWAL, 533.96), _row(SCHEDULE, 535.44)]
    out = derived.resolve(rows, [QUANTUM])
    assert _one(out, QUANTUM)["value"] == pytest.approx(-1.48)


def test_negative_result_is_preserved_not_clamped():
    """Under-drawal is legitimately negative; abs() here would hide it."""
    out = derived.resolve([_row(DRAWAL, 100.0), _row(SCHEDULE, 250.0)], [QUANTUM])
    assert _one(out, QUANTUM)["value"] == pytest.approx(-150.0)


def test_positive_result():
    out = derived.resolve([_row(DRAWAL, 300.0), _row(SCHEDULE, 250.0)], [QUANTUM])
    assert _one(out, QUANTUM)["value"] == pytest.approx(50.0)


def test_genuine_zero_survives_as_zero_not_none():
    """A block that settled exactly on schedule is 0, and 0 is not missing data."""
    out = derived.resolve([_row(DRAWAL, 150.0), _row(SCHEDULE, 150.0)], [QUANTUM])
    value = _one(out, QUANTUM)["value"]
    assert value is not None
    assert value == pytest.approx(0.0)


def test_integer_inputs_are_accepted():
    out = derived.resolve([_row(DRAWAL, 10), _row(SCHEDULE, 4)], [QUANTUM])
    assert _one(out, QUANTUM)["value"] == pytest.approx(6)


# ------------------------------------------------------------------- absence

def test_missing_schedule_row_yields_none_not_the_raw_drawal():
    """The absent side must not be read as 0 — that would report 533.96 of OD."""
    out = derived.resolve([_row(DRAWAL, 533.96)], [QUANTUM])
    assert _one(out, QUANTUM)["value"] is None


def test_missing_drawal_row_yields_none_not_the_negated_schedule():
    out = derived.resolve([_row(SCHEDULE, 535.44)], [QUANTUM])
    assert _one(out, QUANTUM)["value"] is None


def test_dependency_present_but_null_yields_none():
    """An all-null $avg group returns None; that is absence, not zero."""
    out = derived.resolve([_row(DRAWAL, None), _row(SCHEDULE, 535.44)], [QUANTUM])
    assert _one(out, QUANTUM)["value"] is None


def test_missing_on_one_block_only_affects_that_block():
    rows = [
        _row(DRAWAL, 533.96, block_key="81"),
        _row(SCHEDULE, 535.44, block_key="81"),
        _row(DRAWAL, 400.0, block_key="82"),          # no schedule for block 82
    ]
    out = derived.resolve(rows, [QUANTUM])
    values = {r["block_key"]: r["value"] for r in _by_dataset(out, QUANTUM)}
    assert values["81"] == pytest.approx(-1.48)
    assert values["82"] is None


def test_no_row_is_invented_where_neither_dependency_appears():
    """Block 83 is outside the returned data entirely; it gets no cell at all."""
    rows = [_row(DRAWAL, 1.0, block_key="81"), _row(SCHEDULE, 2.0, block_key="81")]
    out = derived.resolve(rows, [QUANTUM])
    assert [r["block_key"] for r in _by_dataset(out, QUANTUM)] == ["81"]


def test_no_dependency_rows_at_all_produces_no_derived_rows():
    """A failed demand group must not manufacture a full column of zeroes."""
    out = derived.resolve([], [QUANTUM])
    assert _by_dataset(out, QUANTUM) == []


# -------------------------------------------------------------- key matching

def test_pairs_only_within_the_same_date_and_block():
    rows = [
        _row(DRAWAL, 100.0, date_key="2026-08-26", block_key="81"),
        _row(SCHEDULE, 40.0, date_key="2026-08-27", block_key="81"),
        _row(SCHEDULE, 30.0, date_key="2026-08-26", block_key="82"),
    ]
    out = derived.resolve(rows, [QUANTUM])
    got = {(r["date_key"], r["block_key"]): r["value"]
           for r in _by_dataset(out, QUANTUM)}
    assert got[("2026-08-26", "81")] is None
    assert got[("2026-08-27", "81")] is None
    assert got[("2026-08-26", "82")] is None


def test_entity_is_part_of_the_key():
    """A per-entity derived dataset must not cross-pair two plants."""
    rows = [
        _row(DRAWAL, 100.0, entity="PLANT_A"),
        _row(SCHEDULE, 60.0, entity="PLANT_B"),
    ]
    out = derived.resolve(rows, [QUANTUM])
    by_entity = {r["entity"]: r["value"] for r in _by_dataset(out, QUANTUM)}
    assert by_entity == {"PLANT_A": None, "PLANT_B": None}


def test_matching_entity_pairs_normally():
    rows = [
        _row(DRAWAL, 100.0, entity="PLANT_A"),
        _row(SCHEDULE, 60.0, entity="PLANT_A"),
    ]
    out = derived.resolve(rows, [QUANTUM])
    assert _one(out, QUANTUM)["value"] == pytest.approx(40.0)


def test_derived_row_carries_the_full_canonical_shape():
    out = derived.resolve([_row(DRAWAL, 5.0), _row(SCHEDULE, 3.0)], [QUANTUM])
    row = _one(out, QUANTUM)
    assert set(row) == {"dataset", "date_key", "block_key", "entity", "value"}
    assert row["date_key"] == "2026-08-26"
    assert row["block_key"] == "81"
    assert row["entity"] is None


def test_multiple_blocks_and_dates_all_resolve():
    rows = []
    for day in ("2026-08-26", "2026-08-27"):
        for block in ("1", "2", "3"):
            rows.append(_row(DRAWAL, 100.0, date_key=day, block_key=block))
            rows.append(_row(SCHEDULE, 90.0, date_key=day, block_key=block))
    out = derived.resolve(rows, [QUANTUM])
    quantum = _by_dataset(out, QUANTUM)
    assert len(quantum) == 6
    assert all(r["value"] == pytest.approx(10.0) for r in quantum)


# ------------------------------------------------- dependency-only row pruning

def test_dependency_rows_are_dropped_when_only_the_derived_was_ticked():
    rows = [_row(DRAWAL, 533.96), _row(SCHEDULE, 535.44)]
    out = derived.resolve(rows, [QUANTUM])
    assert _by_dataset(out, DRAWAL) == []
    assert _by_dataset(out, SCHEDULE) == []
    assert len(_by_dataset(out, QUANTUM)) == 1


def test_a_ticked_dependency_survives():
    rows = [_row(DRAWAL, 533.96), _row(SCHEDULE, 535.44)]
    out = derived.resolve(rows, [QUANTUM, DRAWAL])
    assert len(_by_dataset(out, DRAWAL)) == 1
    assert _by_dataset(out, SCHEDULE) == []
    assert _one(out, QUANTUM)["value"] == pytest.approx(-1.48)


def test_both_dependencies_ticked_both_survive():
    rows = [_row(DRAWAL, 533.96), _row(SCHEDULE, 535.44)]
    out = derived.resolve(rows, [QUANTUM, DRAWAL, SCHEDULE])
    assert len(_by_dataset(out, DRAWAL)) == 1
    assert len(_by_dataset(out, SCHEDULE)) == 1
    assert len(_by_dataset(out, QUANTUM)) == 1


def test_untouched_datasets_pass_straight_through():
    rows = [_row("ACTUAL_DEMAND", 42.0), _row(DRAWAL, 1.0), _row(SCHEDULE, 2.0)]
    out = derived.resolve(rows, ["ACTUAL_DEMAND", QUANTUM])
    assert _one(out, "ACTUAL_DEMAND")["value"] == pytest.approx(42.0)


def test_no_derived_requested_returns_the_rows_unchanged():
    rows = [_row("ACTUAL_DEMAND", 42.0), _row(DRAWAL, 1.0)]
    out = derived.resolve(rows, ["ACTUAL_DEMAND", DRAWAL])
    assert out == rows


def test_rows_for_datasets_nobody_ticked_are_dropped():
    rows = [_row("ACTUAL_DEMAND", 42.0), _row("FREQUENCY", 49.98)]
    out = derived.resolve(rows, ["ACTUAL_DEMAND"])
    assert [r["dataset"] for r in out] == ["ACTUAL_DEMAND"]


def test_kept_rows_keep_their_order_and_derived_rows_are_appended():
    rows = [_row("ACTUAL_DEMAND", 42.0), _row(DRAWAL, 5.0), _row(SCHEDULE, 3.0)]
    out = derived.resolve(rows, ["ACTUAL_DEMAND", DRAWAL, QUANTUM])
    assert [r["dataset"] for r in out] == [
        "ACTUAL_DEMAND", DRAWAL, QUANTUM]


def test_input_rows_are_not_mutated():
    rows = [_row(DRAWAL, 5.0), _row(SCHEDULE, 3.0)]
    snapshot = [dict(r) for r in rows]
    derived.resolve(rows, [QUANTUM])
    assert rows == snapshot
    assert len(rows) == 2


def test_requested_accepts_any_sequence():
    rows = [_row(DRAWAL, 5.0), _row(SCHEDULE, 3.0)]
    out = derived.resolve(rows, (QUANTUM,))
    assert _one(out, QUANTUM)["value"] == pytest.approx(2.0)


def test_empty_request_returns_nothing():
    assert derived.resolve([_row(DRAWAL, 5.0)], []) == []


# ------------------------------------------------------------- source_codes

def test_source_codes_expands_a_derived_dataset_to_its_dependencies():
    assert derived.source_codes([QUANTUM]) == [DRAWAL, SCHEDULE]


def test_source_codes_leaves_direct_datasets_alone():
    assert derived.source_codes(["ACTUAL_DEMAND"]) == ["ACTUAL_DEMAND"]


def test_source_codes_does_not_duplicate_a_ticked_dependency():
    assert derived.source_codes([DRAWAL, QUANTUM]) == [DRAWAL, SCHEDULE]


def test_source_codes_never_returns_a_derived_code():
    codes = derived.source_codes([QUANTUM, "ACTUAL_DEMAND"])
    assert QUANTUM not in codes
    assert all(registry.get(c).kind == "direct" for c in codes)


def test_source_codes_is_empty_for_an_empty_request():
    assert derived.source_codes([]) == []


# ----------------------------------------------------------------- validation

def test_unknown_code_raises():
    with pytest.raises(registry.UnknownDatasetError):
        derived.resolve([], ["NOT_A_DATASET"])


def test_source_codes_rejects_an_unknown_code():
    with pytest.raises(registry.UnknownDatasetError):
        derived.source_codes(["NOT_A_DATASET"])


def test_registry_still_declares_the_quantum_as_derived():
    """Guards the contract this module reads rather than hardcodes."""
    d = registry.get(QUANTUM)
    assert d.kind == "derived"
    assert d.depends == (DRAWAL, SCHEDULE)
    assert d.expr == "drawal - schedule"


# ------------------------------------------------ generality beyond OD/UD

def _inject(monkeypatch, *datasets):
    patched = dict(registry.DATASETS)
    for d in datasets:
        patched[d.code] = d
    monkeypatch.setattr(registry, "DATASETS", patched)


def _derived(code, depends, expr):
    return registry.Dataset(code=code, label=code, group="demand", unit="MW",
                            agg="avg", kind="derived", depends=depends, expr=expr)


def test_addition_expression(monkeypatch):
    _inject(monkeypatch, _derived("SUM_X", (DRAWAL, SCHEDULE), "drawal + schedule"))
    out = derived.resolve([_row(DRAWAL, 2.0), _row(SCHEDULE, 3.0)], ["SUM_X"])
    assert _one(out, "SUM_X")["value"] == pytest.approx(5.0)


def test_division_expression(monkeypatch):
    _inject(monkeypatch, _derived("RATIO", (DRAWAL, SCHEDULE), "drawal / schedule"))
    out = derived.resolve([_row(DRAWAL, 10.0), _row(SCHEDULE, 4.0)], ["RATIO"])
    assert _one(out, "RATIO")["value"] == pytest.approx(2.5)


def test_division_by_zero_is_none_not_an_exception(monkeypatch):
    _inject(monkeypatch, _derived("RATIO", (DRAWAL, SCHEDULE), "drawal / schedule"))
    out = derived.resolve([_row(DRAWAL, 10.0), _row(SCHEDULE, 0.0)], ["RATIO"])
    assert _one(out, "RATIO")["value"] is None


def test_a_derived_dataset_may_depend_on_another_derived_dataset(monkeypatch):
    _inject(monkeypatch,
            _derived("OUTER", (QUANTUM, DRAWAL), "quantum - drawal"))
    rows = [_row(DRAWAL, 10.0), _row(SCHEDULE, 4.0)]
    out = derived.resolve(rows, ["OUTER"])
    # quantum = 10 - 4 = 6; outer = 6 - 10 = -4
    assert _one(out, "OUTER")["value"] == pytest.approx(-4.0)
    # the intermediate was computed but never ticked, so it is not returned
    assert _by_dataset(out, QUANTUM) == []


def test_a_cycle_raises_rather_than_hanging(monkeypatch):
    _inject(monkeypatch,
            _derived("LOOP_A", ("LOOP_B", DRAWAL), "b - drawal"),
            _derived("LOOP_B", ("LOOP_A", DRAWAL), "a - drawal"))
    with pytest.raises(ValueError, match="(?i)cycle"):
        derived.resolve([_row(DRAWAL, 1.0)], ["LOOP_A"])


def test_an_unparseable_expression_raises_rather_than_returning_none(monkeypatch):
    _inject(monkeypatch, _derived("WEIRD", (DRAWAL, SCHEDULE), "max(drawal, sched)"))
    with pytest.raises(ValueError):
        derived.resolve([_row(DRAWAL, 1.0), _row(SCHEDULE, 2.0)], ["WEIRD"])


def test_a_derived_dataset_with_the_wrong_dependency_count_raises(monkeypatch):
    _inject(monkeypatch, _derived("LONELY", (DRAWAL,), "drawal - schedule"))
    with pytest.raises(ValueError):
        derived.resolve([_row(DRAWAL, 1.0)], ["LONELY"])


def test_source_codes_walks_a_nested_derived_chain(monkeypatch):
    _inject(monkeypatch, _derived("OUTER", (QUANTUM, "ACTUAL_DEMAND"), "q - a"))
    assert derived.source_codes(["OUTER"]) == [DRAWAL, SCHEDULE, "ACTUAL_DEMAND"]
