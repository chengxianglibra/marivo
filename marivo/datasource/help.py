"""md.help - agent-facing introspection of the datasource surface."""

from __future__ import annotations

import builtins
import json
from functools import lru_cache
from typing import Literal, cast

from marivo.datasource.constraints import iter_constraints
from marivo.introspection.schema import Descriptor
from marivo.introspection.surface import Surface, render

_SUMMARIES: dict[str, str] = {
    "AiContextIR": "immutable AI-facing context stored on datasource objects",
    "ColumnMetadata": "column-level metadata including type and nullability",
    "DatasourceAiContextIR": "datasource alias for AiContextIR",
    "DatasourceDescription": "literal fields and env refs for one datasource",
    "DatasourceIR": "project-level datasource configuration IR",
    "DatasourceRef": "global datasource reference used by semantic declarations",
    "DatasourceSourceLocation": "absolute source location for datasource error reporting",
    "DatasourceSpec": "validated project-level datasource configuration",
    "DatasourceSummary": "summary row for one configured project datasource",
    "DatasourceTestResult": "result of a datasource connectivity round-trip",
    "MetadataWarning": "warning emitted during table metadata inspection",
    "PartitionMetadata": "partition metadata for a table column",
    "PreviewResult": "bounded preview result with rows, columns, and types",
    "PreviewSamplePolicy": "sampling policy used to produce a preview",
    "PreviewWarning": "warning emitted during datasource preview",
    "TableMetadata": "schema, comments, nullability, and partition metadata for a table",
    "connect": "open a live ibis backend for a datasource; caller disconnects",
    "datasource": "declare one project-level datasource",
    "describe": "show literal fields and env refs for one datasource",
    "help": "this introspection entry point",
    "help_text": "return datasource help text without printing",
    "inspect_source": "table metadata for a semantic entity source (table or file)",
    "inspect_table": "schema, comments, nullability, and partition metadata for a table",
    "list": "list configured project datasources as DatasourceSummary rows",
    "load_datasources": "load project datasource declarations from .marivo/datasource files",
    "preview": "bounded, filtered preview of one datasource table",
    "ref": "reference a global project datasource by short name",
    "register": "create or replace a project datasource file from a DatasourceSpec",
    "remove": "delete the named project datasource file",
    "test": "round-trip the backend and persist validated env secrets",
}


def _constraint_topic() -> Descriptor:
    constraints = [
        {
            "id": constraint.id,
            "title": constraint.title,
        }
        for constraint in iter_constraints()
    ]
    return Descriptor(
        surface="marivo.datasource",
        kind="topic",
        symbol="constraints",
        summary="Datasource authoring and validation constraints. Drill into an id for full rule details.",
        content={"constraints": constraints},
        doc="\n".join(
            (
                "marivo.datasource constraints:",
                "",
                *(f"  {constraint['id']:<36} {constraint['title']}" for constraint in constraints),
                "",
                'Call md.help("<constraint_id>") for full rule details.',
            )
        ),
    )


def _resolve(symbol: str) -> object | None:
    import marivo.datasource as md

    if symbol in md.__all__ and hasattr(md, symbol):
        return cast("object", getattr(md, symbol))
    return None


@lru_cache(maxsize=1)
def _surface() -> Surface:
    import marivo.datasource as md

    all_names = tuple(md.__all__)
    summaries = {name: _SUMMARIES.get(name, "") for name in all_names}
    catalog = {constraint.id: constraint for constraint in iter_constraints()}
    return Surface(
        name="marivo.datasource",
        all_names=all_names,
        summaries=summaries,
        resolve=_resolve,
        catalog=catalog,
        topics={"constraints": _constraint_topic()},
    )


def _format_top_level_text() -> str:
    data = cast("dict[str, object]", render(_surface(), None, "json"))
    entries = cast("list[dict[str, str]]", data["entries"])
    lines = ["marivo.datasource - top-level entries:", ""]
    for entry in entries:
        lines.append(f"  md.{entry['name']:<24} [{entry['kind']}]  {entry['summary']}")
    lines.append("")
    lines.append('Call md.help("<name>") for detail on any entry.')
    return "\n".join(lines)


def help_text(symbol: str | None = None) -> str:
    """Return help text as a string instead of printing it."""

    normalized = None if symbol == "" else symbol
    if normalized is None:
        return _format_top_level_text()
    return cast("str", render(_surface(), normalized, "text"))


def help(  # noqa: A001, RUF100
    symbol: str | None = None,
    *,
    format: Literal["text", "json"] = "text",
    print: bool = True,
) -> dict[str, object] | str | None:
    """Print or return agent-facing help for the datasource surface.

    With ``format="text"``, prints a compact text descriptor by default and
    returns None. Pass ``print=False`` to return the text without printing.
    With ``format="json"``, prints the structured JSON descriptor by default
    and returns the dict. Pass ``print=False`` to suppress printing.
    """

    normalized = None if symbol == "" else symbol
    if format == "json":
        data = cast("dict[str, object]", render(_surface(), normalized, "json"))
        if print:
            builtins.print(json.dumps(data, indent=2, sort_keys=True))
        return data
    if format == "text":
        text = help_text(normalized)
        if print:
            builtins.print(text)
            return None
        return text
    raise ValueError("format must be 'text' or 'json'")
