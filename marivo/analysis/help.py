"""Small stdout-based introspection helper for marivo.analysis."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from types import ModuleType
from typing import cast

_TOP_LEVEL_ENTRIES = {
    "session.observe": "build a MetricFrame from a metric and window",
    "session.compare": "compare two MetricFrames into a DeltaFrame",
    "session.decompose": "decompose a DeltaFrame into an AttributionFrame",
    "session.discover": "discover candidate follow-ups from analysis artifacts",
    "session.transform": "family-preserving reshape of a MetricFrame / DeltaFrame",
    "session.correlate": "correlate compatible analysis frames",
    "session.forecast": "project a time_series or panel MetricFrame forward",
    "session.assess_quality": "inspect MetricFrame quality and recommend follow-ups",
    "session.hypothesis_test": "run a mean_changed paired test over compatible MetricFrames",
    "CandidateSet.select": "pull a typed field out of a CandidateSet row",
    "alignment": "AlignmentPolicy variants and required arguments",
    "calendar": "project-local calendar JSON file shape",
    "session": "session lifecycle and persistence helpers",
    "help": "print top-level or symbol-specific introspection",
}

_MATRIX_TOPICS = {"discover", "select", "transform", "alignment", "calendar"}


def _list_top_level() -> str:
    lines = ["marivo.analysis - top-level entries:"]
    for name, summary in _TOP_LEVEL_ENTRIES.items():
        prefix = "mv." if name in {"alignment", "session", "help"} else ""
        lines.append(f"  {prefix}{name:<22} {summary}")
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
        lines.append("Common compare case: pass two MetricFrame inputs to session.compare(...).")
    return "\n".join(lines)


def _describe_module(name: str, obj: ModuleType) -> str:
    lines = [f"module {name}"]
    if doc := inspect.getdoc(obj):
        lines.append("")
        lines.append(doc)
    return "\n".join(lines)


def _format_discover_matrix() -> str:
    from marivo.analysis.intents.discover import (
        _OBJECTIVE_COMPATIBILITY,
        _OBJECTIVE_REQUIRED_KWARGS,
        _OBJECTIVE_TO_SHAPE,
    )

    lines = ["session.discover objective helper matrix:", ""]
    header = f"  {'helper':<42}{'source':<14}{'semantic_kind':<40}{'shape':<26}required"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for objective in sorted(_OBJECTIVE_COMPATIBILITY):
        compat = _OBJECTIVE_COMPATIBILITY[objective]
        shape = _OBJECTIVE_TO_SHAPE[objective]
        required = ", ".join(_OBJECTIVE_REQUIRED_KWARGS.get(objective, ())) or "-"
        for source in sorted(compat):
            kinds = "|".join(sorted(compat[source]))
            helper = f"session.discover.{objective}"
            lines.append(f"  {helper:<42}{source:<14}{kinds:<40}{shape:<26}{required}")
    lines.append("")
    lines.append("Example: session.discover.driver_axes(delta,")
    lines.append('                                     search_space=[mv.DimensionRef("country")])')
    return "\n".join(lines)


def _format_select_matrix() -> str:
    from marivo.analysis.intents.select import _FIELD_BY_SHAPE

    lines = ["CandidateSet.select attribute-by-shape matrix:", ""]
    for shape in sorted(_FIELD_BY_SHAPE):
        valid = ", ".join(sorted(_FIELD_BY_SHAPE[shape]))
        lines.append(f"  {shape:<28}{valid}")
    lines.append("")
    lines.append('Dot-paths "keys.<dim>" / "selector.<dim>" pull a single key out')
    lines.append('of the candidate row. Example: cs.select(rank=1, attribute="window")')
    return "\n".join(lines)


def _format_transform_matrix() -> str:
    from marivo.analysis.intents.transform import _SUPPORTED_OPS

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
    lines = ["session.transform op helper matrix (v1):", ""]
    for op in _SUPPORTED_OPS:
        required = ", ".join(op_required.get(op, ())) or "-"
        lines.append(f"  session.transform.{op:<12}required: {required}")
    lines.append("")
    lines.append('Example: session.transform.topk(delta, by="delta", limit=3, order="decrease")')
    lines.append("")
    lines.append("normalize is MetricFrame-only in v1; DeltaFrame normalize is reserved.")
    return "\n".join(lines)


def _format_alignment_matrix() -> str:
    lines = ["mv.AlignmentPolicy variants:", ""]
    lines.append("Valid kind values:")
    lines.append("  kind='window_bucket'         no calendar argument")
    lines.append("  kind='dow_aligned'             calendar=mv.CalendarRef(...) required")
    lines.append("  kind='holiday_aligned'         calendar=mv.CalendarRef(...) required")
    lines.append("  kind='holiday_and_dow_aligned' calendar=mv.CalendarRef(...) required")
    lines.append("")
    lines.append("window_bucket behavior:")
    lines.append("  window_bucket default -> align by ordinal bucket position")
    lines.append("  window_bucket mode='calendar_bucket' -> outer join absolute bucket keys")
    lines.append("  strict_lengths=True -> require equal ordinal bucket counts")
    lines.append("  sparse observed buckets become NaN values rather than alignment failures")
    lines.append("  there is no separate kind='ordinal'")
    lines.append("")
    lines.append("Calendar alignment output columns:")
    lines.append("  align_key is a compact JSON object string; fields depend on kind")
    lines.append('  dow: {"kind":"dow","iso_weekday":2,"period_week_offset":0}')
    lines.append('  holiday: {"kind":"holiday","holiday_id":"labor-day","holiday_ordinal":1}')
    lines.append('  workday: {"kind":"workday","workday_ordinal":1}')
    lines.append('  fallback_workday: {"kind":"fallback_workday","baseline_date":"2026-04-03"}')
    lines.append("  align_quality is 'exact' or 'fallback'; bucket_start_a/b show paired dates")
    lines.append("")
    lines.append("Example: mv.AlignmentPolicy(kind='dow_aligned',")
    lines.append("                            calendar=mv.CalendarRef('cn_holidays'),")
    lines.append("                            period='month')")
    return "\n".join(lines)


def _format_calendar_schema() -> str:
    lines = ["project-local calendar JSON schema:", ""]
    lines.append("Location:")
    lines.append("  .marivo/calendar/<name>.json")
    lines.append("  The directory is created when an analysis session is created or attached.")
    lines.append("")
    lines.append("Top-level object:")
    lines.append('  "name": string matching the file stem')
    lines.append('  "holidays": list[CalendarEntry]')
    lines.append('  "adjusted_workdays": optional list[CalendarEntry], defaults to []')
    lines.append("  Calendar files define dates only; extra top-level fields are rejected.")
    lines.append("")
    lines.append("CalendarEntry:")
    lines.append('  "date": ISO date string, YYYY-MM-DD')
    lines.append('  "holiday_id": optional string used to match same holiday across years')
    lines.append("  Extra fields are rejected; use holiday_id rather than name/label.")
    lines.append("")
    lines.append("Example:")
    lines.append("{")
    lines.append('  "name": "cn_holidays",')
    lines.append('  "holidays": [')
    lines.append('    {"date": "2026-05-01", "holiday_id": "labor-day"}')
    lines.append("  ],")
    lines.append('  "adjusted_workdays": [')
    lines.append('    {"date": "2026-05-02"}')
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines)


_MATRIX_FORMATTERS: dict[str, Callable[[], str]] = {
    "discover": _format_discover_matrix,
    "select": _format_select_matrix,
    "transform": _format_transform_matrix,
    "alignment": _format_alignment_matrix,
    "calendar": _format_calendar_schema,
}


def _resolve(symbol: str) -> object | None:
    import marivo.analysis as mv
    import marivo.analysis.errors as errors_mod

    if hasattr(mv, symbol):
        return cast("object", getattr(mv, symbol))
    if symbol == "observe":
        from marivo.analysis.intents.observe import observe

        return observe
    if symbol == "compare":
        from marivo.analysis.intents.compare import compare

        return compare
    if symbol == "decompose":
        from marivo.analysis.intents.decompose import decompose

        return decompose
    if symbol == "correlate":
        from marivo.analysis.intents.correlate import correlate

        return correlate
    if symbol == "forecast":
        from marivo.analysis.intents.forecast import forecast

        return forecast
    if symbol == "assess_quality":
        from marivo.analysis.intents.assess_quality import assess_quality

        return assess_quality
    if symbol == "hypothesis_test":
        from marivo.analysis.intents.test import hypothesis_test

        return hypothesis_test
    if hasattr(errors_mod, symbol):
        return cast("object", getattr(errors_mod, symbol))
    return None


def help_text(symbol: str | None = None) -> str:
    """Return help text as a string instead of printing it."""

    if symbol is None:
        return _list_top_level()

    if symbol in _MATRIX_FORMATTERS:
        return _MATRIX_FORMATTERS[symbol]()

    obj = _resolve(symbol)
    if obj is None:
        return f"Unknown symbol {symbol!r}. Call mv.help() to list available entries."

    if inspect.isclass(obj):
        return _describe_class(symbol, obj)

    if callable(obj):
        return _describe_callable(symbol, obj)

    if isinstance(obj, ModuleType):
        return _describe_module(symbol, obj)

    return repr(obj)


def help(symbol: str | None = None) -> None:
    """Print top-level or symbol-specific help for marivo.analysis."""

    print(help_text(symbol))
