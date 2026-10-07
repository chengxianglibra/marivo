"""Precisely admitted Anchor placements; no backend fallback."""

from dataclasses import replace
from typing import Literal

from marivo.analysis.core.rules import AnchorObserve, ObserveMetric, RuleParameters
from marivo.analysis.methods.domain_preparation import implementations as preparation
from marivo.analysis.methods.domain_preparation import (
    remote_implementations,
    sqlite_implementations,
)
from marivo.analysis.methods.errors import reject
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
    placements: tuple[
        tuple[tuple[Literal["occurrence", "journey"], ...], Literal["ibis", "ibis_python"]], ...
    ] = ((("occurrence",), "ibis"), (("journey",), "ibis_python"))
    routes: tuple[Literal["ibis", "ibis_python"], ...] = ("ibis", "ibis_python")
    bases = tuple(
        p
        for p in preparation(MethodKey("occurrence.prepare"))
        + (
            (
                sqlite_implementations(MethodKey("occurrence.prepare"))
                + remote_implementations(MethodKey("occurrence.prepare"))
            )
            if method.name in ("anchor.bind", "anchor.observe")
            else ()
        )
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
                numeric_specialization="exact"
                if isinstance(base.key.shape, SourceShape)
                and base.key.shape.backend == "sqlite"
                and (domains == ("journey",))
                else "exact"
                if isinstance(base.key.shape, SourceShape) and base.key.shape.backend == "sqlite"
                else "consumer",
                qualification=Qualified(
                    "r93.c18.sqlite.journey_anchor_int64_us_utc@v1"
                    if isinstance(base.key.shape, SourceShape)
                    and base.key.shape.backend == "sqlite"
                    and domains == ("journey",)
                    else "r93.c18.sqlite.event_anchor_int64_us_utc@v1"
                    if isinstance(base.key.shape, SourceShape)
                    and base.key.shape.backend == "sqlite"
                    else f"r77.{method}.{base.key.shape}.{route}@v1",
                    "analysis.materialization.anchor_execution",
                    "tests/test_r93_journey_anchors.py"
                    if isinstance(base.key.shape, SourceShape)
                    and base.key.shape.backend == "sqlite"
                    and domains == ("journey",)
                    else "tests/test_r93_anchor_consumers.py"
                    if isinstance(base.key.shape, SourceShape)
                    and base.key.shape.backend == "sqlite"
                    else "tests/test_analysis_anchors_r77.py",
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
                numeric_specialization="exact"
                if isinstance(base.key.shape, SourceShape) and base.key.shape.backend == "sqlite"
                else "consumer",
                qualification=Qualified(
                    "r93.c18.sqlite.event_observe_sum_int64_us_utc@v1"
                    if isinstance(base.key.shape, SourceShape)
                    and base.key.shape.backend == "sqlite"
                    else f"r77.{method}.{base.key.shape}.{route}@v1",
                    "analysis.materialization.anchor_execution",
                    "tests/test_r93_anchor_consumers.py"
                    if isinstance(base.key.shape, SourceShape)
                    and base.key.shape.backend == "sqlite"
                    else "tests/test_analysis_anchors_r77.py",
                ),
            )
            for base in bases
            for route in routes
            if not isinstance(base.key.shape, FixedShape)
            and not (base.key.shape.backend != "duckdb" and route != "ibis_python")
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
                numeric_specialization="consumer",
                qualification=Qualified(
                    f"r77.{method}.{item.key.shape}.{item.key.input_types}@v1",
                    "analysis.materialization.graph_local_execution",
                    "tests/test_analysis_anchors_r77.py",
                ),
            )
        )
    return tuple(result)


def admit_observation(implementation: Implementation, params: RuleParameters) -> None:
    """Admit only the consumer's supported SQLite parameter variants."""
    if (
        isinstance(implementation.key.shape, SourceShape)
        and implementation.key.shape.backend == "sqlite"
        and implementation.key.method.name == "anchor.observe"
        and isinstance(implementation.qualification, Qualified)
        and implementation.qualification.consumer_id == "analysis.materialization.anchor_execution"
        and isinstance(params, AnchorObserve)
        and (
            params.composition is not None
            or len(params.observations) != 1
            or not isinstance(params.observations[0], ObserveMetric)
            or params.observations[0].amount_type != "int64"
            or params.observations[0].method != "sum"
            or params.observations[0].metric.empty_rule != "zero"
            or params.observations[0].fold is not None
            or params.observations[0].distinct_columns
            or params.observations[0].coordinates
            or params.observations[0].filters
        )
    ):
        reject(
            "one int64 sum-zero Anchor observation",
            repr(params),
            "Use one direct int64 sum-zero Metric without coordinates or filters; other variants require separate physical qualification.",
        )
