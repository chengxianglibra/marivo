"""Build a bounded, safe Catalog snapshot in a fresh interpreter."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

from marivo.datasource.ir import TableSourceIR
from marivo.semantic import load
from marivo.semantic.catalog import SemanticObject
from marivo.semantic.catalog_entries import (
    DatasourceDefinition,
    DomainDefinition,
    EntityDefinition,
    FieldDefinition,
    MetricDefinition,
    RelationshipDefinition,
    _json_value,
)
from marivo.semantic.errors import ErrorKind, SemanticLoadFailed
from marivo.server.wire import JsonObject, JsonValue

DEFINITION_FIELDS: dict[type[object], str] = {
    DatasourceDefinition: "context backend_type wall_clock_timezone entities",
    DomainDefinition: "context owner default members",
    EntityDefinition: "context datasource primary_key versioning default_time fields dependencies",
    FieldDefinition: "context entity expression unit additivity parse sample_interval dependencies dependency_context facts",
    MetricDefinition: "context roots formula explanation reductions conditions time_bindings unit null_rules dependencies dependency_context facts",
    RelationshipDefinition: "context from_entity to_entity keys cardinality dependencies",
}
SUMMARY_FIELDS = ("id", "kind", "path", "name", "domain", "business_definition")


def _safe_identifier(value: str) -> bool:
    return re.fullmatch(r"[\w .-]+", value) is not None


def _detail(entry: SemanticObject, root: Path) -> JsonObject:
    definition = entry.definition
    public = {
        name: _json_value(getattr(definition, name))
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
    cursor = None
    first = True
    while True:
        page = catalog.search(limit=100, cursor=cursor)
        for entry in page.items:
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
            if not first:
                write(",")
            first = False
            for part in encoder.iterencode(
                {"detail": detail, "related": related, "outside_scope": outside}
            ):
                write(part)
        cursor = page.next_cursor
        if cursor is None:
            break
    write("]}")


class _CatalogTooLargeError(Exception):
    pass


if __name__ == "__main__":
    # Separate the worker protocol from stdout even for native writes by model code.
    with os.fdopen(os.dup(1), "wb") as protocol, open(os.devnull, "wb") as discard:
        os.dup2(discard.fileno(), 1)
        try:
            # A fresh interpreter still trusts timestamp/size-valid .pyc files.
            # Use an empty cache namespace for model and helper imports without
            # deleting or rewriting the project's existing bytecode caches.
            with TemporaryDirectory(prefix="marivo-catalog-") as cache:
                sys.pycache_prefix = cache
                sys.dont_write_bytecode = True
                _catalog(Path(sys.argv[1]), protocol, int(sys.argv[2]))
        except _CatalogTooLargeError:
            sys.exit(3)
