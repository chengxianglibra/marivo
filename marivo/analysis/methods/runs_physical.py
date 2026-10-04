"""Exact physical declarations for complete-grid classification and run reads."""

from dataclasses import replace

from marivo.analysis.methods.deviation_physical import implementations as numeric_implementations
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    Implementation,
    QualificationKey,
    Qualified,
    ScalarType,
    ValueType,
)
from marivo.analysis.methods.semantics import MethodKey


def output_type(field: str) -> ValueType:
    return (
        DurationType("us")
        if field == "duration"
        else ScalarType("timestamp")
        if field in ("start", "end")
        else ScalarType("int64")
    )


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    return tuple(
        replace(
            item,
            key=replace(item.key, method=method),
            precision="exact",
            qualification=Qualified(
                "r8-" + method.name.replace(".", "-") + "-" + item.key.route + "-v1",
                "analysis.materialization.runs_execution",
                "tests/test_analysis_runs_r83.py",
            ),
        )
        for item in numeric_implementations(MethodKey("deviation.read"))
    )


def specialize(implementation: Implementation, key: QualificationKey) -> Implementation:
    if (
        implementation.key.shape != key.shape
        or implementation.key.route != key.route
        or implementation.key.input_domains[0] != key.input_domains[0]
    ):
        return implementation
    allowed = (
        ScalarType("int64"),
        ScalarType("float64"),
        ScalarType("timestamp"),
        ScalarType("date"),
        ScalarType("boolean"),
        ScalarType("string"),
    )
    if any(
        t not in allowed and not isinstance(t, (DecimalType, DurationType)) for t in key.input_types
    ) or any(d != key.input_domains[0] for d in key.input_domains):
        return implementation
    return replace(implementation, key=key)
