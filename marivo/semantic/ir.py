"""Intermediate representation dataclasses for marivo.semantic v1.1.

All IR dataclasses are frozen (value semantics).  Callable objects are
stored in a sidecar map, not in the IR itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
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
    "DatasetVersioningIR",
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "DecompositionIR",
    "FieldIR",
    "FieldRef",
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
    "TimeFieldRef",
    "_BaseRef",
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
    PYTHON_NATIVE = "python_native"
    UNVERIFIED = "unverified"
    DRIFTED = "drifted"


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


DatasetVersioningIR = SnapshotVersioningIR


@dataclass(frozen=True)
class ProvenanceIR:
    """Source provenance and parity status for expression-bearing objects."""

    source_sql: str | None = None
    source_dialect: str | None = None
    source_document: str | None = None
    source_notes: str | None = None
    declared_status: Literal["python_native", "unverified"] | None = None


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
    """Ref returned by ms.metric().  Not callable.

    Derived metric composition uses ms.component() sentinels, not
    direct metric calls.
    """

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.METRIC)


class RelationshipRef(_BaseRef):
    """Ref returned by ms.relationship().  Not callable."""

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.RELATIONSHIP)
