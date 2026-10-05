"""Exact funnel placements in the existing governed preparation/local pipeline."""

from dataclasses import replace

from marivo.analysis.core.model import DomainKind
from marivo.analysis.methods.domain_preparation import implementations as preparations
from marivo.analysis.methods.domain_preparation import sqlite_implementations
from marivo.analysis.methods.physical import (
    FixedShape,
    Implementation,
    NoTime,
    Qualified,
    ScalarName,
    ScalarType,
    SourceShape,
)
from marivo.analysis.methods.semantics import MethodKey


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    bases = tuple(
        i
        for i in preparations(MethodKey("occurrence.prepare"))
        + (
            sqlite_implementations(MethodKey("occurrence.prepare"))
            if method.name in ("funnel.entry_axes", "funnel.reduce")
            else ()
        )
        if i.key.input_types == (ScalarType("int64"),)
    )
    fixed = next(i for i in bases if isinstance(i.key.shape, FixedShape))
    bases = (*bases, replace(fixed, key=replace(fixed.key, shape=FixedShape(NoTime()))))
    contracts: dict[str, tuple[tuple[tuple[ScalarName, ...], tuple[DomainKind, ...]], ...]] = {
        "funnel.entry_axes": ((("int64",), ("occurrence",)),),
        "funnel.reduce": (
            (("int64",), ("journey",)),
            (("int64", "int64"), ("journey", "occurrence")),
        ),
        "funnel.compare": ((("int64", "int64"), ("group", "group")),),
        "funnel.read": ((("int64",), ("group",)), (("float64",), ("group",))),
        "funnel_ratio_mix": ((("float64", "float64"), ("group", "group")),),
    }
    return tuple(
        replace(
            base,
            key=replace(
                base.key,
                method=method,
                input_types=tuple(ScalarType(t) for t in types),
                input_domains=domains,
                route="artifact_python"
                if isinstance(base.key.shape, FixedShape)
                else "ibis"
                if method.name == "funnel.entry_axes"
                else "ibis_python",
            ),
            parts=("entry_axes",)
            if method.name == "funnel.entry_axes"
            else ("funnel_state", "finding_policy"),
            qualification=Qualified(
                f"r93.c12.sqlite.{method.name}.int64_us_utc@v1"
                if isinstance(base.key.shape, SourceShape) and base.key.shape.backend == "sqlite"
                else f"r7.{method.name}.{'artifact_python' if isinstance(base.key.shape, FixedShape) else 'ibis' if method.name == 'funnel.entry_axes' else 'ibis_python'}@v1",
                "analysis.materialization.funnel_execution",
                "tests/test_r93_journey_consumers.py"
                if isinstance(base.key.shape, SourceShape) and base.key.shape.backend == "sqlite"
                else "tests/test_analysis_funnel_r74.py",
            ),
        )
        for base in bases
        for types, domains in contracts[method.name]
        if method.name != "funnel.entry_axes" or not isinstance(base.key.shape, FixedShape)
        if not (
            isinstance(base.key.shape, SourceShape)
            and base.key.shape.backend == "sqlite"
            and method.name == "funnel.reduce"
            and len(types) != 2
        )
    )
