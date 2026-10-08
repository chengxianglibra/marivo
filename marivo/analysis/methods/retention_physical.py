"""Bounded retention routes; fixed starts do not gain unseen return inputs."""

from dataclasses import replace
from typing import Literal

from marivo.analysis.methods.domain_preparation import implementations as preparation
from marivo.analysis.methods.domain_preparation import (
    remote_implementations,
    sqlite_implementations,
)
from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
    NoTime,
    Qualified,
    ScalarType,
    SourceShape,
)
from marivo.analysis.methods.semantics import MethodKey


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    bases = tuple(
        p
        for p in preparation(MethodKey("occurrence.prepare"))
        + (
            (
                sqlite_implementations(MethodKey("occurrence.prepare"))
                + remote_implementations(MethodKey("occurrence.prepare"))
            )
            if method.name == "anchor.retention"
            else ()
        )
        if p.key.input_types == (ScalarType("int64"),)
    )
    fixed = next(p for p in bases if isinstance(p.key.shape, FixedShape))
    bases = (*bases, replace(fixed, key=replace(fixed.key, shape=FixedShape(NoTime()))))
    routes: tuple[Literal["ibis", "ibis_python", "artifact_python"], ...] = (
        ("ibis", "ibis_python") if method.name == "anchor.retention" else ("ibis_python",)
    )
    return tuple(
        replace(
            base,
            key=replace(
                base.key,
                method=method,
                input_types=(ScalarType("int64"), ScalarType("int64"))
                if method.name == "anchor.retention"
                else (ScalarType("boolean"),),
                input_domains=("anchor", "occurrence")
                if method.name == "anchor.retention"
                else ("anchor",),
                route="artifact_python" if isinstance(base.key.shape, FixedShape) else route,
            ),
            parts=("subject", "anchor", "occurrences", "retention"),
            numeric_specialization="exact"
            if isinstance(base.key.shape, SourceShape) and base.key.shape.backend == "sqlite"
            else "consumer",
            qualification=Qualified(
                f"r93.c18.sqlite.event_retention_int64_us_utc.{route}@v1"
                if isinstance(base.key.shape, SourceShape) and base.key.shape.backend == "sqlite"
                else f"r78.{method}.{base.key.shape}.{'artifact_python' if isinstance(base.key.shape, FixedShape) else route}@v1",
                "analysis.materialization.retention_execution",
                "tests/test_r93_anchor_consumers.py"
                if isinstance(base.key.shape, SourceShape) and base.key.shape.backend == "sqlite"
                else "tests/test_analysis_retention_r78.py",
            ),
        )
        for base in bases
        for route in routes
        if not (
            isinstance(base.key.shape, SourceShape)
            and base.key.shape.backend != "duckdb"
            and route != "ibis_python"
        )
        if method.name != "anchor.retention" or not isinstance(base.key.shape, FixedShape)
    )


def consumers(method: MethodKey) -> tuple[Implementation, ...]:
    """Qualify shared Entity transport without hiding retained History views."""
    if method.name not in ("parts_transport", "map_correspond"):
        return ()
    return tuple(
        replace(
            base,
            key=replace(base.key, method=method, input_domains=(domain,)),
            parts=("subject", "history_view", "retention"),
            numeric_specialization="consumer",
            qualification=Qualified(
                f"r79.subject_transport.{method}.{base.key.shape}@v1",
                "analysis.materialization.graph_local_execution",
                "tests/test_analysis_history_r76.py; tests/test_analysis_retention_r78.py",
            ),
        )
        for base in implementations(MethodKey("retention.by_subject"))
        for domain in ("entity",)
        if not isinstance(base.key.shape, FixedShape)
    )
