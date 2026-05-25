"""Small stdout-based introspection helper for marivo.analysis_py."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from types import ModuleType
from typing import cast

_TOP_LEVEL_ENTRIES = {
    "observe": "build a MetricFrame from a metric and window",
    "compare": "compare two MetricFrames into a DeltaFrame",
    "decompose": "decompose a DeltaFrame into an AttributionFrame",
    "discover": "discover candidate follow-ups from analysis artifacts",
    "correlate": "correlate compatible analysis frames",
    "session": "session lifecycle and persistence helpers",
    "help": "print top-level or symbol-specific introspection",
}


def _list_top_level() -> str:
    lines = ["marivo.analysis_py - top-level entries:"]
    for name, summary in _TOP_LEVEL_ENTRIES.items():
        lines.append(f"  mv.{name:<10} {summary}")
    lines.append("")
    lines.append('Call mv.help("<name>") for a signature, docstring, or class summary.')
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
    return "\n".join(lines)


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
