"""Intermediate representation dataclasses for marivo.semantic v1.1.

All IR dataclasses are frozen (value semantics).  Callable objects are
stored in a sidecar map, not in the IR itself.
"""

from __future__ import annotations

import re as _re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marivo._compat import StrEnum
from marivo._temporal import Grain as TemporalGrain
from marivo.datasource.ir import (
    AiContextIR,
    CsvSourceIR,
    DatasourceIR,
    DatasourceSourceLocation,
    EntitySourceIR,
    JsonBodyParam,
    JsonQueryParamValue,
    JsonSourceIR,
    ParquetSourceIR,
    SourceParamIR,
    TableColumnBindingIR,
    TableSourceIR,
    json_body_to_string,
    source_name,
    source_to_dict,
)
from marivo.refs import SemanticKind
from marivo.semantic.time_format import normalize_strptime

__all__ = [
    "Additivity",
    "AggKind",
    "AggregateFoldInput",
    "AggregateFoldValue",
    "AggregationTargetKind",
    "AiContextIR",
    "Composition",
    "CsvSourceIR",
    "CumulativeComposition",
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
    "EventIR",
    "EventParticipantIR",
    "HourPrefixParse",
    "JoinKey",
    "JsonSourceIR",
    "LifecycleStateIR",
    "LinearComposition",
    "LinearTerm",
    "MeasureIR",
    "MetricAdditivity",
    "MetricIR",
    "ParityStatus",
    "ParquetSourceIR",
    "PeriodCalendarIR",
    "RatioComposition",
    "RelationshipIR",
    "SampleIntervalIR",
    "SemanticKind",
    "SemanticParse",
    "SemiAdditive",
    "SnapshotVersioningIR",
    "SourceLocation",
    "SqlProvenance",
    "StateInceptionIR",
    "StateModelIR",
    "StateTransitionIR",
    "StateTriggerIR",
    "StrptimeParse",
    "TableSourceIR",
    "TemporalSetIR",
    "TimeFoldIR",
    "TimestampParse",
    "ValidityVersioningIR",
    "WeightedMeanAggregation",
    "WorkScheduleIR",
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


def _source_schema_from_dict(value: object, *, field_name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        return ()
    normalized: list[tuple[str, str]] = []
    for name, type_name in value.items():
        if not isinstance(name, str) or not isinstance(type_name, str):
            raise TypeError(f"{field_name} column names and type names must be strings.")
        normalized.append((name, type_name))
    return tuple(normalized)


def _table_columns_from_dict(
    value: object,
) -> tuple[tuple[str, TableColumnBindingIR], ...]:
    if not isinstance(value, Mapping):
        raise TypeError("TableSourceIR.columns must be a mapping.")
    if not value:
        raise ValueError("TableSourceIR.columns must contain at least one binding.")

    normalized: list[tuple[str, TableColumnBindingIR]] = []
    expected_keys = {"source", "data_type"}
    for output_name, raw_binding in value.items():
        if not isinstance(output_name, str):
            raise TypeError("TableSourceIR.columns output names must be strings.")
        if not isinstance(raw_binding, Mapping):
            raise TypeError(
                "TableSourceIR.columns values must be mappings with source and data_type."
            )
        received_keys = set(raw_binding)
        if received_keys != expected_keys:
            missing = sorted(expected_keys - received_keys)
            unknown = sorted(str(key) for key in received_keys - expected_keys)
            details = []
            if missing:
                details.append(f"missing keys {missing!r}")
            if unknown:
                details.append(f"unknown keys {unknown!r}")
            raise ValueError(
                f"TableSourceIR.columns binding for {output_name!r} has "
                + " and ".join(details)
                + "."
            )
        source = raw_binding["source"]
        data_type = raw_binding["data_type"]
        if not isinstance(source, str) or not isinstance(data_type, str):
            raise TypeError("TableSourceIR.columns binding source and data_type must be strings.")
        normalized.append(
            (
                output_name,
                TableColumnBindingIR(source=source, data_type=data_type),
            )
        )
    return tuple(normalized)


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
        table_columns = _table_columns_from_dict(data["columns"]) if "columns" in data else ()
        return TableSourceIR(
            table=str(data["table"]),
            database=database,
            columns=table_columns,
        )
    if kind == "parquet":
        raw_columns = data.get("columns")
        columns = tuple(str(col) for col in raw_columns) if isinstance(raw_columns, list) else None
        return ParquetSourceIR(
            path=str(data["path"]),
            hive_partitioning=bool(data.get("hive_partitioning", False)),
            columns=columns,
        )
    if kind == "csv":
        return CsvSourceIR(
            path=str(data["path"]),
            schema=_source_schema_from_dict(data.get("schema"), field_name="CsvSourceIR.schema"),
            header=bool(data.get("header", True)),
            delimiter=str(data.get("delimiter", ",")),
        )
    if kind == "json":
        raw_format = str(data.get("format", "auto"))
        raw_records_path = data.get("records_path")
        raw_query_params = data.get("query_params", {})
        raw_method = str(data.get("method", "GET"))
        raw_body = data.get("body")
        raw_body_params = data.get("body_params", [])
        if not isinstance(raw_query_params, Mapping):
            raise TypeError("JsonSourceIR.query_params must be a mapping.")
        query_params: list[tuple[str, object]] = []
        for name, raw_value in raw_query_params.items():
            if not isinstance(name, str):
                raise TypeError("JsonSourceIR.query_params names must be strings.")
            value: object = raw_value
            if isinstance(raw_value, Mapping) and raw_value.get("kind") == "source_param":
                value = SourceParamIR(name=str(raw_value.get("name", "")))
            query_params.append((name, value))
        if not isinstance(raw_body_params, Sequence) or isinstance(raw_body_params, str | bytes):
            raise TypeError("JsonSourceIR.body_params must be a sequence.")
        body_params: list[JsonBodyParam] = []
        for raw_param in raw_body_params:
            if not isinstance(raw_param, Mapping):
                raise TypeError("JsonSourceIR.body_params entries must be mappings.")
            raw_path = raw_param.get("path")
            raw_name = raw_param.get("name")
            if not isinstance(raw_path, Sequence) or isinstance(raw_path, str | bytes):
                raise TypeError("JsonSourceIR.body_params paths must be sequences.")
            path: list[str | int] = []
            for part in raw_path:
                if isinstance(part, str) or (isinstance(part, int) and not isinstance(part, bool)):
                    path.append(part)
                else:
                    raise TypeError(
                        "JsonSourceIR.body_params path parts must be strings or integers."
                    )
            if not isinstance(raw_name, str):
                raise TypeError("JsonSourceIR.body_params names must be strings.")
            body_params.append((tuple(path), SourceParamIR(name=raw_name)))
        return JsonSourceIR(
            path=str(data["path"]),
            schema=_source_schema_from_dict(data.get("schema"), field_name="JsonSourceIR.schema"),
            format=cast('Literal["auto", "newline_delimited", "array"]', raw_format),
            records_path=cast("str | None", raw_records_path),
            query_params=cast("tuple[tuple[str, JsonQueryParamValue], ...]", tuple(query_params)),
            method=cast('Literal["GET", "POST"]', raw_method),
            body_json=json_body_to_string(raw_body) if raw_body is not None else None,
            body_params=tuple(body_params),
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
    owner: str
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
class PeriodCalendarIR:
    """Authored identity and source-field contract for one period authority."""

    semantic_id: str
    domain: str
    name: str
    date: str
    boundary_timezone: str
    coverage: tuple[str, str]
    levels: tuple[tuple[str, str], ...]
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation
    correspondences: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class TemporalSetIR:
    """Authored identity and source-field contract for named occurrences."""

    semantic_id: str
    domain: str
    name: str
    occurrence_id: str
    start: str
    end: str
    boundary_timezone: str
    coverage: tuple[str, str]
    category: str | None
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation


@dataclass(frozen=True)
class WorkScheduleIR:
    """Authored identity and source-field contract for final daily status."""

    semantic_id: str
    domain: str
    name: str
    date: str
    is_working: str
    boundary_timezone: str
    coverage: tuple[str, str]
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation


@dataclass(frozen=True)
class EventParticipantIR:
    """One normalized participant role owned by an Event."""

    name: str
    path: tuple[str, ...] | None
    cardinality: Literal["one", "optional_one"]


@dataclass(frozen=True)
class EventIR:
    """Executable occurrence semantics over one existing Entity."""

    semantic_id: str
    domain: str
    name: str
    source_entity: str
    identity: tuple[str, ...]
    occurred_at: str
    participants: tuple[EventParticipantIR, ...]
    predicate_kind: Literal["all_rows", "filtered"]
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation
    body_ast_hash: str


@dataclass(frozen=True)
class LifecycleStateIR:
    """One closed state definition owned by a StateModel."""

    name: str
    initial: bool
    terminal: bool


@dataclass(frozen=True)
class StateTriggerDeclarationIR:
    """Authoring-time Event trigger before catalog role resolution."""

    event_ref: str
    participant_role: str | None


@dataclass(frozen=True)
class StateTriggerIR:
    """Canonical Event and participant-role trigger."""

    event_ref: str
    participant_role: str


@dataclass(frozen=True)
class StateInceptionIR:
    """Canonical transition from unseeded history into the initial state."""

    trigger: StateTriggerIR


@dataclass(frozen=True)
class StateTransitionIR:
    """Canonical deterministic transition between modeled states."""

    from_state: str
    trigger: StateTriggerIR
    to_state: str


@dataclass(frozen=True)
class StateModelDeclarationIR:
    """Authoring-time StateModel awaiting canonical trigger resolution."""

    semantic_id: str
    domain: str
    name: str
    subject: str
    states: tuple[LifecycleStateIR, ...]
    inceptions: tuple[StateTriggerDeclarationIR, ...]
    transitions: tuple[tuple[str, StateTriggerDeclarationIR, str], ...]
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation


@dataclass(frozen=True)
class StateModelIR:
    """Canonical finite normative lifecycle for one subject Entity."""

    semantic_id: str
    domain: str
    name: str
    subject: str
    states: tuple[LifecycleStateIR, ...]
    inceptions: tuple[StateInceptionIR, ...]
    transitions: tuple[StateTransitionIR, ...]
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation


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

    kind: Literal["mean", "min", "max", "first", "last", "percentile"]
    q: float | None = None

    def __post_init__(self) -> None:
        if self.kind == "percentile" and self.q is None:
            msg = "TimeFoldIR(kind='percentile') requires q to be set"
            raise ValueError(msg)

    def label(self) -> str:
        if self.kind == "percentile":
            return f"percentile({self.q})"
        return self.kind


AggregateFoldValue: TypeAlias = (
    Literal["mean", "min", "max", "first", "last"] | tuple[Literal["percentile"], float]
)
AggregateFoldInput: TypeAlias = AggregateFoldValue | None


AggKind = (
    Literal["sum", "count", "count_distinct", "min", "max", "mean", "median"]
    | tuple[Literal["percentile"], float]
)
AggregationTargetKind = Literal["measure", "entity"]

# Predicate values for a filtered tier-1 aggregation.
WhereScalar = str | int | float | bool
WhereValue = WhereScalar | tuple[WhereScalar, ...]


@dataclass(frozen=True)
class WhereFilter:
    """AND-joined equality or membership predicates for a filtered tier-1 metric.

    Built by ``ms.where(dimension=value, ...)`` and consumed by ``ms.count`` /
    ``ms.aggregate`` to restrict the aggregated rows. Scalar values mean
    equality; tuple values mean membership.
    """

    conditions: tuple[tuple[str, WhereValue], ...]


# Tuple form of :class:`WhereFilter` stored on MetricIR (JSON-safe, hashable).
FilterIR = tuple[tuple[str, WhereValue], ...]


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
    body_ast_hash: str = ""
    source_column: str | None = None

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
    kind: SemanticKind = SemanticKind.MEASURE
    body_ast_hash: str = ""


@dataclass(frozen=True)
class RatioComposition:
    numerator: str
    denominator: str
    kind: Literal["ratio"] = "ratio"


@dataclass(frozen=True)
class WeightedMeanAggregation:
    """Two-measure physical aggregate for an exact weighted mean."""

    value: str
    weight: str
    kind: Literal["weighted_mean"] = "weighted_mean"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.value, "WeightedMeanAggregation.value")
        _require_non_empty_str(self.weight, "WeightedMeanAggregation.weight")
        _require_kind(
            self.kind, field_name="WeightedMeanAggregation.kind", expected="weighted_mean"
        )


# Anchor payloads: the closed-kind growth the v1 anchor-in-hash commitment
# reserved. ``all_history`` stays a plain string (byte-identical v1 hash);
# the new kinds carry their parameters as a tuple.
CumulativeAnchor = (
    Literal["all_history"]
    | tuple[Literal["grain_to_date"], str | TemporalGrain]
    | tuple[Literal["trailing"], int, str]
)

# Reset grains for grain-to-date anchors (MTD/QTD/YTD/WTD).
_GRAIN_TO_DATE_RESETS = ("week", "month", "quarter", "year")
# Fixed-size units accepted by trailing anchors (rolling N).
_TRAILING_FIXED_UNITS = ("second", "minute", "hour", "day", "week")


def _validate_cumulative_anchor(anchor: object) -> None:
    """Reject unknown anchor shapes at IR construction time."""
    if anchor == "all_history":
        return
    if isinstance(anchor, tuple):
        if (
            len(anchor) == 2
            and anchor[0] == "grain_to_date"
            and (
                (isinstance(anchor[1], str) and anchor[1] in _GRAIN_TO_DATE_RESETS)
                or (
                    isinstance(anchor[1], TemporalGrain)
                    and (
                        anchor[1].kind == "semantic"
                        or anchor[1].to_token() in _GRAIN_TO_DATE_RESETS
                    )
                )
            )
        ):
            return
        if (
            len(anchor) == 3
            and anchor[0] == "trailing"
            and isinstance(anchor[1], int)
            and not isinstance(anchor[1], bool)
            and anchor[1] >= 1
            and isinstance(anchor[2], str)
            and anchor[2] in _TRAILING_FIXED_UNITS
        ):
            return
    raise ValueError(f"invalid CumulativeComposition.anchor: {anchor!r}")


@dataclass(frozen=True)
class CumulativeComposition:
    base: str
    over: str | None
    anchor: CumulativeAnchor = "all_history"
    kind: Literal["cumulative"] = "cumulative"

    def __post_init__(self) -> None:
        _require_non_empty_str(self.base, "CumulativeComposition.base")
        if self.over is not None:
            _require_non_empty_str(self.over, "CumulativeComposition.over")
        _validate_cumulative_anchor(self.anchor)
        _require_kind(self.kind, field_name="CumulativeComposition.kind", expected="cumulative")


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


Composition = RatioComposition | LinearComposition | CumulativeComposition


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
    if isinstance(composition, CumulativeComposition):
        return {"base": composition.base}
    return {f"term{i}": term.metric for i, term in enumerate(composition.terms)}


# Temporary compat alias — removed when authoring.py's metric/derived_metric
# are removed (Task 12).
@dataclass(frozen=True)
class DecompositionIR:
    """Decomposition semantics for a metric (DEPRECATED: use Composition)."""

    kind: Literal["sum", "ratio"]
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
    filter: FilterIR | None = None  # tier-1 only: AND equality predicates
    unit_override: str | None = None
    weighted_mean: WeightedMeanAggregation | None = None

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
            tier1 = self.aggregation is not None or self.weighted_mean is not None
            has_target = self.aggregation_target is not None
            legacy_measure_target = self.measure is not None and not has_target
            if self.aggregation is not None and not (has_target or legacy_measure_target):
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: tier-1 metric requires an aggregation target"
                )
            if self.weighted_mean is not None and (has_target or self.measure is not None):
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: weighted mean must use its value/weight inputs"
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
            if (
                self.aggregation is not None
                or self.measure is not None
                or self.weighted_mean is not None
            ):
                raise ValueError(
                    f"MetricIR {self.semantic_id!r}: derived metric must not carry physical aggregation inputs"
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
