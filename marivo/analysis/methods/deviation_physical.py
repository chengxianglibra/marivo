"""Exact shape specialization of the connected deviation consumers."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Literal

from marivo.analysis.methods.physical import (
    DecimalType,
    FixedShape,
    Implementation,
    NoTime,
    QualificationKey,
    Qualified,
    ResourceRequirements,
    ScalarType,
    SourceShape,
    TimeShape,
    ValueType,
)
from marivo.analysis.methods.semantics import MethodKey

CELL_REASONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("undefined", ("no_valid_samples", "insufficient_samples", "zero_scale")),
)


def parse_type(value: str) -> ValueType:
    if value in ("int64", "float64"):
        return ScalarType("int64" if value == "int64" else "float64")
    match = re.fullmatch(r"decimal\((\d+),\s*(\d+)\)", value)
    if match is None:
        raise ValueError("deviation requires int64, float64 or Decimal(p,s)")
    return DecimalType(int(match[1]), int(match[2]))


def output_type(field: str, source: ValueType) -> ValueType:
    from marivo.analysis.methods.deviation_numeric import unit_type

    return (
        source
        if field == "observed"
        else ScalarType("float64")
        if field == "score"
        else unit_type(source)
    )


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    from marivo.analysis.methods.builtin import NUMERIC_CHECKS, PARTS

    times: tuple[NoTime | TimeShape, ...] = tuple(
        TimeShape("instant", unit, zone)
        for zone in ("UTC", "America/New_York")
        for unit in ("s", "ms", "us", "ns")
    )
    forms: tuple[tuple[Literal["table", "parquet"], str], ...] = (
        ("table", "native"),
        ("parquet", "parquet"),
    )
    shapes: tuple[SourceShape | FixedShape, ...] = (
        *(
            SourceShape("duckdb", form, kind, time)
            for form, kind in forms
            for time in (NoTime(), *times)
            if form == "parquet" or isinstance(time, NoTime) or time.unit == "us"
        ),
        *(FixedShape(time) for time in (NoTime(), *times)),
    )
    declarations = tuple(
        Implementation(
            QualificationKey(
                method,
                (typ,),
                (domain,),
                shape,
                "artifact_python" if isinstance(shape, FixedShape) else "ibis_python",
            ),
            NUMERIC_CHECKS,
            (
                *PARTS,
                "current_endpoint",
                "baseline_endpoint",
                "correspondence",
                "statistical_weight",
                "pair_counts",
            ),
            "exact" if method.name == "deviation.read" else "certified_statistical",
            ResourceRequirements(
                "complete", "caller" if isinstance(shape, FixedShape) else "producer", None
            ),
            Qualified(
                "r93-journey-" + method.name + "-float64-v1"
                if domain == "journey"
                else "r8-"
                + method.name.replace(".", "-")
                + ("-artifact_python-v1" if isinstance(shape, FixedShape) else "-ibis_python-v1"),
                "analysis.materialization.deviation_execution",
                "tests/test_r93_duration_ratio_unknown.py"
                if domain == "journey"
                else "tests/test_analysis_deviation_r82.py",
            ),
        )
        for domain in ("singleton", "entity", "group", "journey")
        for typ in (ScalarType("int64"), ScalarType("float64"))
        for shape in shapes
        if domain != "journey" or (typ == ScalarType("float64") and shape == FixedShape(NoTime()))
    )
    source_backends: tuple[Literal["sqlite", "clickhouse", "mysql", "postgres", "trino"], ...] = (
        "sqlite",
        "clickhouse",
        "mysql",
        "postgres",
        "trino",
    )
    sources = tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend=backend)),
            qualification=Qualified(
                "r93-" + backend + "-" + method.name + "-entity-int64-v1",
                "analysis.materialization.deviation_execution",
                "tests/test_r93_method_consumers.py",
            ),
        )
        for backend in source_backends
        for item in declarations
        if isinstance(item.key.shape, SourceShape)
        and item.key.shape.form == "table"
        and isinstance(item.key.shape.time, NoTime)
        and item.key.input_domains == ("entity",)
        and item.key.input_types == (ScalarType("int64"),)
    )
    return (*declarations, *sources)


def specialize(implementation: Implementation, key: QualificationKey) -> Implementation:
    if isinstance(key.shape, SourceShape) and key.shape.backend != "duckdb":
        allowed = (
            ((ScalarType("int64"),), (ScalarType("float64"),))
            if key.method.name == "deviation.read"
            else ((ScalarType("int64"),),)
        )
        if key.input_types not in allowed:
            return implementation
    if not key.method.name.startswith("deviation.") or implementation.key.method != key.method:
        return implementation
    if (
        implementation.key.shape != key.shape
        or implementation.key.route != key.route
        or not key.input_domains
        or implementation.key.input_domains[0] != key.input_domains[0]
    ):
        return implementation
    if key.input_types[0] not in (ScalarType("int64"), ScalarType("float64")) and not isinstance(
        key.input_types[0], DecimalType
    ):
        return implementation
    if any(
        t not in (ScalarType("string"), ScalarType("int64")) for t in key.input_types[1:]
    ) or any(d != key.input_domains[0] for d in key.input_domains):
        return implementation
    if key.method.name == "deviation.read" and len(key.input_types) != 1:
        return implementation
    return replace(implementation, key=key)
