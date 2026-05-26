"""ms.help - agent-facing introspection of the semantic_py surface."""

from __future__ import annotations

import inspect
from typing import Any

_TOP_LEVEL_ENTRIES: dict[str, str] = {
    "model": "context manager - opens a model namespace for decorator registration",
    "datasource": "removed - declare project datasources in .marivo/datasource/*.py",
    "dataset": "decorator - declare a dataset on top of a datasource",
    "field": "decorator - declare a non-aggregated field on a dataset",
    "time_field": "decorator - declare a time-aware field used as the calendar axis",
    "metric": "decorator - declare an aggregated metric",
    "relationship": "top-level call - declare a relationship between datasets",
    "ratio": "builder - derived metric helper (a/b)",
    "ref": "builder - refer to another metric by qualified name",
    "sum": "builder - sum aggregation marker",
    "weighted_average": "builder - weighted-average aggregation marker",
    "component": "builder - refer to a decomposition component in derived metric body",
    "help": "function - this introspection entry point",
    "find_project": "function - discover a semantic project by walking up from a directory",
    "SemanticProject": "class - primary reader for a loaded semantic project",
    "typing": "module - IbisBackend Protocol, ComponentExpr Protocol, AiContext TypedDict",
    "errors": "module - SemanticError hierarchy and ErrorKind enum",
}


def _list_top_level() -> str:
    lines = ["marivo.semantic_py - top-level entries:", ""]
    for name, summary in _TOP_LEVEL_ENTRIES.items():
        lines.append(f"  ms.{name:<18}  {summary}")
    lines.append("")
    lines.append('Call ms.help("<name>") to inspect any of these.')
    return "\n".join(lines)


def _describe_callable(name: str, obj: Any) -> str:
    sig: str
    try:
        sig = f"{name}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        sig = name
    doc = inspect.getdoc(obj) or "(no docstring)"
    return f"{sig}\n\n{doc}"


def _describe_class(name: str, obj: type) -> str:
    doc = inspect.getdoc(obj) or "(no docstring)"
    return f"class {name}\n\n{doc}"


def _resolve(symbol: str) -> Any | None:
    import marivo.semantic_py as ms
    from marivo.semantic_py import errors as errors_mod
    from marivo.semantic_py import typing as typing_mod

    if hasattr(ms, symbol):
        return getattr(ms, symbol)
    if hasattr(errors_mod, symbol):
        return getattr(errors_mod, symbol)
    if hasattr(typing_mod, symbol):
        return getattr(typing_mod, symbol)
    return None


def help(symbol: str | None = None) -> None:  # noqa: A001, RUF100
    """Print agent-facing help for the semantic_py surface.

    Without arguments, lists top-level entries. With a symbol name (decorator,
    builder, function, or exception class) prints its signature and docstring.
    """
    if symbol is None or symbol == "":
        print(_list_top_level())
        return

    obj = _resolve(symbol)
    if obj is None:
        print(f"unknown symbol: {symbol!r}\nRun ms.help() to see the top-level entry list.")
        return

    if inspect.isclass(obj):
        print(_describe_class(symbol, obj))
    elif callable(obj) or inspect.ismodule(obj):
        print(_describe_callable(symbol, obj))
    else:
        print(repr(obj))
