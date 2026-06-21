"""Intermediate representation dataclasses for marivo.semantic v1.1.

All IR dataclasses are frozen (value semantics).  Callable objects are
stored in a sidecar map, not in the IR itself.
"""

from __future__ import annotations

import re as _re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from marivo.refs import SymbolKind
from marivo.semantic.time_format import normalize_strptime

__all__ = [
    "Additivity",
    "AggKind",
    "AggregationTargetKind",
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
    "DomainIR",
    "EntityIR",
    "EntityProvenance",
    "EntitySourceIR",
    "EntityVersioningIR",
    "HourPrefixParse",
    "JoinKey",
    "LinearComposition",
    "LinearTerm",
    "MeasureIR",
    "MetricAdditivity",
    "MetricIR",
    "ParityStatus",
    "ParquetSourceIR",
    "RatioComposition",
    "RelationshipIR",
    "SampleIntervalIR",
    "SemanticParse",
    "SemiAdditive",
    "SnapshotVersioningIR",
    "SourceLocation",
    "SqlProvenance",
    "StrptimeParse",
    "SymbolKind",
    "TableSourceIR",
    "TimeFoldIR",
    "TimestampParse",
    "ValidityVersioningIR",
    "WeightedAverageComposition",
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


def _require_non_empty_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str, got {type(value).__name__}.")
    if not value:
        raise ValueError(f"{field_name} must be non-empty.")
    return value


def _require_kind(value: object, *, field_name: str, expected: str) -> None:
    if value != expected:
        raise ValueError(f"{field_name} must be {expected!r}, got {value!r}.")


def _validate_timezone_value(value: object, field_name: str) -> None:
    if value is None:
        return
    timezone = _require_non_empty_str(value, field_name)
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        raise ValueError(f"{field_name} must be a valid IANA timezone, got {value!r}.") from None


def _validate_sample_interval_value(value: object, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, SampleIntervalIR):
        raise TypeError(
            f"{field_name} must be SampleIntervalIR | None, got {type(value).__name__}."
        )


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
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation
    versioning: EntityVersioningIR | None = None


@dataclass(frozen=True)
class SampleIntervalIR:
    """Periodic sampling interval for a time dimension."""

    count: int
    unit: Literal["minute", "hour"]

    def __post_init__(self) -> None:
        if not isinstance(self.count, int) or isinstance(self.count, bool):
            raise TypeError(f"SampleIntervalIR.count must be int, got {type(self.count).__name__}.")
        if self.count < 1:
            raise ValueError(f"SampleIntervalIR.count must be positive, got {self.count}.")
        if self.unit not in ("minute", "hour"):
            raise ValueError(
                f"SampleIntervalIR.unit must be 'minute' or 'hour', got {self.unit!r}."
            )

    def to_token(self) -> str:
        return f"{self.count}{self.unit}"


# ---------------------------------------------------------------------------
# Time parse value objects (closed variants)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DateParse:
    """Parse a time-dimension column as a calendar date."""

    kind: Literal["date"] = "date"

    def __post_init__(self) -> None:
        _require_kind(self.kind, field_name="DateParse.kind", expected="date")


@dataclass(frozen=True)
class DatetimeParse:
    """Parse a time-dimension column as a datetime, optionally timezone-aware."""

    timezone: str | None = None
    sample_interval: SampleIntervalIR | None = None
    kind: Literal["datetime"] = "datetime"

    def __post_init__(self) -> None:
        _validate_timezone_value(self.timezone, "DatetimeParse.timezone")
        _validate_sample_interval_value(self.sample_interval, "DatetimeParse.sample_interval")
        _require_kind(self.kind, field_name="DatetimeParse.kind", expected="datetime")


@dataclass(frozen=True)
class TimestampParse:
    """Parse a time-dimension column as a timestamp, optionally timezone-aware."""

    timezone: str | None = None
    sample_interval: SampleIntervalIR | None = None
    kind: Literal["timestamp"] = "timestamp"

    def __post_init__(self) -> None:
        _validate_timezone_value(self.timezone, "TimestampParse.timezone")
        _validate_sample_interval_value(self.sample_interval, "TimestampParse.sample_interval")
        _require_kind(self.kind, field_name="TimestampParse.kind", expected="timestamp")


@dataclass(frozen=True)
class StrptimeParse:
    """Parse a time-dimension column using an explicit ``strptime`` format."""

    format: str
    timezone: str | None = None
    sample_interval: SampleIntervalIR | None = None
    kind: Literal["strptime"] = "strptime"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.format, "StrptimeParse.format")
        try:
            normalized = normalize_strptime(self.format)
        except ValueError as exc:
            raise ValueError(f"StrptimeParse.format is invalid: {exc}") from exc
        object.__setattr__(self, "format", normalized)
        _validate_timezone_value(self.timezone, "StrptimeParse.timezone")
        if self.timezone is not None and not is_time_bearing_format(normalized):
            raise ValueError("StrptimeParse.timezone is only supported for time-bearing formats.")
        _validate_sample_interval_value(self.sample_interval, "StrptimeParse.sample_interval")
        _require_kind(self.kind, field_name="StrptimeParse.kind", expected="strptime")


@dataclass(frozen=True)
class HourPrefixParse:
    """Parse a time-dimension column from an hour-prefixed string."""

    prefix: str
    sample_interval: SampleIntervalIR | None = None
    kind: Literal["hour_prefix"] = "hour_prefix"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.prefix, "HourPrefixParse.prefix")
        _validate_sample_interval_value(self.sample_interval, "HourPrefixParse.sample_interval")
        _require_kind(self.kind, field_name="HourPrefixParse.kind", expected="hour_prefix")


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

    def __post_init__(self) -> None:
        _require_non_empty_str(self.sql, "SqlProvenance.sql")
        _require_non_empty_str(self.dialect, "SqlProvenance.dialect")
        _require_kind(self.kind, field_name="SqlProvenance.kind", expected="from_sql")

    @property
    def verification_mode(self) -> Literal["sql_parity"]:
        return "sql_parity"


@dataclass(frozen=True)
class JoinKey:
    """One left/right relationship key pair."""

    from_key: str
    to_key: str

    def __post_init__(self) -> None:
        _require_non_empty_str(self.from_key, "JoinKey.from_key")
        _require_non_empty_str(self.to_key, "JoinKey.to_key")

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
AggregationTargetKind = Literal["measure", "entity"]


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
    ai_context: AiContextIR
    body_ast_hash: str
    python_symbol: str
    location: SourceLocation
    root_entity: str | None = None
    fanout_policy: Literal["block", "aggregate_then_join"] = "block"
    unit: str | None = None
    aggregation_target: str | None = None
    aggregation_target_kind: AggregationTargetKind | None = None
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
            has_target = self.aggregation_target is not None
            legacy_measure_target = self.measure is not None and not has_target
            if tier1 and not (has_target or legacy_measure_target):
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: tier-1 metric requires an aggregation target"
                )
            if not tier1 and (self.measure is not None or has_target):
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: tier-2 body metric must not carry "
                    "measure or aggregation target"
                )
            if has_target and self.aggregation_target_kind is None:
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: aggregation target requires a target kind"
                )
            if (
                self.aggregation_target_kind == "measure"
                and self.measure != self.aggregation_target
            ):
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: measure target must match measure"
                )
            if self.aggregation_target_kind == "entity" and self.measure is not None:
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: entity aggregate target must not carry measure"
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
    ai_context: AiContextIR
    location: SourceLocation


# ---------------------------------------------------------------------------
# Ref types
# ---------------------------------------------------------------------------


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
