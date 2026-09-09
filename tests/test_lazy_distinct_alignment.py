"""Distinct memberships consume comparison's exact selected coordinate mapping."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import date

import ibis
import ibis.expr.types as ir
import pyarrow as pa
import pytest
from ibis.backends.duckdb import Backend

from marivo.analysis import grain
from marivo.analysis.compiler.comparison import lower_compare
from marivo.analysis.compiler.distinct import (
    MembershipRelations,
    comparison_memberships,
    membership_validations,
)
from marivo.analysis.datasets.descriptors import _CORE_TOKEN, DatasetRowContract, _make_schema
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.distinct_contracts import (
    DISTINCT_KEY_COLUMN,
    membership_part_authorities,
)
from marivo.analysis.operators.contracts import ComparePayload, CompareSpecV1
from marivo.refs import ref
from tests.lazy_distinct_fixtures import DISTINCT_BUYERS, REGION, make_distinct_sources
from tests.lazy_execution_fixtures import assert_compiled_validations


@pytest.fixture
def backend() -> Iterator[Backend]:
    connection = ibis.duckdb.connect()
    try:
        yield connection
    finally:
        connection.disconnect()


def _spec(*, timed: bool = True, dimension: bool = True) -> CompareSpecV1:
    metric = make_distinct_sources().observe(DISTINCT_BUYERS)
    if dimension:
        metric = metric.with_dimensions(REGION)
    if timed:
        metric = metric.with_time_axis(
            ref.time_dimension("sales.orders.order_time"), grain=grain("day")
        )
    result = metric.aggregate()
    delta = result.compare(result)
    assert isinstance(delta._root, LogicalRootHandle)
    assert isinstance(delta._root.payload, ComparePayload)
    return delta._root.payload.spec


def _side(
    backend: Backend,
    name: str,
    row: DatasetRowContract,
    values: pa.Table,
    keys: Sequence[str],
) -> tuple[ir.Table, MembershipRelations]:
    ((role, authority),) = membership_part_authorities(row)
    coordinate_names = [
        field.name for field in row.schema.columns if field.field_id in row.key_field_ids
    ]
    members = values.select(coordinate_names).append_column(
        DISTINCT_KEY_COLUMN, pa.array(keys, type=pa.string())
    )
    for component in authority.components:
        for _state, column in component.state_columns:
            values = values.append_column(column, values["distinct_buyers"])
    return backend.create_table(name, values), (
        (role, backend.create_table(name + "_members", members)),
    )


def _rows(backend: Backend, table: ir.Table) -> list[dict[str, object]]:
    result = backend.to_pyarrow(table)
    return [
        {name: result[name][index].as_py() for name in result.column_names}
        for index in range(len(result))
    ]


def test_membership_uses_selected_time_pairs_with_null_dimensions_and_side_aliases(
    backend: Backend,
) -> None:
    spec = _spec()

    def renamed(row: DatasetRowContract, dimension: str, time: str) -> DatasetRowContract:
        names = {"region": dimension, "order_time": time}
        return replace(
            row,
            _token=_CORE_TOKEN,
            schema=_make_schema(
                tuple(
                    replace(field, _token=_CORE_TOKEN, name=names.get(field.name, field.name))
                    for field in row.schema.columns
                )
            ),
        )

    spec = replace(
        spec,
        current_row=renamed(spec.current_row, "current_value", "current_day"),
        baseline_row=renamed(spec.baseline_row, "baseline_value", "baseline_day"),
    )
    current, current_parts = _side(
        backend,
        "current",
        spec.current_row,
        pa.table(
            {
                "current_value": pa.array([None, "north", None, "north"], type=pa.string()),
                "current_day": [
                    date(2026, 2, 4),
                    date(2026, 2, 1),
                    date(2026, 2, 1),
                    date(2026, 2, 4),
                ],
                "distinct_buyers": [1, 1, 1, 1],
            }
        ),
        ["null-late", "north-early", "null-early", "north-late"],
    )
    baseline, baseline_parts = _side(
        backend,
        "baseline",
        spec.baseline_row,
        pa.table(
            {
                "baseline_value": pa.array(["north", None, "north", None], type=pa.string()),
                "baseline_day": [
                    date(2026, 1, 3),
                    date(2026, 1, 1),
                    date(2026, 1, 1),
                    date(2026, 1, 3),
                ],
                "distinct_buyers": [1, 1, 1, 1],
            }
        ),
        ["north-before-late", "null-before-early", "north-before-early", "null-before-late"],
    )
    comparison, checks = lower_compare(current, baseline, spec)
    assert_compiled_validations(checks)
    selected = comparison.filter(comparison.comparison_ordinal == 1)
    parts = dict(comparison_memberships(selected, current_parts, baseline_parts, spec))
    assert _rows(backend, selected.select("current_time", "baseline_time").distinct()) == [
        {"current_time": date(2026, 2, 4), "baseline_time": date(2026, 1, 3)}
    ]
    for side, expected in (
        ("current", ["null-late", "north-late"]),
        ("baseline", ["null-before-late", "north-before-late"]),
    ):
        rows = _rows(backend, parts["delta_membership." + side])
        assert {row["comparison_ordinal"] for row in rows} == {1}
        assert {row["region"] for row in rows} == {None, "north"}
        assert {row[DISTINCT_KEY_COLUMN] for row in rows} == set(expected)


def test_preassigned_noncontiguous_ordinals_and_selection_remain_comparison_authority(
    backend: Backend,
) -> None:
    spec = _spec(dimension=False)
    current, current_parts = _side(
        backend,
        "current",
        spec.current_row,
        pa.table(
            {
                "order_time": [date(2026, 2, 1), date(2026, 2, 4)],
                "comparison_ordinal": [42, 7],
                "distinct_buyers": [1, 1],
            }
        ),
        ["current-selected", "current-rejected"],
    )
    baseline, baseline_parts = _side(
        backend,
        "baseline",
        spec.baseline_row,
        pa.table(
            {
                "order_time": [date(2026, 1, 1), date(2026, 1, 3)],
                "comparison_ordinal": [7, 42],
                "distinct_buyers": [1, 1],
            }
        ),
        ["baseline-rejected", "baseline-selected"],
    )
    comparison, checks = lower_compare(current, baseline, spec, ordinal_preassigned=True)
    assert_compiled_validations(checks)
    selected = comparison.filter(comparison.comparison_ordinal == 42)
    parts = dict(comparison_memberships(selected, current_parts, baseline_parts, spec))
    for side in ("current", "baseline"):
        assert _rows(backend, parts["delta_membership." + side]) == [
            {"comparison_ordinal": 42, DISTINCT_KEY_COLUMN: side + "-selected"}
        ]
    assert _rows(backend, selected.select("current_time", "baseline_time")) == [
        {"current_time": date(2026, 2, 1), "baseline_time": date(2026, 1, 3)}
    ]


def test_memberships_keep_one_sided_coordinates_without_inventing_absent_support(
    backend: Backend,
) -> None:
    spec = _spec(timed=False)
    current, current_parts = _side(
        backend,
        "current",
        spec.current_row,
        pa.table({"region": ["matched", "current-only"], "distinct_buyers": [1, 1]}),
        ["current-match", "current-only"],
    )
    baseline, baseline_parts = _side(
        backend,
        "baseline",
        spec.baseline_row,
        pa.table({"region": ["matched", "baseline-only"], "distinct_buyers": [1, 1]}),
        ["baseline-match", "baseline-only"],
    )
    comparison, checks = lower_compare(current, baseline, spec)
    assert_compiled_validations(checks)
    parts = dict(comparison_memberships(comparison, current_parts, baseline_parts, spec))
    assert_compiled_validations(
        membership_validations(spec.current_row, current, dict(current_parts))
    )
    assert_compiled_validations(
        membership_validations(spec.baseline_row, baseline, dict(baseline_parts))
    )
    assert_compiled_validations(membership_validations(spec.output_row, comparison, parts))
    for side in ("current", "baseline"):
        rows = _rows(backend, parts["delta_membership." + side])
        assert {row["region"] for row in rows} == {"matched", side + "-only"}
        assert {row[DISTINCT_KEY_COLUMN] for row in rows} == {side + "-match", side + "-only"}


@pytest.mark.parametrize("absent", ["current", "baseline"])
def test_expanded_paired_time_does_not_recover_membership_for_an_absent_side(
    backend: Backend, absent: str
) -> None:
    spec = _spec(dimension=False)
    current, current_parts = _side(
        backend,
        "current",
        spec.current_row,
        pa.table({"order_time": [date(2026, 2, 1)], "distinct_buyers": [1]}),
        ["current-key"],
    )
    baseline, baseline_parts = _side(
        backend,
        "baseline",
        spec.baseline_row,
        pa.table({"order_time": [date(2026, 1, 1)], "distinct_buyers": [1]}),
        ["baseline-key"],
    )
    comparison, checks = lower_compare(current, baseline, spec)
    assert_compiled_validations(checks)
    present = "baseline" if absent == "current" else "current"
    expanded = comparison.mutate(coordinate_presence=ibis.literal(present + "_only"))
    parts = dict(comparison_memberships(expanded, current_parts, baseline_parts, spec))
    assert _rows(backend, parts["delta_membership." + absent]) == []
    assert _rows(backend, parts["delta_membership." + present]) == [
        {"comparison_ordinal": 0, DISTINCT_KEY_COLUMN: present + "-key"}
    ]


def test_scalar_comparison_selection_keeps_or_removes_membership_without_coordinates(
    backend: Backend,
) -> None:
    spec = _spec(timed=False, dimension=False)
    current, current_parts = _side(
        backend, "current", spec.current_row, pa.table({"distinct_buyers": [1]}), ["current-key"]
    )
    baseline, baseline_parts = _side(
        backend, "baseline", spec.baseline_row, pa.table({"distinct_buyers": [1]}), ["baseline-key"]
    )
    comparison, checks = lower_compare(current, baseline, spec)
    assert_compiled_validations(checks)
    for selected in (True, False):
        parts = dict(
            comparison_memberships(
                comparison.filter(ibis.literal(selected)), current_parts, baseline_parts, spec
            )
        )
        for side in ("current", "baseline"):
            assert _rows(backend, parts["delta_membership." + side]) == (
                [{DISTINCT_KEY_COLUMN: side + "-key"}] if selected else []
            )
