"""Intermediate representation for project-level datasources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal

__all__ = [
    "AiContextIR",
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "EntitySourceIR",
    "FileSourceIR",
    "TableSourceIR",
    "source_name",
    "source_to_dict",
]


@dataclass(frozen=True)
class DatasourceSourceLocation:
    """Absolute source location for datasource error reporting."""

    file: str
    line: int


@dataclass(frozen=True)
class AiContextIR:
    """Immutable AI-facing context stored on semantic and datasource objects."""

    business_definition: str | None = None
    guardrails: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    instructions: str | None = None
    owner_notes: str | None = None


DatasourceAiContextIR = AiContextIR


@dataclass(frozen=True)
class DatasourceIR:
    """Project-level datasource configuration."""

    semantic_id: str
    name: str
    backend_type: str
    fields: dict[str, Any]
    env_refs: dict[str, str]
    description: str | None
    ai_context: AiContextIR
    python_symbol: str
    location: DatasourceSourceLocation


# ---------------------------------------------------------------------------
# Physical source descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableSourceIR:
    """Physical table source for a dataset."""

    table: str
    database: str | tuple[str, ...] | None = None
    kind: Literal["table"] = "table"


@dataclass(frozen=True)
class FileSourceIR:
    """Physical file source for a dataset."""

    path: str
    format: Literal["parquet", "csv", "json"]
    options: dict[str, Any] = field(default_factory=dict)
    kind: Literal["file"] = "file"


EntitySourceIR = TableSourceIR | FileSourceIR

_GLOB_CHARS = re.compile(r"[*?\\[]")
_SOURCE_NAME_CHARS = re.compile(r"[^0-9A-Za-z_]+")


def _sanitize_source_name(value: str) -> str:
    name = _SOURCE_NAME_CHARS.sub("_", value).strip("_").lower()
    return name or "file_source"


def source_name(source: EntitySourceIR) -> str:
    if isinstance(source, TableSourceIR):
        return source.table

    normalized_path = source.path.replace("\\", "/").rstrip("/")
    path = PurePosixPath(normalized_path)
    raw_name = path.name
    raw_name = path.parent.name if _GLOB_CHARS.search(raw_name) else PurePosixPath(raw_name).stem
    return _sanitize_source_name(raw_name)


def source_to_dict(source: EntitySourceIR) -> dict[str, object]:
    if isinstance(source, TableSourceIR):
        database: str | list[str] | None = (
            list(source.database) if isinstance(source.database, tuple) else source.database
        )
        return {"kind": "table", "table": source.table, "database": database}
    return {
        "kind": "file",
        "path": source.path,
        "format": source.format,
        "options": dict(source.options),
    }
