"""Independent exhaustive complete-grid interval and classifier oracles."""

from dataclasses import replace
from itertools import groupby, product

import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DerivedQuantity,
    DomainSignature,
    Signature,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.time_grid import bind_grid
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.deviation_execution import save
from marivo.analysis.materialization.runs_execution import Capture, compute
from marivo.analysis.methods.physical import ScalarType


def capture(
    states: tuple[int, ...],
    *,
    zone: str = "UTC",
    start: str = "2026-03-06",
    end: str = "2026-03-11",
) -> Capture:
    grid = bind_grid(mv.time_scope(start=start, end=end), mv.grain("day"), report_timezone=zone)
    binding = Binding("session", "owner", "input", "scope")
    coord = Coordinate(ms.ref.entity("sales.order"), "time:" + grid.identity, "anchor")
    signature = Signature(
        DomainSignature(binding, "group", (coord,), (coord,), "original", time_grid=grid),
        DerivedQuantity("amount", "test@v1", ("input",), None, "time", "input_owned"),
    )
    rows = pa.table(
        {
            "key_0": [c.identity for c in grid.cells],
            "value": pa.array([None if x < 0 else x for x in states], type=pa.int64()),
            "cell_tag": ["undefined" if x < 0 else "defined" for x in states],
            "cell_reason": pa.array(
                ["no_scale" if x < 0 else None for x in states], type=pa.string()
            ),
        }
    )
    coverage = pa.table({"key_0": rows["key_0"], "complete": [True] * len(states)})
    return Capture(
        signature,
        "runs",
        ValuePredicate(binding, "gt", 0),
        ("key_0",),
        (save(rows),),
        save(coverage),
        None,
        ("fixture",),
        (signature,),
        (ScalarType("int64"),),
        ((("undefined", ("no_scale",)),),),
    )


def test_every_five_cell_classification() -> None:
    for states in product((-1, 0, 1), repeat=5):
        captured = capture(states)
        views, classes, reasons = compute(captured)
        assert classes == tuple(
            "unavailable" if x < 0 else "true" if x else "false" for x in states
        )
        expected = []
        for active, items in groupby(enumerate(states), key=lambda item: item[1] == 1):
            indices = [i for i, _ in items]
            if active:
                expected.append((indices[0], indices[-1] + 1))
        grid = captured.signature.domain.time_grid
        assert grid is not None
        actual = views.to_pylist()
        assert [(r["start"], r["end"], r["count"]) for r in actual] == [
            (grid.cells[a].start, grid.cells[b - 1].end, b - a) for a, b in expected
        ]
        for row, (a, b) in zip(actual, expected, strict=True):
            assert row["duration"].total_seconds() == (b - a) * 86400
            assert row["cells"] == [c.identity for c in grid.cells[a:b]]
            assert row["left_kind"] == (
                "scope_boundary" if a == 0 else "unavailable" if states[a - 1] < 0 else "false"
            )
            assert row["right_kind"] == (
                "scope_boundary" if b == 5 else "unavailable" if states[b] < 0 else "false"
            )
        assert all(
            reason == ("no_scale",) if x < 0 else reason == ()
            for x, reason in zip(states, reasons, strict=True)
        )


@pytest.mark.parametrize(
    "start,end,hours", [("2026-03-07", "2026-03-10", 71), ("2026-10-31", "2026-11-03", 73)]
)
def test_dst_elapsed_boundaries(start: str, end: str, hours: int) -> None:
    views, _, _ = compute(capture((1, 1, 1), zone="America/New_York", start=start, end=end))
    assert views["duration"][0].as_py().total_seconds() == hours * 3600


def test_unavailable_sibling_is_not_short_circuited() -> None:
    original = capture((-1, 0, 1, 0, -1))
    true = ValuePredicate(original.predicate.binding, "is_defined", 0)
    tree = ValuePredicate(
        original.predicate.binding, "any_of", 0, children=(true, original.predicate)
    )
    _, classes, _ = compute(replace(original, predicate=tree))
    assert classes == ("unavailable", "true", "true", "true", "unavailable")


def test_all_dependencies_and_total_state_predicate() -> None:
    from marivo.analysis.materialization.deviation_execution import load

    original = capture((1, 0, 1, -1, 0))
    other = capture((-1, 1, 0, -1, 1))
    comparison = replace(original.predicate, input_index=1)
    tree = ValuePredicate(
        original.predicate.binding, "all_of", 0, children=(original.predicate, comparison)
    )
    bound = replace(
        original,
        predicate=tree,
        inputs=(*original.inputs, *other.inputs),
        input_bindings=("left", "right"),
        input_signatures=(original.signature, original.signature),
        value_types=(*original.value_types, *other.value_types),
        input_reasons=(*original.input_reasons, *other.input_reasons),
    )
    assert compute(bound)[1] == ("unavailable", "false", "false", "unavailable", "false")
    state = replace(original.predicate, operator="is_defined")
    assert compute(replace(original, predicate=state))[1] == (
        "true",
        "true",
        "true",
        "false",
        "true",
    )
    invalid = load(original.inputs[0]).set_column(
        3, "cell_reason", pa.array([None, None, None, "unowned", None], type=pa.string())
    )
    with pytest.raises(AnalysisError):
        compute(replace(original, inputs=(save(invalid),)))


def test_two_sided_opposite_signs_share_a_maximal_run() -> None:
    from marivo.analysis.materialization.deviation_execution import load

    original = capture((1, 1, 1, 1, 0))
    table = load(original.inputs[0]).set_column(
        1, "value", pa.array([-5, -2, 2, 5, 0], type=pa.int64())
    )
    left = replace(original.predicate, operator="lt", value=-1)
    right = replace(original.predicate, operator="gt", value=1)
    condition = ValuePredicate(original.predicate.binding, "any_of", 0, children=(left, right))
    views, classes, _ = compute(replace(original, predicate=condition, inputs=(save(table),)))
    assert classes == ("true", "true", "true", "true", "false")
    assert views["count"].to_pylist() == [4]
    assert views["right_kind"].to_pylist() == ["false"]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "coverage"])
def test_grid_faults_are_hard_failures(fault: str) -> None:
    original = capture((1, 1, 1, 1, 1))
    from marivo.analysis.materialization.deviation_execution import load

    rows = load(original.inputs[0])
    if fault == "missing":
        original = replace(original, inputs=(save(rows.slice(1)),))
    elif fault == "duplicate":
        original = replace(original, inputs=(save(pa.concat_tables([rows, rows.slice(0, 1)])),))
    else:
        coverage = load(original.coverage).set_column(1, "complete", pa.array([False] * 5))
        original = replace(original, coverage=save(coverage))
    with pytest.raises(AnalysisError):
        compute(original)


def test_identity_and_segments_ignore_row_and_batch_layout() -> None:
    from marivo.analysis.materialization.deviation_execution import load

    original = capture((1, 1, 0, -1, 1))
    table = load(original.inputs[0])
    shuffled = table.take(pa.array([4, 2, 0, 3, 1], type=pa.int64()))
    batches = pa.concat_tables([shuffled.slice(0, 2), shuffled.slice(2, 1), shuffled.slice(3)])
    altered = replace(original, inputs=(save(batches),))
    first = compute(original)[0]
    second = compute(altered)[0]
    assert first.select(["key_0", "start", "end", "count", "duration"]).equals(
        second.select(["key_0", "start", "end", "count", "duration"])
    )
