"""Intermediate representation dataclasses for marivo.semantic v1.1.

All IR dataclasses are frozen (value semantics).  Callable objects are
stored in a sidecar map, not in the IR itself.
"""

from __future__ import annotations

import re as _re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from marivo.datasource.ir import (
    AiContextIR,
    CsvSourceIR,
    DatasourceIR,
    DatasourceSourceLocation,
    EntitySourceIR,
    ParquetSourceIR,
    TableSourceIR,
    source_name,
    source_to_dict,
)

__all__ = [
    "Additivity",
    "AggKind",
    "AiContextIR",
    "Composition",
    "CsvSourceIR",
    "DatasourceAiContextIR",
    "DatasourceIR",
    "DatasourceSourceLocation",
    "DateParse",
    "DatetimeParse",
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
    "HourPrefixParse",
    "JoinKey",
    "LinearComposition",
    "LinearTerm",
    "MeasureIR",
    "MeasureRef",
    "MetricAdditivity",
    "MetricIR",
    "MetricRef",
    "ParityStatus",
    "ParquetSourceIR",
    "RatioComposition",
    "RelationshipIR",
    "RelationshipRef",
    "SampleIntervalIR",
    "SemanticParse",
    "SemiAdditive",
    "SnapshotVersioningIR",
    "SourceLocation",
    "SqlProvenance",
    "StrptimeParse",
    "SymbolKind",
    "TableSourceIR",
    "TimeDimensionRef",
    "TimeFoldIR",
    "TimestampParse",
    "ValidityVersioningIR",
    "WeightedAverageComposition",
    "_BaseRef",
    "is_time_bearing_format",
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
    MEASURE = "measure"
    TIME_DIMENSION = "time_dimension"
    METRIC = "metric"
    RELATIONSHIP = "relationship"


class DimensionKind(StrEnum):
    """Kind of dimension: categorical or time."""

    CATEGORICAL = "categorical"
    TIME = "time"


class ParityStatus(StrEnum):
    """Parity verification status for metrics."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    DRIFTED = "drifted"


class MetricAdditivity(StrEnum):
    """Metric summability relative to its entity row grain."""

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
    open_end: tuple[str | None, ...]
    timezone: str | None = None


EntityVersioningIR = SnapshotVersioningIR | ValidityVersioningIR


def source_from_dict(data: Mapping[str, object]) -> EntitySourceIR:
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
    if kind == "parquet":
        raw_columns = data.get("columns")
        columns = tuple(str(col) for col in raw_columns) if isinstance(raw_columns, list) else None
        return ParquetSourceIR(
            path=str(data["path"]),
            hive_partitioning=bool(data.get("hive_partitioning", False)),
            columns=columns,
        )
    if kind == "csv":
        raw_columns = data.get("columns")
        columns = tuple(str(col) for col in raw_columns) if isinstance(raw_columns, list) else None
        return CsvSourceIR(
            path=str(data["path"]),
            header=bool(data.get("header", True)),
            delimiter=str(data.get("delimiter", ",")),
            columns=columns,
        )
    raise ValueError(f"unsupported entity source kind: {kind!r}")


def source_label(source: EntitySourceIR) -> str:
    if isinstance(source, TableSourceIR):
        if source.database is None:
            return source.table
        database = (
            ".".join(source.database) if isinstance(source.database, tuple) else source.database
        )
        return f"{database}.{source.table}"
    return source.path


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


# ---------------------------------------------------------------------------
# Time parse value objects (closed variants)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DateParse:
    kind: Literal["date"] = "date"


@dataclass(frozen=True)
class DatetimeParse:
    timezone: str
    sample_interval: SampleIntervalIR | None = None
    kind: Literal["datetime"] = "datetime"


@dataclass(frozen=True)
class TimestampParse:
    timezone: str
    sample_interval: SampleIntervalIR | None = None
    kind: Literal["timestamp"] = "timestamp"


@dataclass(frozen=True)
class StrptimeParse:
    format: str
    data_type: Literal["string", "integer"]
    timezone: str | None = None
    kind: Literal["strptime"] = "strptime"


@dataclass(frozen=True)
class HourPrefixParse:
    prefix: str
    data_type: Literal["string", "integer"]
    kind: Literal["hour_prefix"] = "hour_prefix"


SemanticParse = DateParse | DatetimeParse | TimestampParse | StrptimeParse | HourPrefixParse


# ---------------------------------------------------------------------------
# Provenance and join-key value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SqlProvenance:
    """SQL parity provenance for a Python-authored metric body."""

    sql: str
    dialect: str
    kind: Literal["from_sql"] = "from_sql"

    @property
    def verification_mode(self) -> Literal["sql_parity"]:
        return "sql_parity"


@dataclass(frozen=True)
class JoinKey:
    """One left/right relationship key pair."""

    from_key: str
    to_key: str

    def to_tuple(self) -> tuple[str, str]:
        return (self.from_key, self.to_key)


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


AggKind = (
    Literal["sum", "count", "count_distinct", "min", "max", "mean", "median"]
    | tuple[Literal["percentile"], float]
)


@dataclass(frozen=True)
class SemiAdditive:
    """Semi-additive marker: additive on non-time axes, folded along ``over``."""

    over: str  # status_time_dimension semantic id
    fold: TimeFoldIR  # time-axis collapse op (never "sum"/"none")


Additivity = Literal["additive", "non_additive"] | SemiAdditive


@dataclass(frozen=True)
class DimensionIR:
    """Categorical or time dimension declaration."""

    semantic_id: str
    domain: str
    entity: str
    name: str
    description: str | None
    ai_context: AiContextIR
    is_time_dimension: bool
    kind: DimensionKind
    python_symbol: str
    location: SourceLocation
    granularity: str | None = None
    parse: SemanticParse | None = None
    is_default: bool = False

    def __post_init__(self) -> None:
        if self.is_time_dimension != (self.kind == DimensionKind.TIME):
            raise ValueError(
                f"DimensionIR {self.semantic_id!r}: is_time_dimension={self.is_time_dimension} "
                f"inconsistent with kind={self.kind.value!r}"
            )
        if self.kind == DimensionKind.TIME and self.parse is None:
            raise ValueError(f"DimensionIR {self.semantic_id!r}: time dimension requires parse")
        if self.kind == DimensionKind.CATEGORICAL and self.parse is not None:
            raise ValueError(
                f"DimensionIR {self.semantic_id!r}: categorical dimension must not carry parse"
            )


@dataclass(frozen=True)
class MeasureIR:
    """Row-level quantitative declaration that metrics aggregate."""

    semantic_id: str
    domain: str
    entity: str
    name: str
    description: str | None
    ai_context: AiContextIR
    additivity: Additivity
    unit: str | None
    python_symbol: str
    location: SourceLocation
    kind: SymbolKind = SymbolKind.MEASURE


@dataclass(frozen=True)
class RatioComposition:
    numerator: str
    denominator: str
    kind: Literal["ratio"] = "ratio"


@dataclass(frozen=True)
class WeightedAverageComposition:
    value: str
    weight: str
    kind: Literal["weighted_average"] = "weighted_average"


@dataclass(frozen=True)
class LinearTerm:
    sign: Literal["+", "-"]
    metric: str


@dataclass(frozen=True)
class LinearComposition:
    terms: tuple[LinearTerm, ...]
    kind: Literal["linear"] = "linear"

    def __post_init__(self) -> None:
        if len(self.terms) < 2:
            raise ValueError("LinearComposition requires at least two terms")


Composition = RatioComposition | WeightedAverageComposition | LinearComposition


def additivity_bucket(
    additivity: Additivity,
) -> Literal["additive", "semi_additive", "non_additive"]:
    """Collapse an Additivity value to its three-bucket summary for analysis/display."""
    if isinstance(additivity, SemiAdditive):
        return "semi_additive"
    return additivity


def composition_components(composition: Composition) -> dict[str, str]:
    """Role-keyed component refs for a derived metric composition."""
    if isinstance(composition, RatioComposition):
        return {"numerator": composition.numerator, "denominator": composition.denominator}
    if isinstance(composition, WeightedAverageComposition):
        return {"value": composition.value, "weight": composition.weight}
    return {f"term{i}": term.metric for i, term in enumerate(composition.terms)}


# Temporary compat alias — removed when authoring.py's metric/derived_metric
# are removed (Task 12).
@dataclass(frozen=True)
class DecompositionIR:
    """Decomposition semantics for a metric (DEPRECATED: use Composition)."""

    kind: Literal["sum", "ratio", "weighted_average"]
    components: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricIR:
    """Metric declaration: simple (tier-1 aggregate / tier-2 body) or derived."""

    semantic_id: str
    domain: str
    name: str
    metric_type: Literal["simple", "derived"]
    entities: tuple[str, ...]
    aggregation: AggKind | None
    measure: str | None
    composition: Composition | None
    additivity: Additivity | None
    provenance: SqlProvenance | None
    description: str | None
    ai_context: AiContextIR
    body_ast_hash: str
    python_symbol: str
    location: SourceLocation
    root_entity: str | None = None
    fanout_policy: Literal["block", "aggregate_then_join"] = "block"
    unit: str | None = None
    fold_override: TimeFoldIR | None = (
        None  # tier-1 only: overrides the measure's semi-additive fold at load
    )

    def __post_init__(self) -> None:
        if self.fold_override is not None and self.aggregation is None:
            raise ValueError(
                f"MetricIR {self.semantic_id!r}: fold_override is only valid on tier-1 aggregates"
            )
        if self.metric_type == "simple":
            if not self.entities:
                raise ValueError(f"MetricIR {self.semantic_id!r}: simple metric requires entities")
            if self.composition is not None:
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: simple metric must not carry composition"
                )
            tier1 = self.aggregation is not None
            if tier1 != (self.measure is not None):
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: aggregation and measure must both be set "
                    "(tier-1) or both be None (tier-2 body)"
                )
            if not tier1 and self.additivity is None:
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: tier-2 simple metric must declare additivity"
                )
        elif self.metric_type == "derived":
            if self.entities:
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: derived metric must not carry entities"
                )
            if self.composition is None:
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: derived metric requires composition"
                )
            if self.aggregation is not None or self.measure is not None:
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: derived metric must not carry aggregation/measure"
                )
        else:
            raise ValueError(
                f"MetricIR {self.semantic_id!r}: invalid metric_type {self.metric_type!r}"
            )

    @property
    def status_time_dimension(self) -> str | None:
        """Compatibility accessor: the semi-additive over axis, or None."""
        if isinstance(self.additivity, SemiAdditive):
            return self.additivity.over
        return None

    @property
    def time_fold(self) -> TimeFoldIR | None:
        """Compatibility accessor: the effective fold (fold_override > additivity.fold)."""
        if self.fold_override is not None:
            return self.fold_override
        if isinstance(self.additivity, SemiAdditive):
            return self.additivity.fold
        return None


@dataclass(frozen=True)
class RelationshipIR:
    """Relationship between two entities."""

    semantic_id: str
    domain: str
    name: str
    from_entity: str
    to_entity: str
    keys: tuple[JoinKey, ...]
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

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Deferred import avoids an ir <-> errors import cycle.
        from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise

        _raise(
            ErrorKind.INVALID_REF,
            f"{self.semantic_id!r} is a declared semantic object, not a decorator. "
            "Body-free constructors (ms.ratio / ms.weighted_average / ms.linear / "
            "ms.aggregate / ms.relationship) return a ref — assign it, e.g. "
            "`loss_rate = ms.ratio(name=..., numerator=..., denominator=...)`. "
            "They have no function body.",
            cls=SemanticDecoratorError,
        )

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


class MeasureRef(_BaseRef):
    """Ref returned by ms.measure(). Callable like DimensionRef."""

    _resolver: Callable[[str, Any], Any] | None

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.MEASURE)
        self._resolver = None

    def __call__(self, parent_table: Any) -> Any:
        if self._resolver is None:
            raise RuntimeError(
                f"MeasureRef({self.semantic_id!r}) has no resolver. "
                "MeasureRefs can only be called inside a loaded semantic project."
            )
        return self._resolver(self.semantic_id, parent_table)


class MetricRef(_BaseRef):
    """Ref returned by ms.aggregate(), @ms.metric(), and derived constructors.

    Not callable as a decorator. _BaseRef.__call__ raises a teaching error.
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


# ---------------------------------------------------------------------------
# Strptime format classification (shared by semantic and analysis)
# ---------------------------------------------------------------------------

_DATE_DIRECTIVES = frozenset({"%Y", "%y", "%m", "%d", "%j", "%U", "%W"})
_HOUR_DIRECTIVES = frozenset({"%H", "%I", "%k", "%l"})
_MINUTE_DIRECTIVES = frozenset({"%M"})
_SECOND_DIRECTIVES = frozenset({"%S"})
_SUBSECOND_DIRECTIVES = frozenset({"%f"})
_AMPM_DIRECTIVES = frozenset({"%p", "%P"})


def is_time_bearing_format(fmt: str | None) -> bool:
    """Return True if a strptime format encodes time-of-day (not just day/hour-only).

    A format is time-bearing when it contains time-of-day directives (hour,
    minute, second) alongside a date directive.  Formats without a date
    component (e.g. ``"%H"``, ``"%H%M"``) are partition encodings, not
    timezone-relevant.

    Args:
        fmt: A strptime format string, or None.

    Returns:
        True if the format encodes time-of-day information.

    Example:
        >>> is_time_bearing_format("%Y%m%d")
        False
        >>> is_time_bearing_format("%Y-%m-%d %H:%M:%S")
        True
        >>> is_time_bearing_format("%H")
        False
        >>> is_time_bearing_format("%H%M")
        False
    """
    if fmt is None or not fmt.startswith("%"):
        return False
    tokens = set(_re.findall(r"%[a-zA-Z]", fmt))
    has_date = bool(_DATE_DIRECTIVES & tokens)
    has_hour = bool((_HOUR_DIRECTIVES | _AMPM_DIRECTIVES) & tokens)
    has_minute = bool(_MINUTE_DIRECTIVES & tokens)
    has_second = bool(_SECOND_DIRECTIVES & tokens)
    has_subsecond = bool(_SUBSECOND_DIRECTIVES & tokens)

    # Without a date, any time-of-day component is a partition encoding,
    # not a timezone-relevant timestamp.
    return has_date and (has_subsecond or has_second or has_minute or has_hour)
