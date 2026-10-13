"""Exact qualification declarations for R7 preparation and local consumers."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo.analysis.core.model import DomainKind
from marivo.analysis.methods.physical import (
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


def consumers(method: MethodKey) -> tuple[Implementation, ...]:
    from marivo.analysis.methods.builtin import NUMERIC_CHECKS

    units: tuple[Literal["s", "ms", "us", "ns"], ...] = ("s", "ms", "us", "ns")
    bases = tuple(
        Implementation(
            QualificationKey(method, (ScalarType("int64"),), ("entity",), shape, "ibis_python"),
            NUMERIC_CHECKS,
            ("subject",),
            "exact",
            ResourceRequirements("stream", "producer", None),
            Qualified(
                "prepared_observation.source@v1",
                "analysis.materialization.graph_preparation",
                "tests/analysis/discover/test_analysis_deviation.py",
            ),
        )
        for form in ("table", "parquet")
        for unit in (("us",) if form == "table" else units)
        for zone in ("UTC", "America/New_York")
        for shape in (
            SourceShape(
                "duckdb",
                form,
                "native" if form == "table" else "parquet",
                TimeShape("instant", unit, zone),
            ),
        )
    )
    if method.name == "map_correspond":
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
            *(
                Implementation(
                    QualificationKey(
                        method,
                        (typ,),
                        (domain,),
                        shape,
                        "artifact_python" if isinstance(shape, FixedShape) else "ibis_python",
                    ),
                    NUMERIC_CHECKS,
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
                numeric_specialization="consumer",
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
            )
            if isinstance(item.key.shape, SourceShape)
        )
    return ()
