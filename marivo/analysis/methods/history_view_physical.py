"""Exact qualification of canonical History consumers and typed owned fields."""

from dataclasses import replace

from marivo.analysis.core.history_types import StateAt
from marivo.analysis.core.model import DomainKind
from marivo.analysis.core.rules import HistoryAxesPrepare, HistoryRead, HistoryView
from marivo.analysis.methods.domain_preparation import implementations as preparations
from marivo.analysis.methods.physical import (
    DurationType,
    FixedShape,
    Implementation,
    NoTime,
    Qualified,
    ScalarType,
    ValueType,
)
from marivo.analysis.methods.semantics import MethodKey


def output_type(params: HistoryView | HistoryRead | HistoryAxesPrepare) -> ValueType:
    if isinstance(params, HistoryView) and isinstance(params.request, StateAt):
        return ScalarType("boolean")
    if isinstance(params, HistoryRead):
        field = params.field
        if field.endswith("duration"):
            return DurationType("us")
        return ScalarType(
            "boolean"
            if field == "left_clipped"
            else "timestamp"
            if field in ("start", "end", "occurred_at")
            else "float64"
            if field.startswith("share_")
            else "int64"
            if field == "count" or field.endswith("count")
            else "string"
        )
    return ScalarType("int64")


def implementations(method: MethodKey) -> tuple[Implementation, ...]:
    contracts: tuple[tuple[tuple[ValueType, ...], tuple[DomainKind, ...]], ...] = (
        (((ScalarType("int64"), ScalarType("int64")), ("entity", "entity")),)
        if method.name == "history.distribution"
        else tuple(
            ((ScalarType(t),), (d,))
            for d in (
                ("entity", "group", "occurrence", "interval")
                if method.name == "history.read"
                else ("entity",)
            )
            for t in (("int64", "string") if method.name == "history.axes" else ("int64",))
        )
    )
    if method.name == "history.distribution":
        contracts += (((ScalarType("int64"),), ("entity",)),)
    bases = tuple(
        b
        for b in preparations(MethodKey("occurrence.prepare"))
        if b.key.input_types == (ScalarType("int64"),)
    )
    fixed = next(b for b in bases if isinstance(b.key.shape, FixedShape))
    bases = (*bases, replace(fixed, key=replace(fixed.key, shape=FixedShape(NoTime()))))
    return tuple(
        replace(
            base,
            key=replace(
                base.key,
                method=method,
                input_types=types,
                input_domains=domains,
                route="artifact_python"
                if isinstance(base.key.shape, FixedShape)
                else "ibis"
                if method.name == "history.axes"
                else "ibis_python",
            ),
            parts=("history", "history_view", "subject"),
            qualification=Qualified(
                f"r7.{method.name}.{'artifact_python' if isinstance(base.key.shape, FixedShape) else 'ibis' if method.name == 'history.axes' else 'ibis_python'}@v1",
                "analysis.materialization.history_views",
                "tests/test_analysis_history_r76.py",
            ),
        )
        for base in bases
        for types, domains in contracts
        if method.name != "history.axes" or not isinstance(base.key.shape, FixedShape)
    )


def consumers(method: MethodKey) -> tuple[Implementation, ...]:
    """Qualify retained interval selection, Subject image and exact Duration mean."""
    from marivo.analysis.methods.journey_physical import consumers as journey_consumers

    interval_consumers = tuple(
        replace(
            item,
            key=replace(
                item.key,
                input_domains=tuple(
                    domain if d == "journey" else d for d in item.key.input_domains
                ),
            ),
            parts=tuple(dict.fromkeys((*item.parts, "history_view"))),
            qualification=Qualified(
                f"r76.{method}.{item.key.shape}@v1",
                "analysis.materialization.graph_local_execution",
                "tests/test_analysis_history_r76.py",
            ),
        )
        for item in journey_consumers(method)
        if "journey" in item.key.input_domains
        for domain in (
            ("interval", "occurrence")
            if method.name in ("map_correspond", "parts_transport")
            and item.key.shape == FixedShape(NoTime())
            else ("interval",)
        )
    )

    local_transport = tuple(
        replace(
            item,
            key=replace(item.key, input_domains=(domain,)),
            parts=("subject", "history_view"),
            qualification=Qualified(
                f"r76.{method}.{item.key.shape}.{domain}@v1",
                "analysis.materialization.graph_local_execution",
                "tests/test_analysis_history_r76.py",
            ),
        )
        for item in journey_consumers(method)
        if method.name == "parts_transport"
        and "journey" in item.key.input_domains
        and not isinstance(item.key.shape, FixedShape)
        for domain in ("entity", "group", "occurrence")
    )
    counts = tuple(
        replace(
            item,
            key=replace(item.key, method=method, input_types=(value,), input_domains=(domain,)),
            parts=("row_state",),
            precision="checked_int64",
            qualification=Qualified(
                f"r76.{method}.{item.key.shape}.{domain}.{value}@v1",
                "analysis.materialization.graph_local_execution",
                "tests/test_analysis_history_r76.py",
            ),
        )
        for item in implementations(MethodKey("history.read"))
        if item.key.input_domains == ("entity",)
        and item.key.input_types == (ScalarType("int64"),)
        and method.name in ("row.count", "row.count_defined")
        for value in (
            ScalarType("int64"),
            ScalarType("float64"),
            ScalarType("boolean"),
            ScalarType("string"),
            ScalarType("timestamp"),
            DurationType("us"),
        )
        for domain in ("interval", "occurrence")
    )
    return (*interval_consumers, *local_transport, *counts)
