"""Derived datasets — expressions over other datasets, resolved after the source
groups return.

A derived dataset is not queryable. `OD_UD_QUANTUM` is `drawal - schedule`, and
although both fields sit on the same `demand_serve` document (so there is no join),
the subtraction still cannot live in the pipeline: the two operands are separate
registry datasets with their own accumulators, and under coarse grouping the
difference of two averages is only correct once both averages exist. So the executor
fetches the dependencies, and this module does the arithmetic over the flat
`CanonicalRow`s that come back.

Two rules carry the whole module:

1. **A missing operand is `None`, never `0`.** If the schedule row for a block never
   arrived, the quantum for that block is unknown — not "all of the drawal was
   over-drawal". PTC reads the DSM money column off this number. The same applies to
   an operand whose value is `None` because its `$avg` group was all-null.

2. **Dependency rows are an artefact of the query, not of the selection.** Ticking
   OD/UD Quantum makes the executor fetch Drawal and Final Schedule; the user never
   asked to see those columns, so `resolve` drops them again unless they were ticked
   in their own right.

`expr` in the registry is a human-readable string (`"drawal - schedule"`) whose
operand names are documentation — the operands themselves are `depends`, positionally.
Only the operator is read from it, and an expression this module cannot parse raises
rather than quietly producing a column of `None`.
"""
import operator
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import registry
from .adapters.base import CanonicalRow

#: `(date_key, block_key, entity)` — the full address of one cell. `entity` is part
#: of it so a per-plant derived dataset can never pair plant A against plant B.
_Key = Tuple[str, str, Optional[str]]

_EXPR_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*([-+*/])\s*([A-Za-z_][A-Za-z0-9_]*)\s*$"
)


def _safe_div(a: float, b: float) -> Optional[float]:
    """Division by zero is an unknown value, not an exception and not infinity."""
    return None if b == 0 else a / b


BINARY_OPS: Dict[str, Callable[[Any, Any], Any]] = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": _safe_div,
}


def _operator_for(dataset: registry.Dataset) -> Callable[[Any, Any], Any]:
    """The callable this dataset's `expr` names, validated against `depends`."""
    if not dataset.expr:
        raise ValueError(
            f"Derived dataset {dataset.code} has no expr; it cannot be computed"
        )
    match = _EXPR_RE.match(dataset.expr)
    if not match:
        raise ValueError(
            f"Derived dataset {dataset.code} has an expression this resolver cannot "
            f"evaluate: {dataset.expr!r}. Supported form is "
            f"'<left> <op> <right>' with op in {''.join(sorted(BINARY_OPS))}."
        )
    if len(dataset.depends) != 2:
        raise ValueError(
            f"Derived dataset {dataset.code} declares expr {dataset.expr!r} "
            f"(two operands) but depends on {len(dataset.depends)}: {dataset.depends}"
        )
    return BINARY_OPS[match.group(2)]


def _walk(codes: Iterable[str]) -> Tuple[List[registry.Dataset], List[str]]:
    """Depth-first walk of the dependency graph.

    Returns the derived datasets in the order they must be computed (dependencies
    first) and the direct dataset codes that have to be fetched, both in first-seen
    order. A cycle raises instead of recursing forever.
    """
    derived_order: List[registry.Dataset] = []
    sources: List[str] = []
    done: set = set()
    path: List[str] = []

    def visit(code: str) -> None:
        dataset = registry.get(code)  # raises UnknownDatasetError for a bad code
        if code in done:
            return
        if dataset.kind != "derived":
            done.add(code)
            sources.append(code)
            return
        if code in path:
            raise ValueError(
                "Derived dataset cycle: " + " -> ".join(path + [code])
            )
        path.append(code)
        for dep in dataset.depends:
            visit(dep)
        path.pop()
        done.add(code)
        derived_order.append(dataset)

    for code in codes:
        visit(code)
    return derived_order, sources


def source_codes(requested: Sequence[str]) -> List[str]:
    """The direct dataset codes that must actually be queried for `requested`.

    The planner calls this before collapsing datasets onto source groups: ticking
    only OD/UD Quantum still has to fetch Drawal and Final Schedule, and ticking a
    dependency as well must not fetch it twice.
    """
    return _walk(requested)[1]


def _index(rows: Iterable[dict], wanted: set
           ) -> Tuple[Dict[str, Dict[_Key, Any]], Dict[str, List[_Key]]]:
    """Rows -> {dataset: {key: value}} plus each dataset's key order.

    Only datasets something depends on are indexed; at individual-block granularity
    the row list runs to hundreds of thousands and the rest is dead weight.
    """
    values: Dict[str, Dict[_Key, Any]] = {}
    key_order: Dict[str, List[_Key]] = {}
    for row in rows or ():
        code = row["dataset"]
        if code not in wanted:
            continue
        key: _Key = (row["date_key"], row["block_key"], row["entity"])
        bucket = values.setdefault(code, {})
        if key not in bucket:
            key_order.setdefault(code, []).append(key)
        bucket[key] = row["value"]
    return values, key_order


def _compute(dataset: registry.Dataset,
             values: Dict[str, Dict[_Key, Any]],
             key_order: Dict[str, List[_Key]]
             ) -> Tuple[Dict[_Key, Any], List[_Key]]:
    """One derived dataset's values, over the union of its operands' cells.

    A cell where neither operand appeared is not invented — a failed group must not
    manufacture a full column. A cell where only one operand appeared is `None`,
    because the absent side is unknown, not zero.
    """
    op = _operator_for(dataset)
    left_code, right_code = dataset.depends
    left = values.get(left_code, {})
    right = values.get(right_code, {})

    keys: List[_Key] = []
    seen: set = set()
    for dep in (left_code, right_code):
        for key in key_order.get(dep, ()):
            if key not in seen:
                seen.add(key)
                keys.append(key)

    computed: Dict[_Key, Any] = {}
    for key in keys:
        a = left.get(key)
        b = right.get(key)
        computed[key] = None if a is None or b is None else op(a, b)
    return computed, keys


def resolve(rows: List[CanonicalRow],
            requested: Sequence[str]) -> List[CanonicalRow]:
    """Append the derived rows `requested` asks for and drop dependency-only rows.

    `rows` is everything the source groups returned, including datasets fetched only
    because something derived depends on them. The output is exactly the datasets the
    user ticked: the surviving originals in their original order, then the derived
    rows appended. The input list and its rows are left untouched.
    """
    wanted = list(requested)
    derived_order, _ = _walk(wanted)  # validates every code up front
    wanted_set = set(wanted)

    kept: List[CanonicalRow] = [r for r in (rows or ()) if r["dataset"] in wanted_set]
    if not derived_order:
        return kept

    dependency_codes = {dep for d in derived_order for dep in d.depends}
    values, key_order = _index(rows, dependency_codes)

    out = kept
    for dataset in derived_order:
        computed, keys = _compute(dataset, values, key_order)
        # Feed intermediates back in so a derived dataset may depend on another.
        values[dataset.code] = computed
        key_order[dataset.code] = keys
        if dataset.code not in wanted_set:
            continue
        for key in keys:
            out.append(CanonicalRow(
                dataset=dataset.code,
                date_key=key[0],
                block_key=key[1],
                entity=key[2],
                value=computed[key],
            ))
    return out
