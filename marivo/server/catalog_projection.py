"""Build a bounded, safe Catalog snapshot in a fresh interpreter."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from marivo.datasource.spi import TableSourceIR
from marivo.semantic import (
    DatasourceDefinition,
    DomainDefinition,
    EntityDefinition,
    FieldDefinition,
    MetricDefinition,
    RelationshipDefinition,
    SemanticObject,
    load,
)
from marivo.semantic.errors import ErrorKind, SemanticLoadFailed
from marivo.semantic.spi import iter_catalog_entries, serialize_catalog_json_value

if TYPE_CHECKING:
    from marivo.server.wire import JsonObject, JsonValue

DEFINITION_FIELDS: dict[type[object], str] = {
    DatasourceDefinition: "context backend_type wall_clock_timezone entities",
    DomainDefinition: "context owner default members",
    EntityDefinition: "context datasource primary_key versioning default_time fields dependencies",
    FieldDefinition: "context entity expression unit additivity parse sample_interval dependencies dependency_context facts",
    MetricDefinition: "context roots formula explanation reductions conditions time_bindings unit null_rules dependencies dependency_context facts",
    RelationshipDefinition: "context from_entity to_entity keys cardinality dependencies",
}

# Literal field allowlist for the authenticated datasource-properties endpoint.
_DATASOURCE_FIELDS: dict[str, frozenset[str]] = {
    "duckdb": frozenset({"path", "read_only"}),
    "trino": frozenset(
        {"host", "port", "catalog", "schema", "source", "http_scheme", "client_tags"}
    ),
    "mysql": frozenset({"host", "port", "database", "autocommit"}),
    "postgres": frozenset({"host", "port", "database", "schema", "autocommit"}),
    "clickhouse": frozenset({"host", "port", "database", "secure"}),
}
_DATASOURCE_TEXT = frozenset(
    {"host", "catalog", "schema", "database", "source", "path", "http_scheme"}
)
_DATASOURCE_VALUE = re.compile(r"[\w./:@+ -]{1,512}\Z")
_SENSITIVE_MARKER = re.compile(r"password|passwd|secret|token|credential|api[_-]?key", re.I)


def _datasource_properties(definition: DatasourceDefinition) -> JsonObject:
    properties: JsonObject = {}
    for key, frozen in definition.fields:
        if key not in _DATASOURCE_FIELDS.get(definition.backend_type, frozenset()):
            continue
        # Validate the frozen value before materializing it: an approved key
        # may still contain an arbitrarily large or deeply nested value.
        value = frozen
        if (
            (
                key in _DATASOURCE_TEXT
                and isinstance(value, str)
                and _DATASOURCE_VALUE.fullmatch(value)
                and not _SENSITIVE_MARKER.search(value)
                and "://" not in value
                and "@" not in value
            )
            or (key == "port" and type(value) is int and 1 <= value <= 65535)
            or (key in {"read_only", "secure", "autocommit"} and type(value) is bool)
        ):
            properties[key] = value
        elif key == "client_tags" and isinstance(value, tuple) and len(value) <= 50:
            tags = [tag for tag in value if isinstance(tag, str)]
            if len(tags) == len(value) and all(
                len(tag) <= 128
                and re.fullmatch(r"[\w. -]+(?:=[\w. -]+)?", tag)
                and not _SENSITIVE_MARKER.search(tag)
                for tag in tags
            ):
                properties[key] = list(tags)
    return {
        "backend_type": definition.backend_type,
        "wall_clock_timezone": definition.wall_clock_timezone,
        "properties": properties,
        "env_refs": dict(definition.env_refs),
        "has_omitted_fields": len(properties) < len(definition.fields),
    }


def _safe_identifier(value: str) -> bool:
    return re.fullmatch(r"[\w .-]+", value) is not None


def _detail(entry: SemanticObject, root: Path) -> JsonObject:
    definition = entry.definition
    public = {
        name: serialize_catalog_json_value(getattr(definition, name))
        for name in DEFINITION_FIELDS[type(definition)].split()
    }
    if isinstance(definition, EntityDefinition):
        source: JsonObject = {"kind": definition.source.kind}
        if isinstance(definition.source, TableSourceIR):
            table = definition.source.table
            database = definition.source.database
            source["table"] = table if _safe_identifier(table) else None
            if isinstance(database, tuple):
                source["database"] = (
                    list(database) if all(map(_safe_identifier, database)) else None
                )
            else:
                source["database"] = (
                    database if database is not None and _safe_identifier(database) else None
                )
        public["source"] = source
    origin = entry.provenance
    path = Path(origin.source_file)
    # Only files inside the bound project can contribute a relative location.
    provenance: JsonObject = {
        "source_file": path.relative_to(root).as_posix() if path.is_relative_to(root) else None,
        "source_line": origin.source_line,
        "python_symbol": origin.python_symbol,
    }
    return {
        "id": entry.id,
        "kind": entry.kind,
        "path": entry.path,
        "name": entry.name,
        "domain": entry.domain,
        "business_definition": definition.context.business_definition,
        "definition": public,
        "provenance": provenance,
    }


def _catalog(root: Path, output: BinaryIO, maximum: int) -> None:
    try:
        catalog = load(workspace_dir=root)
    except SemanticLoadFailed as error:
        known = {kind.value for kind in ErrorKind}
        diagnostics = sorted({item.kind for item in error.errors if item.kind in known})
        output.write(json.dumps({"diagnostics": diagnostics}, separators=(",", ":")).encode())
        return
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    used = 0

    def write(part: str) -> None:
        nonlocal used
        chunk = part.encode("utf-8")
        used += len(chunk)
        if used > maximum:
            raise _CatalogTooLargeError
        output.write(chunk)

    write('{"entries":[')
    for index, entry in enumerate(iter_catalog_entries(catalog)):
        detail = _detail(entry, root)
        related: JsonObject = {}
        outside: list[JsonValue] = []
        for relation in ("dependencies", "dependents"):
            ids: list[JsonValue] = []
            after = None
            while True:
                links = entry.related(relation, limit=100, cursor=after)
                ids.extend(item.id for item in links.items)
                after = links.next_cursor
                if after is None:
                    if relation == "dependencies":
                        outside.extend(links.outside_scope)
                    break
            related[relation] = ids
        if index:
            write(",")
        projected: JsonObject = {"detail": detail, "related": related, "outside_scope": outside}
        if isinstance(entry.definition, DatasourceDefinition):
            projected["datasource_properties"] = _datasource_properties(entry.definition)
        for part in encoder.iterencode(projected):
            write(part)
    write("]}")


class _CatalogTooLargeError(Exception):
    pass


if __name__ == "__main__":
    # Separate the worker protocol from stdout even for native writes by model code.
    with os.fdopen(os.dup(1), "wb") as protocol, open(os.devnull, "wb") as discard:
        os.dup2(discard.fileno(), 1)
        try:
            _catalog(Path(sys.argv[1]), protocol, int(sys.argv[2]))
        except _CatalogTooLargeError:
            sys.exit(3)
