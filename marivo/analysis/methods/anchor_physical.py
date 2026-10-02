"""Precisely admitted Anchor placements; no backend fallback."""

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
    placements: tuple[
        tuple[tuple[Literal["occurrence", "journey"], ...], Literal["ibis", "ibis_python"]], ...
    ] = ((("occurrence",), "ibis"), (("journey",), "ibis_python"))
    routes: tuple[Literal["ibis", "ibis_python"], ...] = ("ibis", "ibis_python")
    bases = tuple(
        p
        for p in preparation(MethodKey("occurrence.prepare"))
        if p.key.input_types == (ScalarType("int64"),)
    )
    fixed = next(p for p in bases if isinstance(p.key.shape, FixedShape))
    bases = (*bases, replace(fixed, key=replace(fixed.key, shape=FixedShape(NoTime()))))
    return (
        tuple(
            replace(
                base,
                key=replace(
                    base.key,
                    method=method,
                    input_types=(ScalarType("int64"),) * len(domains),
                    input_domains=domains,
                    route="artifact_python" if isinstance(base.key.shape, FixedShape) else route,
                ),
                parts=("anchor", "subject", "occurrences", "journey", "original_state", "coverage"),
                qualification=Qualified(
                    f"r77.{method}.{base.key.shape}.{route}@v1",
                    "analysis.materialization.anchor_execution",
                    "tests/test_analysis_anchors_r77.py",
                ),
            )
            for base in bases
            for domains, route in placements
            if method.name == "anchor.bind"
            and (not isinstance(base.key.shape, FixedShape) or domains == ("journey",))
        )
        if method.name == "anchor.bind"
        else tuple(
            replace(
                base,
                key=replace(
                    base.key,
                    method=method,
                    input_types=(ScalarType("int64"),) * 2,
                    input_domains=("anchor", "entity"),
                    route="artifact_python" if isinstance(base.key.shape, FixedShape) else route,
                ),
                parts=("anchor", "subject", "original_state", "coverage"),
                qualification=Qualified(
                    f"r77.{method}.{base.key.shape}.{route}@v1",
                    "analysis.materialization.anchor_execution",
                    "tests/test_analysis_anchors_r77.py",
                ),
            )
            for base in bases
            for route in routes
            if not isinstance(base.key.shape, FixedShape)
        )
    )


def consumers(method: MethodKey) -> tuple[Implementation, ...]:
    """Admit Anchor part transport and current-row reductions through R5 owners."""
    from marivo.analysis.methods.builtin import _shape_implementations

    if method.name not in (
        "parts_transport",
        "map_correspond",
        "row.count",
        "row.count_defined",
        "row.sum",
        "row.mean",
        "row.min",
        "row.max",
    ):
        return ()
    result = []
    for item in _shape_implementations(method):
        if item.key.input_domains != ("entity",):
            continue
        result.append(
            replace(
                item,
                key=replace(
                    item.key,
                    input_domains=("anchor",),
                    route="artifact_python"
                    if isinstance(item.key.shape, FixedShape)
                    else "ibis_python",
                ),
                parts=tuple(dict.fromkeys((*item.parts, "anchor"))),
                qualification=Qualified(
                    f"r77.{method}.{item.key.shape}.{item.key.input_types}@v1",
                    "analysis.materialization.graph_local_execution",
                    "tests/test_analysis_anchors_r77.py",
                ),
            )
        )
    return tuple(result)
