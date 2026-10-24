"""Validate captured method structure and exact non-floating arithmetic."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

import pandas as pd
import pyarrow as pa

from marivo.analysis.core.attribute import _coefficient_column
from marivo.analysis.core.data_types import DurationDataType, ScalarDataType
from marivo.analysis.core.data_validation import stage_arrow_type
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameAttributeMetric,
    FrameLevelAdditiveMethod,
    FrameLevelLinearMethod,
    FrameLevelRatioMethod,
    FrameLinearMethod,
    FrameMethodFacts,
    FrameMethodValue,
    FrameRatioMethod,
)
from marivo.analysis.core.model import reject
from marivo.analysis.methods.frame_attribute_contribution import _numeric_binary, _Value
from marivo.semantic.errors import SemanticExpressionError


def _value(value: FrameMethodValue) -> _Value:
    dtype = value.column.dtype
    array = (
        pa.array([value.value], type=pa.int64()).cast(stage_arrow_type(dtype))
        if isinstance(dtype, DurationDataType)
        else pa.array([value.value], type=stage_arrow_type(dtype))
    )
    return _Value(pd.Series(pd.arrays.ArrowExtensionArray(array)), value.column)


def _equal(actual: _Value, expected: FrameMethodValue) -> None:
    array = pa.array(actual.values, type=stage_arrow_type(actual.column.dtype))
    if isinstance(actual.column.dtype, DurationDataType):
        array = array.cast(pa.int64())
    if actual.column.dtype != expected.column.dtype or (
        expected.column.dtype != ScalarDataType("float64") and array[0].as_py() != expected.value
    ):
        reject(
            "saved method totals consistent with their captured typed arithmetic",
            expected.column.name,
            "Preserve the exact completed method totals and their numeric carriers.",
            "frame.method_facts",
            canonical_id="analysis_frame.method_facts",
        )


def _binary_equal(
    operation: Literal["add", "sub", "div"],
    left: FrameMethodValue,
    right: FrameMethodValue,
    expected: FrameMethodValue,
    *,
    finish: bool = True,
) -> None:
    # Float64 facts are captured native results, not a second execution of the
    # formula. Skip before evaluation, including potentially overflowing inputs.
    if expected.column.dtype == ScalarDataType("float64"):
        return
    _equal(
        _numeric_binary(
            operation, _value(left), _value(right), output=expected.column.dtype if finish else None
        ),
        expected,
    )


def _scale_equal(
    value: FrameMethodValue, coefficient: int | float | Decimal, output: FrameMethodValue
) -> None:
    if output.column.dtype == ScalarDataType("float64"):
        return
    scalar = _Value(
        pd.Series([coefficient], dtype=object),
        _coefficient_column(coefficient),
    )
    _equal(_numeric_binary("mul", _value(value), scalar, output=output.column.dtype), output)


def _method(method: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod) -> None:
    _binary_equal("sub", method.left, method.right, method.change, finish=False)
    if isinstance(method, FrameRatioMethod):
        for numerator, denominator, expected in (
            (method.numerator_left, method.denominator_left, method.left),
            (method.numerator_right, method.denominator_right, method.right),
        ):
            _binary_equal("div", numerator, denominator, expected)
    elif isinstance(method, FrameLinearMethod):
        for term in method.terms:
            _method(term.method)
        if len(method.terms) == 1:
            term = method.terms[0]
            _scale_equal(term.method.left, term.coefficient, method.left)
            _scale_equal(term.method.right, term.coefficient, method.right)
        elif (
            len(method.terms) == 2
            and type(method.terms[0].coefficient) is int
            and method.terms[0].coefficient == 1
            and type(method.terms[1].coefficient) is int
            and method.terms[1].coefficient in (1, -1)
        ):
            first, second = method.terms
            operation: Literal["add", "sub"] = "add" if second.coefficient == 1 else "sub"
            _binary_equal(operation, first.method.left, second.method.left, method.left)
            _binary_equal(operation, first.method.right, second.method.right, method.right)
        else:
            reject(
                "the captured unary scale or binary add/subtract method topology",
                str(len(method.terms)),
                "Retain each original ordered linear operation without rewriting its terms.",
                "frame.method_facts",
                canonical_id="analysis_frame.method_facts",
            )


def _level(
    method: FrameLevelAdditiveMethod | FrameLevelRatioMethod | FrameLevelLinearMethod,
) -> None:
    if isinstance(method, FrameLevelRatioMethod):
        _binary_equal("div", method.numerator, method.denominator, method.value)
    elif isinstance(method, FrameLevelLinearMethod):
        for term in method.terms:
            _level(term.method)
        if len(method.terms) == 1:
            term = method.terms[0]
            _scale_equal(term.method.value, term.coefficient, method.value)
        elif (
            len(method.terms) == 2
            and type(method.terms[0].coefficient) is int
            and method.terms[0].coefficient == 1
            and type(method.terms[1].coefficient) is int
            and method.terms[1].coefficient in (1, -1)
        ):
            first, second = method.terms
            operation: Literal["add", "sub"] = "add" if second.coefficient == 1 else "sub"
            _binary_equal(operation, first.method.value, second.method.value, method.value)
        else:
            reject(
                "the captured unary scale or binary add/subtract topology",
                str(len(method.terms)),
                "Preserve original ordered linear operations.",
                "frame.method_facts",
                canonical_id="analysis_frame.method_facts",
            )


def _target(metric: FrameAttributeMetric) -> None:
    method = metric.method
    if isinstance(
        method, (FrameLevelAdditiveMethod, FrameLevelRatioMethod, FrameLevelLinearMethod)
    ):
        _level(method)
        _equal(_value(method.value), metric.total)
    else:
        _method(method)
        if metric.kind == "relative":
            if metric.total.column.dtype != ScalarDataType("float64"):
                baseline = _value(method.right)
                baseline = _Value(baseline.values.abs(), baseline.column)
                _equal(
                    _numeric_binary(
                        "div", _value(method.change), baseline, output=metric.total.column.dtype
                    ),
                    metric.total,
                )
        else:
            _equal(_value(method.change), metric.total)


def validate_method_facts(facts: FrameMethodFacts) -> None:
    """Validate structure and exact arithmetic; Float64 values remain captured facts."""
    if type(facts) is not FrameMethodFacts:
        reject(
            "exact complete FrameMethodFacts",
            type(facts).__name__,
            "Use the completed immutable method facts.",
            "frame.method_facts",
            canonical_id="analysis_frame.method_facts",
        )
    try:
        for parent in facts.parents:
            for metric in parent.metrics:
                _target(metric)
    except (
        SemanticExpressionError,
        pa.ArrowException,
        ArithmeticError,
        TypeError,
        ValueError,
    ) as error:
        reject(
            "finite saved totals in their original numeric carriers",
            f"{type(error).__name__}: {error}",
            "Preserve the exact completed totals; do not alter or recompute saved method records.",
            "frame.method_facts",
            canonical_id="analysis_frame.method_facts",
        )
