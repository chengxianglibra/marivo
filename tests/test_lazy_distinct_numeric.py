"""Independent set and rational references for source-private distinct allocation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from fractions import Fraction
from typing import Literal

import ibis
import pandas as pd
import pyarrow as pa
import pytest

from marivo.analysis.compiler.distinct_attribution import lower_distinct_attribute
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.operators.attribution_contracts import AttributePayload, AttributeSpecV1
from marivo.analysis.operators.compare import execute_compare
from marivo.analysis.operators.contracts import ComparePayload
from tests.lazy_distinct_fixtures import CHANNEL, DISTINCT_BUYERS, REGION, make_distinct_sources
from tests.lazy_execution_fixtures import assert_compiled_validations

Coordinate = tuple[str | None, ...]
Memberships = Sequence[Sequence[str | None]]


def _mask(value: object) -> tuple[bool, ...]:
    assert isinstance(value, (list, tuple))
    result: list[bool] = []
    for item in value:
        assert isinstance(item, bool)
        result.append(item)
    return tuple(result)


def _member(value: object) -> str | None:
    assert value is None or isinstance(value, str)
    return value


def _number(value: object) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value)


def _spec(
    coordinates: Sequence[Coordinate],
    current: Memberships,
    baseline: Memberships,
    mode: Literal["joint", "hierarchy"],
    top_k: int | None,
    scoped: bool,
) -> tuple[pd.DataFrame, AttributeSpecV1]:
    axes = (REGION,) if len(coordinates[0]) == 1 else (REGION, CHANNEL)
    metric = make_distinct_sources().observe(DISTINCT_BUYERS).with_dimensions(*axes).aggregate()
    delta = metric.compare(metric)
    logical = delta.attribute(axes=(REGION,) if scoped else axes, mode=mode, top_k=top_k)
    assert isinstance(delta._root, LogicalRootHandle) and isinstance(
        delta._root.payload, ComparePayload
    )
    assert isinstance(logical._root, LogicalRootHandle) and isinstance(
        logical._root.payload, AttributePayload
    )
    fields = [field for field in metric.schema.columns if field.role_id == "dimension"]
    metric_name = next(field.name for field in metric.schema.columns if field.role_id == "metric")
    sides = [
        pd.DataFrame(
            {
                **{
                    field.name: pd.Series(
                        [coordinate[index] for coordinate in coordinates], dtype="string[pyarrow]"
                    )
                    for index, field in enumerate(fields)
                },
                metric_name: pd.Series(
                    [len(set(values) - {None}) for values in side], dtype="int64[pyarrow]"
                ),
            }
        )
        for side in (current, baseline)
    ]
    frame = execute_compare(sides[0], sides[1], delta._root.payload.spec)
    return frame, logical._root.payload.spec


def _execute(
    coordinates: Sequence[Coordinate],
    current: Memberships,
    baseline: Memberships,
    *,
    mode: Literal["joint", "hierarchy"] = "joint",
    top_k: int | None = None,
    scoped: bool = False,
    corrupt: Literal["endpoint", "near_endpoint", "coordinate", "expanded", "absent"] | None = None,
) -> list[dict[str, object]]:
    frame, spec = _spec(coordinates, current, baseline, mode, top_k, scoped)
    fields = [field for field in spec.input_row.schema.columns if field.role_id == "dimension"]
    backend = ibis.duckdb.connect()
    try:
        members = []
        for index, side in enumerate((current, baseline)):
            records = [
                {
                    **dict(zip((field.name for field in fields), coordinate, strict=True)),
                    "__mv_distinct_key": key,
                }
                for coordinate, values in zip(coordinates, side, strict=True)
                for key in values
            ]
            if corrupt == "coordinate" and index == 0:
                records.append(
                    {**{field.name: "unknown" for field in fields}, "__mv_distinct_key": "secret"}
                )
            members.append(
                backend.create_table(
                    "membership_" + str(index),
                    pa.Table.from_pylist(
                        records,
                        schema=pa.schema(
                            [(field.name, pa.string()) for field in fields]
                            + [("__mv_distinct_key", pa.string())]
                        ),
                    ),
                )
            )
        if corrupt == "endpoint":
            frame.loc[0, "current_value"] = 999
        if corrupt == "absent":
            frame.loc[0, "coordinate_presence"] = "baseline_only"
        primary = backend.create_table(
            "primary_delta", pa.Table.from_pandas(frame, preserve_index=False)
        )
        if corrupt == "near_endpoint":
            primary = primary.mutate(current_value=primary.current_value.cast("float64") + 1e-10)
        original = None
        if corrupt == "expanded":
            spec = replace(spec, original_input_row=spec.input_row)
            original = primary.mutate(current_value=primary.current_value + 1)
        expression, validations = lower_distinct_attribute(
            primary,
            spec,
            current_membership=members[0],
            baseline_membership=members[1],
            original=original,
        )
        assert_compiled_validations(validations)
        assert "__mv_distinct_key" not in expression.columns
        result = backend.to_pyarrow(expression)
        return [
            {name: result[name][index].as_py() for name in result.column_names}
            for index in range(len(result))
        ]
    finally:
        backend.disconnect()


def _reference(
    coordinates: Sequence[Coordinate],
    current: Memberships,
    baseline: Memberships,
    *,
    top_k: int | None,
) -> dict[tuple[int, Coordinate, tuple[bool, ...]], tuple[Fraction, Fraction]]:
    """Allocate sets with rational numbers, independent of the compiled expression graph."""
    width = len(coordinates[0])
    mapped = [list(coordinate) for coordinate in coordinates]
    masks = [[False] * width for _ in coordinates]
    sides = [[set(values) - {None} for values in side] for side in (current, baseline)]
    if top_k is not None:
        for index in range(width):
            groups: dict[tuple[tuple[str | None, ...], tuple[bool, ...]], list[int]] = {}
            for position, coordinate in enumerate(mapped):
                groups.setdefault(
                    (tuple(coordinate[:index]), tuple(masks[position][:index])), []
                ).append(position)
            for positions in groups.values():
                scores: dict[str | None, set[str | None]] = {}
                for position in positions:
                    scores.setdefault(mapped[position][index], set()).update(
                        sides[0][position] | sides[1][position]
                    )
                winners = sorted(
                    scores, key=lambda value: (-len(scores[value]), value is None, value or "")
                )[:top_k]
                for position in positions:
                    if mapped[position][index] not in winners:
                        mapped[position][index] = None
                        masks[position][index] = True
    result: dict[tuple[int, Coordinate, tuple[bool, ...]], tuple[Fraction, Fraction]] = {}
    for size in range(1, width + 1):
        partitions: dict[tuple[Coordinate, tuple[bool, ...]], list[set[str | None]]] = {}
        for position, coordinate in enumerate(mapped):
            partition = (tuple(coordinate[:size]), tuple(masks[position][:size]))
            sets = partitions.setdefault(partition, [set(), set()])
            for side in range(2):
                sets[side].update(sides[side][position])
        for partition, sets in partitions.items():
            values = []
            for side in range(2):
                values.append(
                    sum(
                        (
                            Fraction(1, sum(key in sets_[side] for sets_ in partitions.values()))
                            for key in sets[side]
                        ),
                        Fraction(0),
                    )
                )
            result[(size, *partition)] = (values[0], values[1])
    return result


@pytest.mark.parametrize("top_k", [None, 1, 2])
def test_overlap_duplicates_null_and_other_match_independent_rational_reference(
    top_k: int | None,
) -> None:
    coordinates = [("a", "x"), ("a", "y"), ("b", "x"), ("b", "z"), (None, "x"), ("c", None)]
    current: Memberships = [["one", "one", "two"], ["one"], ["one", "three"], ["three"], [None], []]
    baseline: Memberships = [["one"], ["old", "one"], ["two"], ["three", "four"], [], [None]]
    records = _execute(coordinates, current, baseline, mode="hierarchy", top_k=top_k)
    expected = _reference(coordinates, current, baseline, top_k=top_k)
    assert len(records) == len(expected)
    for row in records:
        size = sum(_mask(row["active_axis_mask"]))
        coordinate = (_member(row["region"]), _member(row["channel"]))
        key = (size, coordinate[:size], _mask(row["other_mask"])[:size])
        a, b = expected[key]
        assert row["current_value"] == pytest.approx(float(a))
        assert row["baseline_value"] == pytest.approx(float(b))
        assert row["contribution"] == pytest.approx(float(a - b))
        assert row["overall_delta"] == -2.0


def test_topk_uses_cross_side_union_not_sum_of_side_distinct_counts() -> None:
    result = _execute(
        [("a",), ("b",)],
        [["same", "shared"], ["new", "third"]],
        [["same", "shared"], ["old"]],
        top_k=1,
    )
    assert {row["region"] for row in result if not _mask(row["other_mask"])[0]} == {"b"}


def test_other_merge_recomputes_degree_and_parent_allocation_is_independent() -> None:
    coordinates = [("a", "x"), ("a", "y"), ("b", "x")]
    current = [["shared"], ["shared"], ["shared"]]
    result = _execute(coordinates, current, [[], [], []], mode="hierarchy")
    parents = {row["region"]: row for row in result if row["active_axis_mask"] == [True, False]}
    leaves = [row for row in result if row["active_axis_mask"] == [True, True]]
    assert parents["a"]["current_value"] == 0.5
    assert sum(
        _number(row["current_value"]) for row in leaves if row["region"] == "a"
    ) == pytest.approx(2 / 3)
    merged = _execute([("a",), ("b",), ("c",)], current, [[], [], []], top_k=1)
    assert [row["current_value"] for row in merged] == [0.5, 0.5]


def test_all_null_and_empty_membership_keeps_zero_coordinate_spine() -> None:
    result = _execute([("a",), (None,)], [[None], []], [[], [None]], top_k=1)
    assert len(result) == 2
    assert all(
        row["current_value"] == row["baseline_value"] == row["contribution"] == 0 for row in result
    )
    assert all(row["status"] == "zero_total_delta" for row in result)
    assert all(row["share_of_total_delta"] is None for row in result)


def test_unrequested_coordinate_keeps_independent_scope_and_pools() -> None:
    result = _execute(
        [("a", "x"), ("b", "x"), ("a", "y")],
        [["shared"], ["shared"], ["shared"]],
        [[], [], ["old"]],
        scoped=True,
    )
    assert {row["overall_delta"] for row in result if row["channel"] == "x"} == {1.0}
    assert {row["overall_delta"] for row in result if row["channel"] == "y"} == {0.0}


@pytest.mark.parametrize(
    "corrupt", ["endpoint", "near_endpoint", "coordinate", "expanded", "absent"]
)
def test_endpoint_membership_and_expansion_contradictions_fail_without_key_values(
    corrupt: Literal["endpoint", "near_endpoint", "coordinate", "expanded", "absent"],
) -> None:
    with pytest.raises(AssertionError, match=r"attribution\.distinct") as caught:
        _execute([("a",)], [["private-key"]], [[]], corrupt=corrupt)
    assert "private-key" not in str(caught.value) and "secret" not in str(caught.value)
