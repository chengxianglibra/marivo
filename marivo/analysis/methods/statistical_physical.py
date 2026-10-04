"""Exact ordered shape declarations for the connected statistical consumers."""

from dataclasses import replace

from marivo.analysis.methods.deviation_physical import implementations as numeric_implementations
from marivo.analysis.methods.physical import (
    DecimalType,
    Implementation,
    QualificationKey,
    Qualified,
    ScalarType,
)
from marivo.analysis.methods.semantics import MethodKey


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    association = method.name.startswith("association.") and method.name != "association.read"
    return tuple(
        replace(
            item,
            key=replace(
                item.key,
                method=method,
                input_types=(item.key.input_types[0],) * (2 if association else 1),
                input_domains=(item.key.input_domains[0],) * (2 if association else 1),
            ),
            precision="exact" if method.name.endswith(".read") else "certified_statistical",
            qualification=Qualified(
                "r8-" + method.name.replace(".", "-") + "-" + item.key.route + "-v1",
                "analysis.materialization.statistical_execution",
                "tests/test_analysis_statistics_r84.py",
            ),
        )
        for item in numeric_implementations(MethodKey("deviation.read"))
        if item.key.input_domains[0] != "singleton"
    )


def specialize(implementation: Implementation, key: QualificationKey) -> Implementation:
    if (
        implementation.key.shape != key.shape
        or implementation.key.route != key.route
        or implementation.key.method != key.method
        or key.input_domains[0] != implementation.key.input_domains[0]
        or any(d != key.input_domains[0] for d in key.input_domains)
    ):
        return implementation
    if any(
        t not in (ScalarType("int64"), ScalarType("float64"), ScalarType("boolean"))
        and not isinstance(t, DecimalType)
        for t in key.input_types
    ):
        return implementation
    if (
        key.method.name.startswith("association.")
        and key.method.name != "association.read"
        and not 2 <= len(key.input_types) <= 16
    ):
        return implementation
    if (key.method.name.startswith("forecast.") or key.method.name == "association.read") and len(
        key.input_types
    ) != 1:
        return implementation
    return replace(implementation, key=key)
