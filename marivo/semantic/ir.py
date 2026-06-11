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
    EntitySourceIR,
    FileSourceIR,
    TableSourceIR,
    source_from_dict,
    source_label,
    source_name,
    source_to_dict,
)

__all__ = [
    "AiContextIR",
    "BoundedProfilePolicyIR",
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "DecompositionIR",
    "DimensionIR",
    "DimensionKind",
    "DimensionRef",
    "DomainIR",
    "DomainRef",
    "EntityIR",
    "EntityProvenance",
    "EntityRef",
    "EntitySourceIR",
    "EntityVersioningIR",
    "FileSourceIR",
    "MetadataOnlyPolicyIR",
    "MetricAdditivity",
    "MetricIR",
    "MetricRef",
    "ParityStatus",
    "ProvenanceIR",
    "RelationshipIR",
    "RelationshipRef",
    "SampleIntervalIR",
    "SamplePolicyIR",
    "SelectedColumnsPolicyIR",
    "SnapshotVersioningIR",
    "SourceLocation",
    "SymbolKind",
    "TableSourceIR",
    "TimeDimensionRef",
    "TimeFoldIR",
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

    DOMAIN = "domain"
    DATASOURCE = "datasource"
    ENTITY = "entity"
    DIMENSION = "dimension"
    TIME_DIMENSION = "time_dimension"
    METRIC = "metric"
    RELATIONSHIP = "relationship"


class DimensionKind(StrEnum):
    """Kind of dimension: categorical, measure, or time."""

    CATEGORICAL = "categorical"
    MEASURE = "measure"
    TIME = "time"


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


class EntityProvenance(StrEnum):
    """How an entity's physical table was produced."""

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


EntityVersioningIR = SnapshotVersioningIR | ValidityVersioningIR


@dataclass(frozen=True)
class MetadataOnlyPolicyIR:
    timeout_seconds: int | None = None
    kind: Literal["metadata_only"] = "metadata_only"


@dataclass(frozen=True)
class BoundedProfilePolicyIR:
    limit: int
    timeout_seconds: int | None = None
    max_profiled_columns: int | None = None
    kind: Literal["bounded_profile"] = "bounded_profile"


@dataclass(frozen=True)
class SelectedColumnsPolicyIR:
    limit: int
    columns: tuple[str, ...]
    timeout_seconds: int | None = None
    max_profiled_columns: int | None = None
    kind: Literal["selected_columns_profile"] = "selected_columns_profile"


SamplePolicyIR = MetadataOnlyPolicyIR | BoundedProfilePolicyIR | SelectedColumnsPolicyIR


@dataclass(frozen=True)
class ProvenanceIR:
    """Source provenance and verification mode for expression-bearing objects."""

    source_sql: str | None = None
    source_dialect: str | None = None
    source_document: str | None = None
    source_notes: str | None = None
    verification_mode: Literal["sql_parity", "python_native"] | None = None


@dataclass(frozen=True)
class DomainIR:
    """Semantic domain container."""

    name: str
    description: str | None
    default: bool
    ai_context: AiContextIR
    location: SourceLocation


@dataclass(frozen=True)
class EntityIR:
    """Entity declaration with physical grounding."""

    semantic_id: str
    domain: str
    name: str
    datasource: str
    source: EntitySourceIR
    primary_key: tuple[str, ...]
    description: str | None
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation
    versioning: EntityVersioningIR | None = None


@dataclass(frozen=True)
class SampleIntervalIR:
    """Periodic sampling interval for a time dimension."""

    count: int
    unit: Literal["minute", "hour"]

    def to_token(self) -> str:
        return f"{self.count}{self.unit}"


@dataclass(frozen=True)
class TimeFoldIR:
    """Time folding declaration for sampled semi-additive metrics."""

    kind: Literal["mean", "min", "max", "first", "last", "quantile"]
    q: float | None = None

    def __post_init__(self) -> None:
        if self.kind == "quantile" and self.q is None:
            msg = "TimeFoldIR(kind='quantile') requires q to be set"
            raise ValueError(msg)

    def label(self) -> str:
        if self.kind == "quantile":
            return f"quantile({self.q})"
        return self.kind


@dataclass(frozen=True)
class DimensionIR:
    """Dimension declaration (categorical or measure column)."""

    semantic_id: str
    domain: str
    entity: str
    name: str
    description: str | None
    ai_context: AiContextIR
    is_time_dimension: bool
    kind: DimensionKind
    data_type: str | None
    granularity: str | None
    required_prefix: str | None
    python_symbol: str
    location: SourceLocation
    format: str | None = None
    timezone: str | None = None
    is_default: bool = False
    sample_interval: SampleIntervalIR | None = None

    def __post_init__(self) -> None:
        if self.is_time_dimension != (self.kind == DimensionKind.TIME):
            raise ValueError(
                f"DimensionIR {self.semantic_id!r}: is_time_dimension={self.is_time_dimension} "
                f"inconsistent with kind={self.kind.value!r}"
            )


@dataclass(frozen=True)
class DecompositionIR:
    """Decomposition semantics for a metric."""

    kind: Literal["sum", "ratio", "weighted_average"]
    components: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricIR:
    """Metric declaration with decomposition and provenance."""

    semantic_id: str
    domain: str
    name: str
    entities: tuple[str, ...]
    is_derived: bool
    decomposition: DecompositionIR
    provenance: ProvenanceIR
    description: str | None
    ai_context: AiContextIR
    body_ast_hash: str
    python_symbol: str
    location: SourceLocation
    additivity: Literal["additive", "semi_additive", "non_additive"] | None = None
    root_entity: str | None = None
    fanout_policy: Literal["block", "aggregate_then_join"] = "block"
    unit: str | None = None
    time_fold: TimeFoldIR | None = None
    fold_time_dimension: str | None = None


@dataclass(frozen=True)
class RelationshipIR:
    """Relationship between two entities."""

    semantic_id: str
    domain: str
    name: str
    from_entity: str
    to_entity: str
    from_dimensions: tuple[str, ...]
    to_dimensions: tuple[str, ...]
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
        normalized = semantic_id.strip()
        if not normalized:
            raise ValueError("ref semantic_id must be non-empty")
        self.semantic_id = normalized
        self.kind = kind

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.semantic_id!r})"

    def __str__(self) -> str:
        return self.semantic_id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _BaseRef):
            return NotImplemented
        return type(self) is type(other) and self.semantic_id == other.semantic_id

    def __hash__(self) -> int:
        return hash((type(self), self.semantic_id))

    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type: Any, _handler: Any) -> Any:
        """Allow ref types to be used as Pydantic field types (e.g. PromotionSemanticAnchors)."""
        from pydantic_core import core_schema

        # _source_type is the concrete ref subclass (DimensionRef, MetricRef, …)
        # which only requires semantic_id. Fall back to cls when Pydantic
        # resolves the schema directly on _BaseRef.
        ref_cls: type[_BaseRef] = _source_type if _source_type is not _BaseRef else cls

        def validate(value: Any) -> _BaseRef:
            if isinstance(value, ref_cls):
                return value
            if isinstance(value, str):
                return ref_cls(value)  # type: ignore[call-arg]
            raise ValueError(f"expected str or {ref_cls.__name__}, got {type(value).__name__}")

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda v: v.semantic_id,
                info_arg=False,
            ),
        )


