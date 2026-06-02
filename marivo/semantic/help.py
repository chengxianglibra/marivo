"""ms.help - agent-facing introspection of the semantic surface."""

from __future__ import annotations

import inspect
from types import ModuleType
from typing import Any, Literal, cast

from marivo.semantic.constraints import (
    constraints_for_symbol,
    get_constraint,
    iter_constraints,
)

_TOP_LEVEL_ENTRIES: dict[str, tuple[str, str]] = {
    "model": ("callable", "opens a model namespace for decorator registration"),
    "datasource": ("removed", "declare project datasources in .marivo/datasource/*.py"),
    "dataset": ("callable", "declare a dataset over a structured source"),
    "table": ("callable", "table source for ms.dataset(source=...)"),
    "file": ("callable", "file source for ms.dataset(source=...)"),
    "field": ("callable", "declare a non-aggregated field on a dataset"),
    "time_field": ("callable", "declare a time-aware field used as the calendar axis"),
    "metric": ("callable", "declare an aggregated metric"),
    "relationship": ("callable", "declare a relationship between datasets"),
    "ratio": ("callable", "derived metric helper (a/b)"),
    "ref": ("callable", "refer to another metric by qualified name"),
    "sum": ("callable", "sum aggregation marker"),
    "weighted_average": ("callable", "weighted-average aggregation marker"),
    "decomposition": ("topic", "metric decomposition builders and aggregation boundary"),
    "component": ("callable", "refer to a decomposition component in derived metric body"),
    "help": ("callable", "this introspection entry point"),
    "constraints": ("topic", "authoring and validation constraints"),
    "find_project": ("callable", "discover a semantic project by walking up from a directory"),
    "SemanticProject": ("class", "primary reader for a loaded semantic project"),
    "typing": ("module", "IbisBackend Protocol, ComponentExpr Protocol, AiContext TypedDict"),
    "errors": ("module", "SemanticError hierarchy and ErrorKind enum"),
}


def _list_top_level() -> str:
    lines = ["marivo.semantic — top-level entries:", ""]
    for name, (kind, desc) in _TOP_LEVEL_ENTRIES.items():
        lines.append(f"  ms.{name:<18} [{kind}]  {desc}")
    lines.append("")
    lines.append('Call ms.help("<name>") for detail on any entry.')
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


def _decomposition_help_json() -> dict[str, object]:
    return {
        "schema_version": "1",
        "surface": "marivo.semantic",
        "kind": "topic",
        "topic": "decomposition",
        "summary": (
            "Metric decomposition is not SQL aggregation. Decomposition declares how "
            "metric values compose during drilldown, derived calculations, and "
            "component-aware analysis."
        ),
        "builders": [
            {
                "name": "sum",
                "call": "ms.sum()",
                "use": "Aggregate metric over its dataset row set.",
                "components": [],
            },
            {
                "name": "ratio",
                "call": "ms.ratio(numerator=..., denominator=...)",
                "use": "Derived metric expressed as numerator / denominator.",
                "components": ["numerator", "denominator"],
            },
            {
                "name": "weighted_average",
                "call": "ms.weighted_average(value=..., weight=...)",
                "use": "Derived metric whose value is explained by additive value and weight components.",
                "components": ["numerator", "weight"],
            },
        ],
        "guidance": [
            {
                "metric_shape": "additive_amount",
                "body": ".sum() or another dataset-backed reduction",
                "decomposition": "ms.sum()",
            },
            {
                "metric_shape": "count",
                "body": ".count() in the metric body",
                "decomposition": "ms.sum()",
            },
            {
                "metric_shape": "mean_or_average",
                "body": "derived body using ms.component('numerator') / ms.component('denominator')",
                "decomposition": "ms.ratio(...)",
            },
            {
                "metric_shape": "weighted_average",
                "body": "derived body using value and weight components",
                "decomposition": "ms.weighted_average(...)",
            },
        ],
        "anti_patterns": [
            "Do not call ms.count(); count metrics use .count() in the metric body and ms.sum() decomposition.",
            "Do not call ms.mean(); mean metrics should be modeled as ratio or weighted_average components.",
            "Do not infer decomposition builders from common SQL aggregate names.",
        ],
        "related_help": [
            "ms.help('metric', format='json')",
            "ms.help('component', format='json')",
            "ms.help('constraints', format='json')",
        ],
    }


def _format_decomposition_text() -> str:
    data = _decomposition_help_json()
    lines = [
        "marivo.semantic decomposition",
        "",
        str(data["summary"]),
        "",
        "Supported builders:",
    ]
    builders = cast("list[dict[str, object]]", data["builders"])
    guidance = cast("list[dict[str, object]]", data["guidance"])
    anti_patterns = cast("list[str]", data["anti_patterns"])
    for builder in builders:
        lines.append(f"  - {builder['call']}: {builder['use']}")
    lines.extend(
        [
            "",
            "Guidance:",
        ]
    )
    for guidance_item in guidance:
        lines.append(
            f"  - {guidance_item['metric_shape']}: body {guidance_item['body']}; decomposition {guidance_item['decomposition']}"
        )
    lines.extend(["", "Anti-patterns:"])
    for anti_pattern in anti_patterns:
        lines.append(f"  - {anti_pattern}")
    lines.append("")
    lines.append('Call ms.help("decomposition", format="json") for agent-readable data.')
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


def help_text(symbol: str | None = None) -> str:
    """Return help text as a string instead of printing it."""

    if symbol is None or symbol == "":
        return _list_top_level()
    if symbol == "constraints":
        return _format_constraints_text()
    if symbol == "decomposition":
        return _format_decomposition_text()

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
                {"name": name, "summary": desc, "kind": kind}
                for name, (kind, desc) in _TOP_LEVEL_ENTRIES.items()
            ],
        }
    if symbol == "constraints":
        return {
            "schema_version": "1",
            "surface": "marivo.semantic",
            "constraints": _constraints_json(),
        }
    if symbol == "decomposition":
        return _decomposition_help_json()

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
    print(help_text(symbol))
    return None
