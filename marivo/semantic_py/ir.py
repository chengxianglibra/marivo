from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from marivo.semantic_py.errors import SourceLocation as SourceLocation


@dataclass(frozen=True)
class SourceProvenance:
    sql: str | None = None
    dialect: str | None = None
    document: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class SymbolRef:
    kind: Literal["metric", "field", "time_field", "datasource"]
    name: str


@dataclass(frozen=True)
class TimeFieldMeta:
    data_type: Literal["date", "timestamp", "string", "integer"]
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"]
    format: str | None = None
    required_prefix: str | None = None


@dataclass(frozen=True)
class DecompositionIR:
    kind: Literal["sum", "ratio", "weighted_average"]
    numerator: str | None = None
    denominator: str | None = None
    weight: str | None = None


@dataclass(frozen=True)
class MetricReferences:
    datasets: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DatasourceIR:
    name: str
    backend_type: str | None
    description: str | None
    ai_context: dict[str, Any] | None
    source_location: SourceLocation


@dataclass
class FieldIR:
    name: str
    dataset_name: str
    fn: Callable[..., Any]
    is_time: bool
    time_meta: TimeFieldMeta | None
    label: str | None
    description: str | None
    ai_context: dict[str, Any] | None
    source_location: SourceLocation
    source: SourceProvenance | None = None


@dataclass
class DatasetIR:
    name: str
    fn: Callable[..., Any]
    datasource_name: str
    primary_key: list[str]
    unique_keys: list[list[str]]
    fields: dict[str, FieldIR]
    description: str | None
    ai_context: dict[str, Any] | None
    source_location: SourceLocation
    source: SourceProvenance | None = None


@dataclass
class TimeFieldIR(FieldIR):
    pass


@dataclass(frozen=True)
class MetricIR:
    name: str
    model_name: str
    fn: Callable[..., Any]
    decomposition: DecompositionIR
    description: str | None
    ai_context: dict[str, Any] | None
    references: MetricReferences
    source_location: SourceLocation
    source: SourceProvenance | None = None


@dataclass
class RelationshipIR:
    name: str
    from_dataset: str
    to_dataset: str
    from_columns: list[str]
    to_columns: list[str]
    source_location: SourceLocation
    description: str | None = None


@dataclass
class ModelIR:
    name: str
    description: str | None
    ai_context: dict[str, Any] | None
    datasources: dict[str, DatasourceIR] = field(default_factory=dict)
    datasets: dict[str, DatasetIR] = field(default_factory=dict)
    relationships: dict[str, RelationshipIR] = field(default_factory=dict)
    metrics: dict[str, MetricIR] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)