class EntityRef(_BaseRef):
    """Ref returned by ms.entity().  Not callable."""

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.ENTITY)


class DimensionRef(_BaseRef):
    """Ref returned by ms.dimension().  Callable in base metric bodies.

    Calling ``dimension_ref(parent_table)`` resolves to the sidecar callable
    stored in the loader registry and invokes it with the parent table.
    """

    _resolver: Callable[[str, Any], Any] | None

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.DIMENSION)
        self._resolver = None

    def __call__(self, parent_table: Any) -> Any:
        if self._resolver is None:
            raise RuntimeError(
                f"DimensionRef({self.semantic_id!r}) has no resolver. "
                "DimensionRefs can only be called inside a loaded semantic project."
            )
        return self._resolver(self.semantic_id, parent_table)


class TimeDimensionRef(_BaseRef):
    """Ref returned by ms.time_dimension().  Callable like DimensionRef."""

    _resolver: Callable[[str, Any], Any] | None

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.TIME_DIMENSION)
        self._resolver = None

    def __call__(self, parent_table: Any) -> Any:
        if self._resolver is None:
            raise RuntimeError(
                f"TimeDimensionRef({self.semantic_id!r}) has no resolver. "
                "TimeDimensionRefs can only be called inside a loaded semantic project."
            )
        return self._resolver(self.semantic_id, parent_table)


class MetricRef(_BaseRef):
    """Ref returned by ms.metric() and ms.derived_metric(). Not callable.

    Derived metrics compose refs through decomposition builders, not direct
    metric calls.
    """

    def __init__(self, semantic_id: str) -> None:
        normalized = semantic_id.strip()
        model, separator, metric = normalized.partition(".")
        if not separator or not model or not metric:
            raise ValueError(f"metric ref must be '<model>.<metric>', got {semantic_id!r}")
        super().__init__(normalized, SymbolKind.METRIC)


class RelationshipRef(_BaseRef):
    """Ref returned by ms.relationship().  Not callable."""

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.RELATIONSHIP)


class DomainRef(_BaseRef):
    """Ref returned by ms.domain().  Not callable."""

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.DOMAIN)
