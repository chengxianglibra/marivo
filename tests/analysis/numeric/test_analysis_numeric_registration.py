"""Boundary decisions for exact numeric implementation specialization."""

from dataclasses import replace
from typing import Literal

import pyarrow as pa
import pytest

from marivo.analysis.methods.builtin import (
    _grain_implementations,
    implementations,
    specialize_numeric,
)
from marivo.analysis.methods.descriptor import MethodName
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    ScalarType,
    ValueType,
    arrow_scalar_type,
)
from marivo.analysis.methods.semantics import MethodKey


@pytest.mark.parametrize(
    "method",
    (
        "metric.mean",
        "metric.weighted_mean",
        "metric.ratio",
        "metric.linear",
        "state_rollup.mean",
        "state_rollup.weighted_mean",
        "state_rollup.ratio",
        "state_rollup.linear",
    ),
)
def test_native_arithmetic_and_flat_registry_versions_are_independently_pinned(
    method: MethodName,
) -> None:
    numerical = [
        item
        for item in _grain_implementations(MethodKey(method))
        if item.precision == "native_numeric"
    ]
    selected = [
        item for item in implementations(MethodKey(method)) if item.precision == "native_numeric"
    ]
    assert numerical and {item.contract_version for item in numerical} == {5}
    assert selected and {item.contract_version for item in selected} == {7}


@pytest.mark.parametrize(
    "method,inputs,accepted,precision",
    [
        ("metric.ratio", (DecimalType(38, 6), DecimalType(38, 2)), True, "native_numeric"),
        ("metric.ratio", (ScalarType("float64"),) * 2, True, "native_numeric"),
        ("metric.ratio", (DurationType("ns"),) * 2, True, "finite_float64"),
        ("metric.ratio", (DurationType("ns"), DurationType("us")), False, None),
        ("metric.ratio", (DecimalType(38, 6), ScalarType("int64")), True, "native_numeric"),
        ("metric.ratio", (ScalarType("float64"), ScalarType("int64")), True, "native_numeric"),
        ("state_rollup.mean", (DecimalType(38, 6),), True, "native_numeric"),
        ("state_rollup.weighted_mean", (DurationType("ms"),), True, "checked_int64"),
        ("row.count", (DecimalType(38, 6),), True, "checked_int64"),
        ("row.count", (DurationType("s"),), True, "checked_int64"),
        ("row.mean", (DurationType("s"),), False, None),
    ],
)
def test_numeric_specialization_boundaries(
    method: MethodName, inputs: tuple[ValueType, ...], accepted: bool, precision: str | None
) -> None:
    template: Literal["float64", "int64"] = (
        "float64" if method in ("state_rollup.mean", "state_rollup.weighted_mean") else "int64"
    )
    candidate = next(
        item
        for item in implementations(MethodKey(method))
        if item.key.input_types == (ScalarType(template),) * len(inputs)
    )
    key = replace(candidate.key, input_types=inputs)
    result = specialize_numeric(candidate, key)
    if accepted:
        assert result.key == key
        assert result.precision == precision
        if precision == "native_numeric":
            assert result.contract_version == 7
    else:
        assert result is candidate
    assert (
        specialize_numeric(
            candidate,
            replace(
                key,
                input_types=(*inputs, inputs[0]),
                input_domains=(*key.input_domains, key.input_domains[0]),
            ),
        )
        is candidate
    )


@pytest.mark.parametrize(
    "physical,expected",
    [
        (ScalarType("int64"), pa.int64()),
        (ScalarType("float64"), pa.float64()),
        (DecimalType(38, 6), pa.decimal128(38, 6)),
        *((DurationType(unit), pa.duration(unit)) for unit in ("s", "ms", "us", "ns")),
    ],
)
def test_numeric_arrow_carriers(physical: ValueType, expected: pa.DataType) -> None:
    assert arrow_scalar_type(physical) == expected
