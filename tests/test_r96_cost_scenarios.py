"""Actual public consumers for the narrow R9.6 cost route increment."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from devtools.r96_cost_scenarios import (
    START,
    _keyed_daily,
    _keyed_equal,
    _keyed_input,
    _keyed_rows,
    _rows,
    workload,
)
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DerivedQuantity,
    DomainSignature,
    Signature,
)
from marivo.analysis.core.time_grid import bind_grid
from marivo.analysis.materialization.deviation_execution import SavedPart, load, save
from marivo.analysis.materialization.graph_protocol import descriptor_plan
from marivo.analysis.materialization.statistical_execution import Input
from marivo.analysis.methods.builtin import implementations
from marivo.analysis.methods.physical import Qualified, ScalarType, SourceShape, TimeShape
from marivo.analysis.methods.semantics import MethodKey


@pytest.mark.parametrize(
    "family,keys,values",
    (
        (
            "ranking",
            (("a", 2**53 + 1, 1), ("a", 2**53 + 2, 1)),
            ((1, "defined", None), (2, "defined", None)),
        ),
        ("distribution", (("a", 0), ("a", 1)), ((1, "defined", None), (2, "defined", None))),
        ("ratio", (("a", 0), ("a", 1)), ((3, 1, 7, 2), (9, 2, 12, 2))),
        ("attribution", ((2, "a", "started", 0), (2, "a", "finished", 0)), ((9, 2, 7), (8, 5, 3))),
        (
            "comparison",
            (("a", 2**53 + 1, 1), ("a", 2**53 + 2, 1)),
            ((3, "defined", None), (8, "defined", None)),
        ),
        ("deviation", (("a", 2**53 + 1, 1), ("a", 2**53 + 2, 1)), ((3, 4.0, -0.5), (8, 4.0, 2.0))),
        (
            "runs",
            (("a", 0, START), ("a", 1, START)),
            ((START + timedelta(days=2), 2), (START + timedelta(days=3), 3)),
        ),
        ("association", ((-1,), (1,)), ((0.25, "valid", False), (0.75, "valid", True))),
        (
            "forecast",
            ((START + timedelta(days=16),), (START + timedelta(days=17),)),
            ((11.0, 1), (12.0, 2)),
        ),
        (
            "anchor",
            (("a", 0, "cost.started", "a", 2**53, 1), ("a", 1, "cost.started", "a", 2**53 + 2, 1)),
            ((("a", 2**53 + 1, 1),), (("a", 2**53 + 3, 1),)),
        ),
    ),
)
def test_keyed_cost_oracle_rejects_consistent_multiset_with_wrong_owner(
    family: str,
    keys: tuple[tuple[object, ...], tuple[object, ...]],
    values: tuple[tuple[object, ...], tuple[object, ...]],
) -> None:
    expected = dict(zip(keys, values, strict=True))
    swapped = dict(zip(keys, reversed(values), strict=True))
    assert sorted(expected.values(), key=repr) == sorted(swapped.values(), key=repr)
    assert len(_keyed_equal(expected, expected)) == 64
    with pytest.raises(AssertionError):
        _keyed_equal(swapped, expected)
    moved = {keys[0]: values[0], (*keys[1], "wrong-owner"): values[1]}
    with pytest.raises(AssertionError, match="original key ownership"):
        _keyed_equal(moved, expected)
    assert family


def _owned_daily_input(rows: list[dict[str, object]], column: str) -> Input:
    grid = bind_grid(
        mv.time_scope(start=START, end=START + timedelta(days=16)),
        mv.grain("day"),
        report_timezone="UTC",
    )
    cells = grid.cells
    coordinate = Coordinate(ms.ref.entity("cost.subjects"), "day", "anchor")
    domain = DomainSignature(
        Binding("owned", "cost", "subjects", "all"),
        "group",
        (coordinate,),
        (),
        "daily",
        time_grid=grid,
    )
    expected = _keyed_daily(rows, column)
    primary = pa.table(
        {
            "key_0": [cell.identity for cell in cells],
            "value": pa.array([value[0] for value in expected.values()], type=pa.int64()),
            "cell_tag": ["defined"] * 16,
            "cell_reason": pa.array([None] * 16, type=pa.string()),
        }
    )
    coverage = pa.table({"key_0": primary["key_0"], "coverage__complete": [True] * 16})
    support = [0] * 16
    for row in rows:
        instant = row["happened"]
        assert isinstance(instant, datetime)
        if row[column] is not None:
            support[(instant - START).days] += 1
    original = pa.table(
        {
            "key_0": primary["key_0"],
            "original_state__sum": primary["value"],
            "original_state__non_null_count": support,
        }
    )
    return Input(
        Signature(
            domain, DerivedQuantity("input", "test@v1", ("raw",), None, "none", "input_owned")
        ),
        "int64",
        "owned",
        ("key_0",),
        save(primary),
        (
            SavedPart("coverage", ("key_0",), save(coverage)),
            SavedPart("original_state", ("key_0",), save(original)),
        ),
        (),
    )


@pytest.mark.parametrize("column", ("amount", "y"))
def test_training_and_pair_inputs_bind_original_daily_values_to_their_time_keys(
    column: str,
) -> None:
    rows = _rows(64, "forecast-models")
    captured = _owned_daily_input(rows, column)
    assert len(_keyed_input(captured, rows, column)) == 64
    expected = _keyed_daily(rows, column)
    values = [value[0] for value in expected.values()]
    grid = captured.signature.domain.time_grid
    assert grid is not None
    changed = pa.table(
        {
            "key_0": [cell.identity for cell in grid.cells],
            "value": pa.array([*values[1:], values[0]], type=pa.int64()),
            "cell_tag": ["defined"] * 16,
            "cell_reason": pa.array([None] * 16, type=pa.string()),
        }
    )
    assert sorted(values, key=repr) == sorted(changed["value"].to_pylist(), key=repr)
    with pytest.raises(AssertionError):
        _keyed_input(replace(captured, primary=save(changed)), rows, column)
    shifted = bind_grid(
        mv.time_scope(start=START + timedelta(days=1), end=START + timedelta(days=17)),
        mv.grain("day"),
        report_timezone="UTC",
    )
    with pytest.raises(AssertionError):
        _keyed_input(
            replace(
                captured,
                signature=replace(
                    captured.signature, domain=replace(captured.signature.domain, time_grid=shifted)
                ),
            ),
            rows,
            column,
        )


def test_daily_support_counts_cannot_be_reassigned_between_training_periods() -> None:
    rows = _rows(64, "forecast-models")
    captured = _owned_daily_input(rows, "amount")
    assert len(_keyed_input(captured, rows, "amount")) == 64
    original = next(part for part in captured.parts if part.role == "original_state")
    table = load(original.table)
    counts = table["original_state__non_null_count"].to_pylist()
    shifted = [*counts[1:], counts[0]]
    assert sorted(counts) == sorted(shifted) and counts != shifted
    damaged = table.set_column(
        table.schema.get_field_index("original_state__non_null_count"),
        "original_state__non_null_count",
        pa.array(shifted, type=pa.int64()),
    )
    with pytest.raises(AssertionError):
        _keyed_input(
            replace(
                captured,
                parts=tuple(
                    replace(part, table=save(damaged)) if part.role == "original_state" else part
                    for part in captured.parts
                ),
            ),
            rows,
            "amount",
        )


def test_cross_batch_key_oracle_retains_known_empty_cells_and_exact_unavailable_day() -> None:
    rows = _rows(64, "cross-batch-long-runs-unavailable")
    cells = _keyed_daily(rows, "amount", periods=1536, subjects=True, gap=1200)
    assert len(cells) == 16 * 1536
    assert cells[("a", 0, START)] == (0, "defined", None)
    assert cells[("a", 1, START + timedelta(days=1))] == (2, "defined", None)
    assert cells[("a", 15, START)] == (0, "defined", None)
    assert cells[("a", 15, START + timedelta(days=1200))] == (
        None,
        "unknown",
        "insufficient_business_coverage",
    )
    assert cells[("a", 15, START + timedelta(days=1201))] == (0, "defined", None)
    table = pa.table({"key_0": ["same", "same"], "value": [1, 2]})
    with pytest.raises(AssertionError, match="duplicate independent oracle key"):
        _keyed_rows(table, ("key_0",), ("value",))


@pytest.mark.parametrize("facts", (1000, 100000))
def test_extreme_training_keeps_every_full_bucket_extreme(facts: int) -> None:
    rows = _rows(facts, "full-training-numeric-extremes")
    assert len(rows) == facts
    assert len({(row["tenant"], row["id"], row["revision"]) for row in rows}) == facts
    totals = [0] * 16
    counts = [0] * 16
    extremes = [0] * 16
    for row in rows:
        instant, amount = row["happened"], row["amount"]
        assert isinstance(instant, datetime) and isinstance(amount, int)
        period = (instant - START).days
        assert 0 <= period < 16
        assert -(2**63) <= amount < 2**63
        totals[period] += amount
        counts[period] += 1
        extremes[period] += int(amount > 2**53)
    assert sum(counts) == facts
    assert counts == [facts // 16 + int(period < facts % 16) for period in range(16)]
    assert extremes == [1] * 16
    assert all(2**53 < total < 2**63 for total in totals)


def test_efficiency_event_and_forecast_representatives_preserve_fact_identity_domain() -> None:
    assert _rows(1000, "event-lifecycle-anchor") == _rows(1000, "many-occurrences-anchors-lags")
    ordinary = _rows(1000, "forecast-models")
    extremes = _rows(1000, "full-training-numeric-extremes")
    assert [
        (row["tenant"], row["id"], row["revision"], row["happened"], row["owner"])
        for row in ordinary
    ] == [
        (row["tenant"], row["id"], row["revision"], row["happened"], row["owner"])
        for row in extremes
    ]
    assert any(row["amount"] is None for row in ordinary)
    assert all(isinstance(row["amount"], int) for row in extremes)


@pytest.mark.runtime
def test_duckdb_cost_routes_keep_the_original_exact_sum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        assert [row["amount"] for row in work.rows[:6]] == [None, 2, 3, 4, 5, 6]
        assert [row["id"] for row in work.rows[:2]] == [9007199254740992, 9007199254740993]
        references: list[object] = []
        for route in ("ibis", "ibis_python"):
            result = work.source(route)
            assert result.to_pandas().value.tolist() == [529]
            assert work.validate(result)["passed"] is True
            identity = work.identity(result)
            assert identity["root_route"] == route
            references.append(identity["artifact_ref"])
            fixed = work.fixed(result)
            first = fixed.execute()
            assert first.to_pandas().value.tolist() == [529]
            assert work.validate(first)["passed"] is True
            count = len(work.session.runs().items)
            hit = fixed.execute()
            assert hit.state.artifact_ref == first.state.artifact_ref
            assert len(work.session.runs().items) == count
            assert work.session._runtime.store.resources(work.session.id) == ()
        assert references[0] != references[1]


@pytest.mark.runtime
def test_duckdb_local_original_sum_consumes_group_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        independent_totals = {"started": 0, "finished": 0}
        for row in work.rows:
            amount = row["amount"]
            if amount is not None:
                assert isinstance(amount, int)
                independent_totals[str(row["kind"])] += amount
        assert independent_totals == {"started": 262, "finished": 267}
        axis = ms.ref.dimension("cost.facts.kind")
        raw = work.session.members(ms.ref.entity("cost.facts")).observe(
            ms.ref.metric("cost.facts_total"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
            coordinates=(axis,),
        )
        assert isinstance(raw, mv.LogicalNumericRelation)
        grouped = raw.group_by(axis).rollup()
        assert isinstance(grouped, mv.LogicalRolledNumericRelation)
        assert grouped._node.root.signature.domain.kind == "group"
        result = grouped.deviation(method="zscore").observed.rollup().execute()
        assert result.to_pandas().value.tolist() == [sum(independent_totals.values())]
        assert result.to_pandas().cell_tag.tolist() == ["defined"]
        assert work.validate(result)["passed"] is True
        assert result._dataset is not None
        plan = descriptor_plan(result._dataset.artifact.descriptor, result._node.definition)
        root = next(
            item
            for item in plan.physical_requirements
            if item.node_id == result._node.definition.identity
        )
        assert root.key.method == MethodKey("state_rollup.sum_zero")
        assert root.key.input_types == (ScalarType("int64"),)
        assert root.key.input_domains == ("group",)
        assert root.key.shape == SourceShape(
            "duckdb", "table", "native", TimeShape("instant", "us", "UTC")
        )
        assert root.key.route == "ibis_python"
        assert isinstance(root.implementation.qualification, Qualified)
        assert root.implementation.qualification.implementation_id.startswith(
            "r93.c09.local.duckdb."
        )
        assert work.session._runtime.store.resources(work.session.id) == ()


@pytest.mark.parametrize("name", ("cell.difference", "attribution.additive_difference"))
def test_cost_sum_increment_does_not_grant_other_duckdb_local_consumers(
    name: Literal["cell.difference", "attribution.additive_difference"],
) -> None:
    assert not any(
        isinstance(item.key.shape, SourceShape)
        and item.key.shape.backend == "duckdb"
        and item.key.shape.time == TimeShape("instant", "us", "UTC")
        and item.key.route == "ibis_python"
        and isinstance(item.qualification, Qualified)
        and item.qualification.implementation_id.startswith("r93.c09.local.duckdb.")
        for item in implementations(MethodKey(name))
    )
