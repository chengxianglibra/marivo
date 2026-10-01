"""Exact qualification declarations for R7 preparation and local consumers."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
    QualificationKey,
    Qualified,
    ResourceRequirements,
    ScalarType,
    SourceShape,
    TimeShape,
)
from marivo.analysis.methods.semantics import MethodKey


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
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
                (ScalarType("int64"),),
                ("occurrence" if isinstance(shape, FixedShape) else "entity",),
                shape,
                "artifact_python" if isinstance(shape, FixedShape) else "ibis",
            ),
            (),
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
    )


def consumers(method: MethodKey) -> tuple[Implementation, ...]:
    bases = implementations(MethodKey("occurrence.prepare"))
    if method.name == "map_correspond":
        return tuple(
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
    if method.name in ("metric.observe", "metric.sum_zero", "metric.count", "metric.mean"):
        return tuple(
            replace(
                item,
                key=replace(
                    item.key,
                    method=method,
                    input_types=(ScalarType("int64"),) * 2,
                    input_domains=("entity",) * 2,
                    route="ibis_python",
                ),
                checks=("source.contribution_partition@v1", "source.complete_coverage@v1"),
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
            if isinstance(item.key.shape, SourceShape)
        )
    return ()
