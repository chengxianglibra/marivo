"""Intermediate representation dataclasses for marivo.semantic v1.1.

All IR dataclasses are frozen (value semantics).  Callable objects are
stored in a sidecar map, not in the IR itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from marivo.datasource.ir import (
    AiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
)

__all__ = [
    "AiContextIR",
    "DatasetIR",
    "DatasetProvenance",
    "DatasetRef",
    "DatasetSourceIR",
    "DatasetVersioningIR",
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "DecompositionIR",
    "FieldIR",
    "FieldRef",
    "FileSourceIR",
    "MetricAdditivity",
    "MetricIR",
    "MetricRef",
    "ModelIR",
    "ParityStatus",
    "ProvenanceIR",
    "RelationshipIR",
    "RelationshipRef",
    "SnapshotVersioningIR",
    "SourceLocation",
    "SymbolKind",
    "TableSourceIR",
    "TimeFieldRef",
    "ValidityVersioningIR",
    "VerificationMode",
    "_BaseRef",
    "source_from_dict",
    "source_label",
    "source_name",
    "source_to_dict",
]

DatasourceAiContextIR = AiContextIR


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SymbolKind(StrEnum):
    """Kind of semantic object."""

    MODEL = "model"
    DATASOURCE = "datasource"
    DATASET = "dataset"
    FIELD = "field"
    TIME_FIELD = "time_field"
    METRIC = "metric"
    RELATIONSHIP = "relationship"


class ParityStatus(StrEnum):
    """Parity verification status for metrics."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    DRIFTED = "drifted"


class VerificationMode(StrEnum):
    """Author-declared metric verification mode."""

    SQL_PARITY = "sql_parity"
    PYTHON_NATIVE = "python_native"


class MetricAdditivity(StrEnum):
    """Metric summability relative to its dataset row grain."""

    ADDITIVE = "additive"
    SEMI_ADDITIVE = "semi_additive"
    NON_ADDITIVE = "non_additive"


class DatasetProvenance(StrEnum):
    """How a dataset's physical table was produced."""

    IBIS_TABLE = "ibis_table"
    SQL_VIEW = "sql_view"


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceLocation:
    """Absolute source location for error reporting."""

    file: str
    line: int


@dataclass(frozen=True)
class SnapshotVersioningIR:
    """Daily snapshot versioning metadata for Phase 1 latest joins."""

    kind: Literal["snapshot"]
    partition_field: str
    grain: Literal["day"]
    timezone: str | None = None
    format: str | None = None


@dataclass(frozen=True)
class ValidityVersioningIR:
    """SCD2 validity interval versioning metadata for Phase 2."""

    kind: Literal["validity"]
    valid_from: str
    valid_to: str
    interval: Literal["closed_open", "closed_closed"]
    open_end: tuple[Any, ...]
    timezone: str | None = None


DatasetVersioningIR = SnapshotVersioningIR | ValidityVersioningIR


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
    format: Literal["parquet", "csv"]
    options: dict[str, Any] = field(default_factory=dict)
    kind: Literal["file"] = "file"


DatasetSourceIR = TableSourceIR | FileSourceIR

_GLOB_CHARS = re.compile(r"[*?\\[]")
_SOURCE_NAME_CHARS = re.compile(r"[^0-9A-Za-z_]+")


def _sanitize_source_name(value: str) -> str:
    name = _SOURCE_NAME_CHARS.sub("_", value).strip("_").lower()
    return name or "file_source"


def source_name(source: DatasetSourceIR) -> str:
    if isinstance(source, TableSourceIR):
        return source.table

    normalized_path = source.path.replace("\\", "/").rstrip("/")
    path = PurePosixPath(normalized_path)
    raw_name = path.name
    raw_name = path.parent.name if _GLOB_CHARS.search(raw_name) else PurePosixPath(raw_name).stem
    return _sanitize_source_name(raw_name)


def source_to_dict(source: DatasetSourceIR) -> dict[str, object]:
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


def source_from_dict(data: Mapping[str, object]) -> DatasetSourceIR:
    kind = data.get("kind")
    if kind == "table":
        raw_database = data.get("database")
        database: str | tuple[str, ...] | None
        if isinstance(raw_database, list):
            database = tuple(str(part) for part in raw_database)
        elif raw_database is None:
            database = None
        else:
            database = str(raw_database)
        return TableSourceIR(table=str(data["table"]), database=database)
    if kind == "file":
        raw_options = data.get("options", {})
        options = dict(raw_options) if isinstance(raw_options, Mapping) else {}
        format_value = str(data["format"])
        if format_value not in {"parquet", "csv"}:
            raise ValueError(f"unsupported file source format: {format_value!r}")
        return FileSourceIR(
            path=str(data["path"]),
            format=format_value,  # type: ignore[arg-type]
            options=options,
        )
    raise ValueError(f"unsupported dataset source kind: {kind!r}")


