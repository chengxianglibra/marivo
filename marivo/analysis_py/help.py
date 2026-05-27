"""Small stdout-based introspection helper for marivo.analysis_py."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from types import ModuleType
from typing import cast

_TOP_LEVEL_ENTRIES = {
    "assess_quality": "inspect MetricFrame quality and recommend follow-ups",
    "observe": "build a MetricFrame from a metric and window",
    "compare": "compare two MetricFrames into a DeltaFrame",
    "decompose": "decompose a DeltaFrame into an AttributionFrame",
    "discover": "discover candidate follow-ups from analysis artifacts",
    "forecast": "project a time_series or panel MetricFrame forward",
    "correlate": "correlate compatible analysis frames",
    "hypothesis_test": "run a mean_changed paired test over compatible MetricFrames",
    "transform": "family-preserving reshape of a MetricFrame / DeltaFrame",
    "select": "pull a typed field out of a CandidateSet row",
    "alignment": "AlignmentPolicy variants and required arguments",
    "session": "session lifecycle and persistence helpers",
    "help": "print top-level or symbol-specific introspection",
}

_MATRIX_TOPICS = {"discover", "select", "transform", "alignment"}


def _list_top_level() -> str:
    lines = ["marivo.analysis_py - top-level entries:"]
    for name, summary in _TOP_LEVEL_ENTRIES.items():
        lines.append(f"  mv.{name:<14} {summary}")
    lines.append("")
    lines.append('Call mv.help("<name>") for a signature, docstring, or reference matrix.')
    return "\n".join(lines)


def _describe_callable(name: str, obj: Callable[..., object]) -> str:
    try:
        signature = str(inspect.signature(obj))
    except (TypeError, ValueError):
        signature = "(...)"
    lines = [f"{name}{signature}"]

    doc = inspect.getdoc(obj)
    if doc is None and getattr(obj, "__module__", None):
        module = inspect.getmodule(obj)
        doc = inspect.getdoc(module)
    if doc:
        lines.append("")
        lines.append(doc)
    namespace_methods = _namespace_methods(obj)
    if namespace_methods:
        lines.append("")
        lines.append("Methods:")
        for method_name in namespace_methods:
            method = getattr(obj, method_name)
            try:
                method_signature = str(inspect.signature(method))
            except (TypeError, ValueError):
                method_signature = "(...)"
            lines.append(f"  {name}.{method_name}{method_signature}")
    return "\n".join(lines)


def _namespace_methods(obj: object) -> tuple[str, ...]:
    if obj.__class__.__name__ == "TransformAPI":
        return ("filter", "slice", "rollup", "topk", "bottomk", "rank", "normalize", "window")
    if obj.__class__.__name__ == "DiscoverAPI":
        return (
            "point_anomalies",
            "period_shifts",
            "driver_axes",
            "interesting_slices",
            "interesting_windows",
            "cross_sectional_outliers",
        )
    return ()


def _describe_class(name: str, obj: type[object]) -> str:
    lines = [f"class {name}"]
    if obj.__doc__:
        doc = inspect.cleandoc(obj.__doc__)
        lines.append("")
        lines.append(doc)
    if name == "SemanticKindMismatchError":
        lines.append("")
        lines.append("Common compare case: pass two MetricFrame inputs to mv.compare(...).")
    return "\n".join(lines)


def _describe_module(name: str, obj: ModuleType) -> str:
    lines = [f"module {name}"]
    if doc := inspect.getdoc(obj):
        lines.append("")
        lines.append(doc)
    return "\n".join(lines)


def _format_discover_matrix() -> str:
    from marivo.analysis_py.intents.discover import (
        _OBJECTIVE_COMPATIBILITY,
        _OBJECTIVE_REQUIRED_KWARGS,
        _OBJECTIVE_TO_SHAPE,
    )

    lines = ["mv.discover objective helper matrix:", ""]
    header = f"  {'helper':<42}{'source':<14}{'semantic_kind':<40}{'shape':<26}required"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for objective in sorted(_OBJECTIVE_COMPATIBILITY):
        compat = _OBJECTIVE_COMPATIBILITY[objective]
        shape = _OBJECTIVE_TO_SHAPE[objective]
        required = ", ".join(_OBJECTIVE_REQUIRED_KWARGS.get(objective, ())) or "-"
        for source in sorted(compat):
            kinds = "|".join(sorted(compat[source]))
            helper = f"mv.discover.{objective}"
            lines.append(f"  {helper:<42}{source:<14}{kinds:<40}{shape:<26}{required}")
    lines.append("")
    lines.append("Example: mv.discover.driver_axes(delta,")
    lines.append('                                search_space=[mv.DimensionRef("country")])')
    lines.append("")
    lines.append('Compatibility: mv.discover(source, objective="...") still works.')
    return "\n".join(lines)


def _format_select_matrix() -> str:
    from marivo.analysis_py.intents.select import _FIELD_BY_SHAPE

    lines = ["mv.select attribute-by-shape matrix:", ""]
    for shape in sorted(_FIELD_BY_SHAPE):
        valid = ", ".join(sorted(_FIELD_BY_SHAPE[shape]))
        lines.append(f"  {shape:<28}{valid}")
    lines.append("")
    lines.append('Dot-paths "keys.<dim>" / "selector.<dim>" pull a single key out')
    lines.append('of the candidate row. Example: mv.select(cs, rank=1, attribute="window")')
    return "\n".join(lines)


def _format_transform_matrix() -> str:
    from marivo.analysis_py.intents.transform import _SUPPORTED_OPS

    op_required: dict[str, tuple[str, ...]] = {
        "filter": ("predicate",),
        "slice": ("where",),
        "rollup": ("drop_axes",),
        "topk": ("by", "limit"),
        "bottomk": ("by", "limit"),
        "rank": ("by",),
        "normalize": ("kind",),
        "window": ("window",),
    }
    lines = ["mv.transform op helper matrix (v1):", ""]
    for op in _SUPPORTED_OPS:
        required = ", ".join(op_required.get(op, ())) or "-"
        lines.append(f"  mv.transform.{op:<12}required: {required}")
    lines.append("")
    lines.append('Example: mv.transform.topk(delta, by="delta", limit=3, direction="decrease")')
    lines.append("")
    lines.append("normalize is MetricFrame-only in v1; DeltaFrame normalize is reserved.")
    lines.append('Compatibility: mv.transform(frame, op="...") still works.')
    return "\n".join(lines)


def _format_alignment_matrix() -> str:
    lines = ["mv.AlignmentPolicy variants:", ""]
    lines.append("  kind='calendar_bucket'         no calendar argument")
    lines.append("  kind='dow_aligned'             calendar=mv.CalendarRef(...) required")
    lines.append("  kind='holiday_aligned'         calendar=mv.CalendarRef(...) required")
    lines.append("  kind='holiday_and_dow_aligned' calendar=mv.CalendarRef(...) required")
    lines.append("")
    lines.append("Example: mv.AlignmentPolicy(kind='dow_aligned',")
    lines.append("                            calendar=mv.CalendarRef('cn_holidays'),")
    lines.append("                            period='month')")
    return "\n".join(lines)


_MATRIX_FORMATTERS: dict[str, Callable[[], str]] = {
    "discover": _format_discover_matrix,
    "select": _format_select_matrix,
    "transform": _format_transform_matrix,
    "alignment": _format_alignment_matrix,
}


def _resolve(symbol: str) -> object | None:
    import marivo.analysis_py as mv
    import marivo.analysis_py.errors as errors_mod

    if hasattr(mv, symbol):
        return cast("object", getattr(mv, symbol))
    if hasattr(errors_mod, symbol):
        return cast("object", getattr(errors_mod, symbol))
    return None


def help(symbol: str | None = None) -> None:
    """Print top-level or symbol-specific help for marivo.analysis_py."""

    if symbol is None:
        print(_list_top_level())
        return

    if symbol in _MATRIX_FORMATTERS:
        print(_MATRIX_FORMATTERS[symbol]())
        return

    obj = _resolve(symbol)
    if obj is None:
        print(f"Unknown symbol {symbol!r}. Call mv.help() to list available entries.")
        return

    if inspect.isclass(obj):
        print(_describe_class(symbol, obj))
        return

    if callable(obj):
        print(_describe_callable(symbol, obj))
        return

    if isinstance(obj, ModuleType):
        print(_describe_module(symbol, obj))
        return

    print(repr(obj))
