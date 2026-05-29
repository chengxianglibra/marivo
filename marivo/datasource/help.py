"""Agent-facing introspection for marivo.datasource."""

from __future__ import annotations

import inspect
from types import ModuleType
from typing import Any, Literal

_TOP_LEVEL_ENTRIES: dict[str, str] = {
    "datasource": "decorator - declare one project-level datasource",
    "load_datasources": "function - load datasource declarations from .marivo/datasource",
    "AiContext": "TypedDict - structured AI-facing context for datasource objects",
    "AiContextIR": "frozen dataclass - immutable AI context stored in DatasourceIR",
    "DatasourceIR": "frozen dataclass - project-level datasource configuration IR",
    "DatasourceSourceLocation": "frozen dataclass - absolute source location for error reporting",
    "DatasourceAiContextIR": "alias - same as AiContextIR for datasource context",
    "errors": "module - DatasourceConfigError hierarchy",
    "typing": "module - AiContext TypedDict",
    "help": "function - this introspection entry point",
}

_VALIDATIONS: list[dict[str, str | list[str]]] = [
    {
        "id": "datasource_name_format",
        "applies_to": ["datasource"],
        "title": "Datasource names must match [A-Za-z0-9_-]+",
        "hint": "Use letters, digits, underscores, and hyphens only.",
    },
    {
        "id": "datasource_name_no_dot",
        "applies_to": ["datasource"],
        "title": "Datasource names must not be model-qualified",
        "hint": "Use a global name, not 'model.name' format.",
    },
    {
        "id": "datasource_backend_type_required",
        "applies_to": ["datasource"],
        "title": "backend_type is required and must be a non-empty string",
        "hint": "Pass backend_type='trino', 'mysql', 'postgres', 'clickhouse', or 'duckdb'.",
    },
    {
        "id": "datasource_fields_jsonable",
        "applies_to": ["datasource"],
        "title": "Datasource fields must be JSON values",
        "hint": "Use str, int, float, bool, None, lists, or dicts with string keys.",
    },
    {
        "id": "datasource_sensitive_env_only",
        "applies_to": ["datasource"],
        "title": "Sensitive fields (password, token, etc.) must use *_env references",
        "hint": "Use password_env='MY_DB_PASSWORD' instead of password='...'.",
    },
    {
        "id": "datasource_ai_context_schema",
        "applies_to": ["datasource"],
        "title": "ai_context must use the supported schema",
        "hint": "Use business_definition, guardrails, synonyms, examples, instructions, owner_notes.",
    },
    {
        "id": "datasource_fdn_format",
        "applies_to": ["datasource"],
        "title": "Table names must use fully-distinguished format for the backend",
        "hint": "trino: catalog.schema.table; mysql/postgres/clickhouse: database.table",
    },
]


def _list_top_level() -> str:
    lines = ["marivo.datasource - top-level entries:", ""]
    for name, summary in _TOP_LEVEL_ENTRIES.items():
        prefix = "md." if name in {"datasource", "load_datasources", "help"} else ""
        lines.append(f"  {prefix}{name:<28} {summary}")
    lines.append("")
    lines.append('Call md.help("<name>") to inspect any of these.')
    lines.append('Call md.help("<name>", format="json") for agent-readable data.')
    return "\n".join(lines)


def _format_validations_text() -> str:
    lines = ["marivo.datasource validations:", ""]
    for v in _VALIDATIONS:
        applies = ", ".join(v["applies_to"])
        lines.append(f"  {v['id']:<34} [{applies}] {v['title']}")
        lines.append(f"    hint: {v['hint']}")
    lines.append("")
    lines.append('Call md.help("validations", format="json") for the full catalog.')
    return "\n".join(lines)


def _describe_callable(name: str, obj: Any) -> str:
    try:
        sig = f"{name}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        sig = name
    doc = inspect.getdoc(obj) or "(no docstring)"
    return f"{sig}\n\n{doc}"


def _describe_class(name: str, obj: type) -> str:
    doc = inspect.getdoc(obj) or "(no docstring)"
    return f"class {name}\n\n{doc}"


def _describe_module(name: str, obj: ModuleType) -> str:
    doc = inspect.getdoc(obj) or "(no docstring)"
    return f"module {name}\n\n{doc}"


def _resolve(symbol: str) -> Any | None:
    import marivo.datasource as md
    from marivo.datasource import errors as errors_mod
    from marivo.datasource import typing as typing_mod

    if hasattr(md, symbol):
        return getattr(md, symbol)
    if hasattr(errors_mod, symbol):
        return getattr(errors_mod, symbol)
    if hasattr(typing_mod, symbol):
        return getattr(typing_mod, symbol)
    return None


def _help_text(symbol: str | None = None) -> str:
    """Return help text as a string instead of printing it."""

    if symbol is None or symbol == "":
        return _list_top_level()
    if symbol == "validations":
        return _format_validations_text()

    obj = _resolve(symbol)
    if obj is None:
        return f"unknown symbol: {symbol!r}\nRun md.help() to see the top-level entry list."

    if inspect.isclass(obj):
        return _describe_class(symbol, obj)
    elif inspect.ismodule(obj):
        return _describe_module(symbol, obj)
    elif callable(obj):
        return _describe_callable(symbol, obj)
    else:
        return repr(obj)


def _signature(name: str, obj: Any) -> str:
    try:
        return f"{name}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        return name


def _validations_json(symbol: str | None = None) -> list[dict[str, str | list[str]]]:
    if symbol is None:
        return list(_VALIDATIONS)
    return [v for v in _VALIDATIONS if symbol in v.get("applies_to", [])]


def _object_json(symbol: str, obj: Any) -> dict[str, object]:
    data: dict[str, object] = {
        "symbol": symbol,
        "validations": _validations_json(symbol),
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
    return data


def _help_json(symbol: str | None = None) -> dict[str, object]:
    surface = "marivo.datasource"

    if symbol is None or symbol == "":
        entries = [
            {"name": name, "summary": summary} for name, summary in _TOP_LEVEL_ENTRIES.items()
        ]
        return {
            "schema_version": "1",
            "surface": surface,
            "entries": entries,
            "validations": _validations_json(),
        }

    if symbol == "validations":
        return {
            "schema_version": "1",
            "surface": surface,
            "validations": _validations_json(),
        }

    obj = _resolve(symbol)
    if obj is None:
        return {
            "schema_version": "1",
            "surface": surface,
            "error": f"unknown symbol: {symbol!r}",
        }

    data = _object_json(symbol, obj)
    data["schema_version"] = "1"
    data["surface"] = surface
    return data


def help(
    symbol: str | None = None,
    *,
    format: Literal["text", "json"] = "text",
) -> dict[str, object] | None:
    """Print or return agent-facing help for the datasource surface.

    Without arguments, lists top-level entries. With a symbol name, prints
    its signature, docstring, and validations. With ``format="json"``,
    returns a structured dict and does not print.
    """
    if format == "json":
        return _help_json(symbol)
    if format != "text":
        raise ValueError("format must be 'text' or 'json'")
    print(_help_text(symbol))
    return None