def source_label(source: DatasetSourceIR) -> str:
    if isinstance(source, TableSourceIR):
        if source.database is None:
            return source.table
        database = (
            ".".join(source.database) if isinstance(source.database, tuple) else source.database
        )
        return f"{database}.{source.table}"
    return source.path


@dataclass(frozen=True)
class ProvenanceIR:
    """Source provenance and verification mode for expression-bearing objects."""

    source_sql: str | None = None
    source_dialect: str | None = None
    source_document: str | None = None
    source_notes: str | None = None
    verification_mode: Literal["sql_parity", "python_native"] | None = None


@dataclass(frozen=True)
class ModelIR:
    """Semantic model container."""

    name: str
    description: str | None
    default: bool
    ai_context: AiContextIR
    location: SourceLocation


@dataclass(frozen=True)
class DatasetIR:
    """Dataset declaration with physical grounding."""

    semantic_id: str
    model: str
    name: str
    datasource: str
    source: DatasetSourceIR
    primary_key: tuple[str, ...]
    description: str | None
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation
    versioning: DatasetVersioningIR | None = None


@dataclass(frozen=True)
class FieldIR:
    """Field declaration (dimension or measure column)."""

    semantic_id: str
    model: str
    dataset: str
    name: str
    description: str | None
    ai_context: AiContextIR
    is_time_field: bool
    data_type: str | None
    granularity: str | None
    required_prefix: str | None
    python_symbol: str
    location: SourceLocation
    format: str | None = None
    timezone: str | None = None
    is_default: bool = False


@dataclass(frozen=True)
class DecompositionIR:
    """Decomposition semantics for a metric."""

    kind: Literal["sum", "ratio", "weighted_average"]
    components: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricIR:
    """Metric declaration with decomposition and provenance."""

    semantic_id: str
    model: str
    name: str
    datasets: tuple[str, ...]
    is_derived: bool
    decomposition: DecompositionIR
    provenance: ProvenanceIR
    description: str | None
    ai_context: AiContextIR
    body_ast_hash: str
    python_symbol: str
    location: SourceLocation
    additivity: Literal["additive", "semi_additive", "non_additive"] | None = None
    root_dataset: str | None = None
    fanout_policy: Literal["block", "aggregate_then_join"] = "block"


@dataclass(frozen=True)
class RelationshipIR:
    """Relationship between two datasets."""

    semantic_id: str
    model: str
    name: str
    from_dataset: str
    to_dataset: str
    from_fields: tuple[str, ...]
    to_fields: tuple[str, ...]
    description: str | None
    ai_context: AiContextIR
    location: SourceLocation


# ---------------------------------------------------------------------------
# Ref types
# ---------------------------------------------------------------------------


class _BaseRef:
    """Base class for decorator return refs.

    Subclasses are returned by the authoring decorators instead of
    the raw function, closing the ambiguity where a decorated metric
    could still be called directly by user code.
    """

    __slots__ = ("kind", "semantic_id")

    def __init__(self, semantic_id: str, kind: SymbolKind) -> None:
        self.semantic_id = semantic_id
        self.kind = kind

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.semantic_id!r})"


class DatasetRef(_BaseRef):
    """Ref returned by ms.dataset().  Not callable."""

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.DATASET)


class FieldRef(_BaseRef):
    """Ref returned by ms.field().  Callable in base metric bodies.

    Calling ``field_ref(parent_table)`` resolves to the sidecar callable
    stored in the loader registry and invokes it with the parent table.
    """

    _resolver: Callable[[str, Any], Any] | None

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.FIELD)
        self._resolver = None

    def __call__(self, parent_table: Any) -> Any:
        if self._resolver is None:
            raise RuntimeError(
                f"FieldRef({self.semantic_id!r}) has no resolver. "
                "FieldRefs can only be called inside a loaded semantic project."
            )
        return self._resolver(self.semantic_id, parent_table)


class TimeFieldRef(_BaseRef):
    """Ref returned by ms.time_field().  Callable like FieldRef."""

    _resolver: Callable[[str, Any], Any] | None

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.TIME_FIELD)
        self._resolver = None

    def __call__(self, parent_table: Any) -> Any:
        if self._resolver is None:
            raise RuntimeError(
                f"TimeFieldRef({self.semantic_id!r}) has no resolver. "
                "TimeFieldRefs can only be called inside a loaded semantic project."
            )
        return self._resolver(self.semantic_id, parent_table)


class MetricRef(_BaseRef):
    """Ref returned by ms.metric() and ms.derived_metric(). Not callable.

    Derived metrics compose refs through decomposition builders, not direct
    metric calls.
    """

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.METRIC)


class RelationshipRef(_BaseRef):
    """Ref returned by ms.relationship().  Not callable."""

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.RELATIONSHIP)
