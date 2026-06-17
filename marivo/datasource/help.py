"""md.help - agent-facing introspection of the datasource surface."""

from __future__ import annotations

import builtins
import json
from functools import lru_cache
from typing import Literal, cast

from marivo.datasource.constraints import iter_constraints
from marivo.introspection.render import format_family_block
from marivo.introspection.schema import Descriptor
from marivo.introspection.surface import Surface, render, top_level_families

_SUMMARIES: dict[str, str] = {
    "AiContextIR": "immutable AI-facing context stored on datasource objects",
    "ColumnInspection": "profiled column facts from a bounded datasource scan",
    "ColumnMetadata": "column-level metadata including type and nullability",
    "ColumnProfile": "bounded-sample column profile with type, nulls, and cardinality",
    "DatasourceAiContextIR": "datasource alias for AiContextIR",
    "DatasourceCatalog": "read-only catalog over configured project datasources, obtained via md.load()",
    "DatasourceConnectionService": "internal service for scoped datasource backend connections",
    "DatasourceDescription": "literal fields and env refs for one datasource",
    "DatasourceIR": "project-level datasource configuration IR",
    "DatasourceRef": "global datasource reference used by semantic declarations",
    "DatasourceSourceLocation": "absolute source location for datasource error reporting",
    "DatasourceSummary": "summary row for one configured project datasource",
    "DatasourceTestResult": "result of a datasource connectivity round-trip",
    "JoinKeyProbe": "join compatibility result for one key column pair",
    "JoinSide": "one side of a join-key probe identifying source and key columns",
    "MetadataWarning": "warning emitted during table metadata inspection",
    "PartitionMetadata": "partition metadata for a table column",
    "PreviewResult": "bounded preview result with rows, columns, and types",
    "PreviewSamplePolicy": "sampling policy used to produce a preview",
    "PreviewWarning": "warning emitted during datasource preview",
    "ScanReport": "report from a scoped datasource scan including column profiles",
    "ScanScope": "scoped datasource scan input with source and sample bounds",
    "TableMetadata": "schema, comments, nullability, and partition metadata for a table",
    "clickhouse": "declare a ClickHouse datasource",
    "connect": "open a live ibis backend for a datasource; caller disconnects",
    "describe": "show literal fields and env refs for one datasource",
    "duckdb": "declare a DuckDB datasource",
    "help": "this introspection entry point",
    "help_text": "return datasource help text without printing",
    "inspect_columns": "profile selected columns from a datasource source with bounded scan",
    "inspect_source": "table metadata for a semantic entity source (table or file)",
    "inspect_table": "schema, comments, nullability, and partition metadata for a table",
    "list": "list configured project datasources as DatasourceSummary rows",
    "load": "load the project datasource catalog and return a DatasourceCatalog",
    "mysql": "declare a MySQL datasource",
    "parquet": "parquet file source for datasource inspection",
    "postgres": "declare a Postgres datasource",
    "preview": "bounded, filtered preview of one datasource table",
    "probe_join_keys": "probe join compatibility between two sources on specified key columns",
    "ref": "reference a global project datasource by short name",
    "register": "create or replace a project datasource file from a DatasourceSpec",
    "remove": "delete the named project datasource file",
    "table": "table source for datasource inspection",
    "test": "round-trip the backend and persist validated env secrets",
    "trino": "declare a Trino datasource",
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
        topics={
            "constraints": _constraint_topic(),
        },
        type_aliases=set(),
        family_suffixes=(),
        hidden_names=frozenset(),
    )


def _format_top_level_text() -> str:
    data = cast("dict[str, object]", render(_surface(), None, "json"))
    entries = cast("list[dict[str, str]]", data["entries"])
    lines = ["marivo.datasource - top-level entries:", ""]
    for entry in entries:
        lines.append(f"  md.{entry['name']:<24} [{entry['kind']}]  {entry['summary']}")
    lines.extend(format_family_block(top_level_families(_surface()), help_call="md.help"))
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
