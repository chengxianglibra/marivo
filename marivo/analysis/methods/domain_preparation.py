"""Exact qualification declarations for R7 preparation and local consumers."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo.analysis.core.model import DomainKind
from marivo.analysis.methods.physical import (
    DurationType,
    FixedShape,
    Implementation,
    NoTime,
    QualificationKey,
    Qualified,
    ResourceRequirements,
    ScalarType,
    SourceShape,
    TimeShape,
)
from marivo.analysis.methods.semantics import MethodKey


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    from marivo.analysis.methods.builtin import NUMERIC_CHECKS

    shapes: list[SourceShape | FixedShape] = []
    units: tuple[Literal["s", "ms", "us", "ns"], ...] = ("s", "ms", "us", "ns")
    for form in ("table", "parquet"):
        for unit in ("us",) if form == "table" else units:
            for zone in ("UTC", "America/New_York"):
                shapes.append(
                    SourceShape(
                        "duckdb",
                        "table" if form == "table" else "parquet",
                        "native" if form == "table" else "parquet",
                        TimeShape("instant", unit, zone),
                    )
                )
    shapes.extend(
        FixedShape(TimeShape("instant", "us", zone)) for zone in ("UTC", "America/New_York")
    )
    return tuple(
        Implementation(
            QualificationKey(
                method,
                (key_type,),
                ("occurrence" if isinstance(shape, FixedShape) else "entity",),
                shape,
                "artifact_python" if isinstance(shape, FixedShape) else "ibis",
            ),
            NUMERIC_CHECKS,
            ("subject", "occurrences"),
            "exact",
            ResourceRequirements(
                "stream", "caller" if isinstance(shape, FixedShape) else "producer", None
            ),
            Qualified(
                f"r7.occurrence.prepare.{'artifact_python' if isinstance(shape, FixedShape) else 'ibis'}@v1",
                "analysis.materialization.domain_preparation",
                "tests/test_analysis_domain_preparation_r72.py",
            ),
            contract_version=1,
        )
        for shape in shapes
        for key_type in (
            (ScalarType("int64"),)
            if isinstance(shape, FixedShape)
            else (ScalarType("int64"), ScalarType("string"))
        )
    )


def sqlite_implementations(method: MethodKey) -> tuple[Implementation, ...]:
    """Bind only native int64 UTC-us occurrence capture; do not fan out consumers."""
    return tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend="sqlite")),
            qualification=Qualified(
                "r93.c11.sqlite.occurrence_prepare_int64_us_utc@v1",
                "analysis.materialization.domain_preparation",
                "tests/test_r93_journey_consumers.py",
            ),
        )
        for item in implementations(method)
        if isinstance(item.key.shape, SourceShape)
        and item.key.shape
        == SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC"))
        and item.key.input_types == (ScalarType("int64"),)
    )


def remote_implementations(method: MethodKey) -> tuple[Implementation, ...]:
    """Connect native int64 UTC-us preparations under independent-read authority."""
    backends: tuple[Literal["postgres", "mysql", "trino", "clickhouse"], ...] = (
        "postgres",
        "mysql",
        "trino",
        "clickhouse",
    )
    return tuple(
        replace(
            item,
            key=replace(item.key, shape=replace(item.key.shape, backend=backend)),
            qualification=Qualified(
                f"r93.{backend}.occurrence_prepare_int64_us_utc@v1",
                "analysis.materialization.domain_preparation",
                "tests/test_r93_remote_domain_consumers.py",
            ),
        )
        for backend in backends
        for item in implementations(method)
        if item.key.shape
        == SourceShape("duckdb", "table", "native", TimeShape("instant", "us", "UTC"))
        and isinstance(item.key.shape, SourceShape)
        and item.key.input_types == (ScalarType("int64"),)
    )


def consumers(method: MethodKey) -> tuple[Implementation, ...]:
    bases = tuple(
        item
        for item in implementations(MethodKey("occurrence.prepare"))
        if item.key.input_types == (ScalarType("int64"),)
    )
    if method.name == "map_correspond":
        occurrence_images = tuple(
            replace(
                item,
                key=replace(
                    item.key,
                    method=method,
                    input_domains=("occurrence",),
                    route="artifact_python"
                    if isinstance(item.key.shape, FixedShape)
                    else "ibis_python",
                ),
                parts=("subject",),
                qualification=Qualified(
                    f"r72.subject_image.{item.key.shape}@v1",
                    "analysis.materialization.graph_local_execution",
                    "tests/test_analysis_domain_preparation_r72.py",
                ),
            )
            for item in bases
        )
        units: tuple[Literal["s", "ms", "us", "ns"], ...] = ("s", "ms", "us", "ns")
        times: tuple[NoTime | TimeShape, ...] = (
            NoTime(),
            *(
                TimeShape("instant", unit, zone)
                for unit in units
                for zone in ("UTC", "America/New_York")
            ),
        )
        shapes: tuple[SourceShape | FixedShape, ...] = (
            *(
                SourceShape("duckdb", form, "native" if form == "table" else "parquet", time)
                for form in ("table", "parquet")
                for time in times
                if form == "parquet" or isinstance(time, NoTime) or time.unit == "us"
            ),
            *(FixedShape(time) for time in times if isinstance(time, TimeShape)),
        )
        image_domains: tuple[DomainKind, ...] = ("entity", "group")
        return (
            *occurrence_images,
            *(
                Implementation(
                    QualificationKey(
                        method,
                        (typ,),
                        (domain,),
                        shape,
                        "artifact_python" if isinstance(shape, FixedShape) else "ibis_python",
                    ),
                    bases[0].checks,
                    ("subject",),
                    "exact",
                    ResourceRequirements(
                        "complete", "caller" if isinstance(shape, FixedShape) else "producer", None
                    ),
                    Qualified(
                        f"r83.subject_image.{shape}@v1"
                        if domain == "group"
                        else f"r82.subject_image.{shape}@v1",
                        "analysis.materialization.graph_local_execution",
                        "tests/test_analysis_deviation_f11_r82.py",
                    ),
                )
                for shape in (*shapes, FixedShape(NoTime()))
                for domain in image_domains
                if domain == "group"
                or not (isinstance(shape, FixedShape) and isinstance(shape.time, NoTime))
                for typ in (ScalarType("int64"), ScalarType("float64"), ScalarType("string"))
            ),
        )
    if method.name in ("metric.observe", "metric.sum_zero", "metric.count", "metric.mean"):
        return tuple(
            replace(
                item,
                key=replace(
                    item.key,
                    method=method,
                    input_types=(selected_type, original_type),
                    input_domains=("entity",) * 2,
                    route="ibis_python",
                ),
                checks=item.checks,
                parts=("subject", "original_state", "coverage", "coordinate_state"),
                precision="finite_float64" if method.name == "metric.mean" else "checked_int64",
                contract_version=4 if method.name == "metric.mean" else 3,
                qualification=Qualified(
                    f"r72.prepared_observation.{method}.{item.key.shape}@v1",
                    "analysis.materialization.graph_preparation",
                    "tests/test_analysis_domain_preparation_r72.py",
                ),
            )
            for item in bases
            for original_type in (ScalarType("int64"), ScalarType("string"))
            for selected_type in (
                ScalarType("int64"),
                ScalarType("boolean"),
                ScalarType("string"),
                ScalarType("timestamp"),
                DurationType("us"),
            )
            if isinstance(item.key.shape, SourceShape)
        )
    return ()
