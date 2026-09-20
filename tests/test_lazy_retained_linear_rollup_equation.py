"""Typed-multiply contracts for the retained rollup equation's Linear branch.

The fold path re-evaluates Linear roots over exact retained component state, so
the coefficient multiply must keep int64 terms int64 and Decimal terms Decimal
the way the engine contract does (:func:`marivo.analysis.compiler.lowering._fold_value`
and ``metric_graph_lowering`` use the same integral-coefficient rule). These
tests drive the pandas state equation directly, independent of any backend.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from marivo.analysis.observation.fold_contracts import (
    FoldComponentV1,
    FoldNodeV1,
    MetricFoldAuthorityV1,
)
from marivo.analysis.operators.rollup import _value


def _sum_component(node_id: str, name: str, empty_rule: str) -> FoldComponentV1:
    return FoldComponentV1(
        node_id=node_id,
        kind="sum",
        state_columns=(("sum", name),),
        spatial_merge="sum",
        time_merge="sum",
        empty_rule="null" if empty_rule == "null" else "zero",
        null_rule="ignore_null_inputs",
        cumulative=False,
    )


def _linear_authority(
    components: tuple[FoldComponentV1, ...],
    coefficients: tuple[float, ...],
    empty_rule: str = "zero",
) -> MetricFoldAuthorityV1:
    left, right = components
    return MetricFoldAuthorityV1(
        metric_ref="sales.linear",
        field_id="metric.linear@v1",
        root_id="root",
        nodes=(
            FoldNodeV1(
                node_id="root", kind="linear", children=("left", "right"), coefficients=coefficients
            ),
            FoldNodeV1(node_id="left", kind="component"),
            FoldNodeV1(node_id="right", kind="component"),
        ),
        components=components,
        axis_partitions=(),
    )


def _state(left: object, right: object) -> dict[str, object]:
    return {"left": left, "right": right}


def test_int64_linear_rollup_stays_int64() -> None:
    authority = _linear_authority(
        (_sum_component("left", "left", "zero"), _sum_component("right", "right", "zero")),
        (1.0, -1.0),
    )
    result = _value(authority, _state(30, 12))
    assert result == 18
    assert type(result) is int


def test_decimal_linear_rollup_stays_decimal() -> None:
    authority = _linear_authority(
        (_sum_component("left", "left", "zero"), _sum_component("right", "right", "zero")),
        (1.0, -1.0),
    )
    result = _value(authority, _state(Decimal("30.15"), Decimal("3.85")))
    assert result == Decimal("26.30")
    assert isinstance(result, Decimal)


def test_decimal_linear_rollup_with_all_none_children_returns_none() -> None:
    authority = _linear_authority(
        (_sum_component("left", "left", "null"), _sum_component("right", "right", "null")),
        (1.0, -1.0),
    )
    assert _value(authority, _state(None, None)) is None


def test_mixed_int_float_linear_stays_float_like_engine_promotion() -> None:
    authority = _linear_authority(
        (_sum_component("left", "left", "zero"), _sum_component("right", "right", "zero")),
        (1.0, -1.0),
    )
    result = _value(authority, _state(7, 2.5))
    assert result == 4.5
    assert type(result) is float


def test_mixed_float_decimal_linear_promotes_to_float_like_engine() -> None:
    # The engine promotes decimal + float linear sums to double; the retained
    # equation must not raise on the same constructible state.
    authority = _linear_authority(
        (_sum_component("left", "left", "zero"), _sum_component("right", "right", "zero")),
        (1.0, -1.0),
    )
    result = _value(authority, _state(Decimal("3.75"), 1.25))
    assert result == 2.5


@pytest.mark.parametrize(
    "coefficients,expected,expected_type",
    [
        ((1.0, -1.0), -6, int),
        ((-1.0, 1.0), 6, int),
    ],
)
def test_pm1_coefficients_keep_int_signs(
    coefficients: tuple[float, float], expected: int, expected_type: type
) -> None:
    authority = _linear_authority(
        (_sum_component("left", "left", "zero"), _sum_component("right", "right", "zero")),
        coefficients,
    )
    result = _value(authority, _state(2, 8))
    assert result == expected
    assert type(result) is expected_type


def test_non_integral_coefficient_keeps_float_tolerance_path() -> None:
    authority = _linear_authority(
        (_sum_component("left", "left", "zero"), _sum_component("right", "right", "zero")),
        (0.5, 0.5),
    )
    result = _value(authority, _state(2, 8))
    assert result == 5.0
    assert type(result) is float
