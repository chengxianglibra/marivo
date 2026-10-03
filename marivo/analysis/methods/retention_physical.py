"""Bounded retention routes; fixed starts do not gain unseen return inputs."""

from dataclasses import replace
from typing import Literal

from marivo.analysis.methods.domain_preparation import implementations as preparation
from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
    NoTime,
    Qualified,
    ScalarType,
)
from marivo.analysis.methods.semantics import MethodKey


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    bases = tuple(
        p
        for p in preparation(MethodKey("occurrence.prepare"))
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
            qualification=Qualified(
                f"r78.{method}.{base.key.shape}.{'artifact_python' if isinstance(base.key.shape, FixedShape) else route}@v1",
                "analysis.materialization.retention_execution",
                "tests/test_analysis_retention_r78.py",
            ),
        )
        for base in bases
        for route in routes
        if method.name != "anchor.retention" or not isinstance(base.key.shape, FixedShape)
    )


def consumers(method: MethodKey) -> tuple[Implementation, ...]:
    if method.name not in ("parts_transport", "map_correspond"):
        return ()
    return tuple(
        replace(
            base,
            key=replace(base.key, method=method, input_domains=(domain,)),
            parts=("subject", "retention"),
        )
        for base in implementations(MethodKey("retention.by_subject"))
        for domain in ("entity",)
        if not isinstance(base.key.shape, FixedShape)
    )
