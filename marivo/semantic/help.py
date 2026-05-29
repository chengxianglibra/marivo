"""ms.help - agent-facing introspection of the semantic surface."""

from __future__ import annotations

import inspect
from types import ModuleType
from typing import Any, Literal

from marivo.semantic.constraints import (
    constraints_for_symbol,
    get_constraint,
    iter_constraints,
)

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
    "constraints": "catalog - authoring and validation constraints",
    "find_project": "function - discover a semantic project by walking up from a directory",
    "SemanticProject": "class - primary reader for a loaded semantic project",
    "typing": "module - IbisBackend Protocol, ComponentExpr Protocol, AiContext TypedDict",
    "errors": "module - SemanticError hierarchy and ErrorKind enum",
}


def _list_top_level() -> str:
    lines = ["marivo.semantic - top-level entries:", ""]
    for name, summary in _TOP_LEVEL_ENTRIES.items():
        lines.append(f"  ms.{name:<18}  {summary}")
    lines.append("")
    lines.append('Call ms.help("<name>") to inspect any of these.')
    lines.append('Call ms.help("<name>", format="json") for agent-readable data.')
    return "\n".join(lines)


def _describe_callable(name: str, obj: Any) -> str:
    sig: str
    try:
        sig = f"{name}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        sig = name
    doc = inspect.getdoc(obj) or "(no docstring)"
    constraint_text = _format_symbol_constraints(name)
    if constraint_text:
        return f"{sig}\n\n{doc}\n\n{constraint_text}"
    return f"{sig}\n\n{doc}"


def _describe_class(name: str, obj: type) -> str:
    doc = inspect.getdoc(obj) or "(no docstring)"
    return f"class {name}\n\n{doc}"


def _format_symbol_constraints(symbol: str) -> str:
    constraints = constraints_for_symbol(symbol)
    if not constraints:
        return ""
    lines = ["Constraints:"]
    for constraint in constraints:
        lines.append(f"  - {constraint.id.value}: {constraint.title}")
        lines.append(f"    hint: {constraint.hint}")
    return "\n".join(lines)


def _format_constraints_text() -> str:
    lines = ["marivo.semantic constraints:", ""]
    for constraint in iter_constraints():
        lines.append(f"  {constraint.id.value:<34} [{constraint.error_kind}] {constraint.title}")
    lines.append("")
    lines.append('Call ms.help("constraints", format="json") for the full catalog.')
    return "\n".join(lines)


def _resolve(symbol: str) -> Any | None:
    import marivo.semantic as ms
    from marivo.semantic import errors as errors_mod
    from marivo.semantic import typing as typing_mod

    if hasattr(ms, symbol):
        return getattr(ms, symbol)
    if hasattr(errors_mod, symbol):
        return getattr(errors_mod, symbol)
    if hasattr(typing_mod, symbol):
        return getattr(typing_mod, symbol)
    return None


def _help_text(symbol: str | None = None) -> str:
    """Return help text as a string instead of printing it."""

    if symbol is None or symbol == "":
        return _list_top_level()
    if symbol == "constraints":
        return _format_constraints_text()

    obj = _resolve(symbol)
    if obj is None:
        return f"unknown symbol: {symbol!r}\nRun ms.help() to see the top-level entry list."

    if inspect.isclass(obj):
        return _describe_class(symbol, obj)
    elif callable(obj) or inspect.ismodule(obj):
        return _describe_callable(symbol, obj)
    else:
        return repr(obj)


def _signature(name: str, obj: Any) -> str:
    try:
        return f"{name}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        return name


def _constraints_json(symbol: str | None = None) -> list[dict[str, object]]:
    if symbol is None:
        return [constraint.to_dict() for constraint in iter_constraints()]
    return [constraint.to_dict() for constraint in constraints_for_symbol(symbol)]


def _object_json(symbol: str, obj: Any) -> dict[str, object]:
    data: dict[str, object] = {
        "symbol": symbol,
        "constraints": _constraints_json(symbol),
    }
    if inspect.isclass(obj):
        data["kind"] = "class"
        data["signature"] = f"class {symbol}"
        data["doc"] = inspect.getdoc(obj) or ""
    elif inspect.ismodule(obj) or isinstance(obj, ModuleType):
        data["kind"] = "module"
        data["signature"] = f"module {symbol}"
        data["doc"] = inspect.getdoc(obj) or ""
    elif callable(obj):
        data["kind"] = "callable"
        data["signature"] = _signature(symbol, obj)
        data["doc"] = inspect.getdoc(obj) or ""
    else:
        data["kind"] = "object"
        data["repr"] = repr(obj)
    examples = [
        constraint.example
        for constraint in constraints_for_symbol(symbol)
        if constraint.example is not None
    ]
    if examples:
        data["examples"] = sorted(set(examples))
    return data


def _help_json(symbol: str | None = None) -> dict[str, object]:
    if symbol is None or symbol == "":
        return {
            "schema_version": "1",
            "surface": "marivo.semantic",
            "entries": [
                {"name": name, "summary": summary} for name, summary in _TOP_LEVEL_ENTRIES.items()
            ],
            "authoring_constraints": _constraints_json("dataset")
            + _constraints_json("field")
            + _constraints_json("time_field")
            + _constraints_json("metric"),
        }
    if symbol == "constraints":
        return {
            "schema_version": "1",
            "surface": "marivo.semantic",
            "constraints": _constraints_json(),
        }

    obj = _resolve(symbol)
    if obj is None:
        constraint = get_constraint(symbol)
        if constraint is not None:
            return {
                "schema_version": "1",
                "surface": "marivo.semantic",
                "constraint": constraint.to_dict(),
            }
        return {
            "schema_version": "1",
            "surface": "marivo.semantic",
            "error": f"unknown symbol: {symbol!r}",
        }
    data = _object_json(symbol, obj)
    data["schema_version"] = "1"
    data["surface"] = "marivo.semantic"
    return data


def help(  # noqa: A001, RUF100
    symbol: str | None = None,
    *,
    format: Literal["text", "json"] = "text",
) -> dict[str, object] | None:
    """Print or return agent-facing help for the semantic surface.

    Without arguments, lists top-level entries. With a symbol name (decorator,
    builder, function, exception class, or ``"constraints"``) prints its
    signature, docstring, and constraints. With ``format="json"``, returns a
    structured dict and does not print.
    """
    if format == "json":
        return _help_json(symbol)
    if format != "text":
        raise ValueError("format must be 'text' or 'json'")
    print(_help_text(symbol))
    return None
