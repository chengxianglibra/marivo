"""Exact physical keys and declarations, independent of method business semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, get_args

import pyarrow as pa

from marivo.analysis.core.model import CheckId, DomainKind, PartRole
from marivo.analysis.methods.errors import reject
from marivo.analysis.methods.semantics import MethodKey

Backend: TypeAlias = Literal["duckdb", "postgres", "mysql", "sqlite", "trino", "clickhouse"]
ScalarName: TypeAlias = Literal["boolean", "string", "int64", "float64", "date", "timestamp"]
Route: TypeAlias = Literal["ibis", "ibis_python", "artifact_python"]


@dataclass(frozen=True, slots=True)
class ScalarType:
    name: ScalarName

    def __post_init__(self) -> None:
        if self.name not in get_args(ScalarName):
            reject(
                "an exact scalar type", str(self.name), "Declare a supported scalar or DecimalType."
            )


@dataclass(frozen=True, slots=True)
class DecimalType:
    precision: int
    scale: int

    def __post_init__(self) -> None:
        if (
            type(self.precision) is not int
            or type(self.scale) is not int
            or not 0 <= self.scale <= self.precision
            or not 1 <= self.precision <= 38
        ):
            reject(
                "positive Decimal precision and scale within precision",
                repr(self),
                "Bind exact decimal metadata.",
            )

    @property
    def name(self) -> str:
        return f"decimal({self.precision},{self.scale})"


@dataclass(frozen=True, slots=True)
class DurationType:
    """Fixed elapsed ticks, never a calendar interval or a floating value."""

    unit: Literal["s", "ms", "us", "ns"]

    def __post_init__(self) -> None:
        if self.unit not in ("s", "ms", "us", "ns"):
            reject(
                "fixed duration unit s/ms/us/ns",
                repr(self.unit),
                "Preserve the exact elapsed tick unit.",
            )

    @property
    def name(self) -> str:
        return f"interval('{self.unit}')"


ValueType: TypeAlias = ScalarType | DecimalType | DurationType


@dataclass(frozen=True, slots=True)
class NoTime:
    """No temporal axis participates in this physical shape."""


@dataclass(frozen=True, slots=True)
class TimeShape:
    kind: Literal["instant", "elapsed", "calendar"]
    unit: Literal["s", "ms", "us", "ns", "day", "week", "month", "year"]
    timezone: str

    def __post_init__(self) -> None:
        if (
            self.kind not in ("instant", "elapsed", "calendar")
            or self.unit not in ("s", "ms", "us", "ns", "day", "week", "month", "year")
            or type(self.timezone) is not str
            or not self.timezone
            or (self.kind in ("instant", "elapsed") and self.unit not in ("s", "ms", "us", "ns"))
            or (self.kind == "calendar" and self.unit not in ("day", "week", "month", "year"))
        ):
            reject(
                "a precise time kind, unit and zone",
                repr(self),
                "Preserve the source time shape; do not coerce calendar to elapsed time.",
            )


@dataclass(frozen=True, slots=True)
class SourceShape:
    backend: Backend
    form: Literal["table", "parquet", "csv", "json"]
    table_kind: str
    time: NoTime | TimeShape

    def __post_init__(self) -> None:
        if (
            self.backend not in get_args(Backend)
            or self.form not in ("table", "parquet", "csv", "json")
            or type(self.table_kind) is not str
            or not self.table_kind
            or type(self.time) not in (NoTime, TimeShape)
        ):
            reject(
                "an exact backend, source form and physical table kind",
                repr(self),
                "Bind the adapter's precise shape; wildcard qualifications are not supported.",
            )


@dataclass(frozen=True, slots=True)
class FixedShape:
    time: NoTime | TimeShape

    def __post_init__(self) -> None:
        if type(self.time) not in (NoTime, TimeShape):
            reject(
                "a fixed Artifact time shape", repr(self.time), "Preserve retained time metadata."
            )


@dataclass(frozen=True, slots=True)
class QualificationKey:
    method: MethodKey
    input_types: tuple[ValueType, ...]
    input_domains: tuple[DomainKind, ...]
    shape: SourceShape | FixedShape
    route: Route

    def __post_init__(self) -> None:
        if (
            type(self.method) is not MethodKey
            or type(self.input_types) is not tuple
            or not self.input_types
            or any(
                type(item) not in (ScalarType, DecimalType, DurationType)
                for item in self.input_types
            )
            or type(self.input_domains) is not tuple
            or not self.input_domains
            or any(item not in get_args(DomainKind) for item in self.input_domains)
            or len(self.input_types) != len(self.input_domains)
            or type(self.shape) not in (SourceShape, FixedShape)
            or self.route not in get_args(Route)
            or (type(self.shape) is FixedShape) != (self.route == "artifact_python")
        ):
            reject(
                "a complete exact method/type/domain/shape/route key",
                repr(self),
                "Use Ibis source routes or fixed Artifact Python; never import Artifacts into DuckDB.",
            )


@dataclass(frozen=True, slots=True)
class ResourceRequirements:
    batch: Literal["stream", "complete"]
    owner: Literal["producer", "caller"]
    max_rows: int | None

    def __post_init__(self) -> None:
        if (
            self.batch not in ("stream", "complete")
            or self.owner not in ("producer", "caller")
            or (self.max_rows is not None and (type(self.max_rows) is not int or self.max_rows < 1))
        ):
            reject(
                "explicit batch, owner and positive optional row limit",
                repr(self),
                "Declare resource constraints before execution.",
            )


@dataclass(frozen=True, slots=True)
class Qualified:
    """Static qualification evidence; never proof of this invocation's obligations."""

    implementation_id: str
    consumer_id: str
    evidence_id: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.implementation_id, self.consumer_id, self.evidence_id)
        ):
            reject(
                "implementation, consumer and qualification evidence",
                repr(self),
                "Connect and qualify a real consumer before selecting this route.",
            )


@dataclass(frozen=True, slots=True)
class Unavailable:
    status: Literal["unsupported", "unverified", "blocked"]
    reason: str
    recovery: str

    def __post_init__(self) -> None:
        if self.status not in ("unsupported", "unverified", "blocked") or any(
            type(value) is not str or not value for value in (self.reason, self.recovery)
        ):
            reject(
                "a named unavailable status, reason and recovery",
                repr(self),
                "Record the exact qualification gap.",
            )


@dataclass(frozen=True, slots=True)
class Implementation:
    key: QualificationKey
    checks: tuple[CheckId, ...]
    parts: tuple[PartRole, ...]
    precision: Literal[
        "exact", "checked_int64", "finite_float64", "certified_statistical", "native_numeric"
    ]
    resources: ResourceRequirements
    qualification: Qualified | Unavailable
    contract_version: int = 1
    numeric_specialization: Literal["consumer", "exact"] = "consumer"

    def __post_init__(self) -> None:
        if (
            type(self.key) is not QualificationKey
            or type(self.checks) is not tuple
            or any(item not in get_args(CheckId) for item in self.checks)
            or len(set(self.checks)) != len(self.checks)
            or type(self.parts) is not tuple
            or any(item not in get_args(PartRole) for item in self.parts)
            or len(set(self.parts)) != len(self.parts)
            or self.precision
            not in (
                "exact",
                "checked_int64",
                "finite_float64",
                "certified_statistical",
                "native_numeric",
            )
            or type(self.resources) is not ResourceRequirements
            or type(self.qualification) not in (Qualified, Unavailable)
            or type(self.contract_version) is not int
            or self.contract_version < 1
            or self.numeric_specialization not in ("consumer", "exact")
        ):
            reject(
                "complete immutable implementation obligations and qualification",
                repr(self),
                "Declare exact checks, parts, precision, resources and evidence.",
            )
        if self.precision == "native_numeric" and self.key.method.name not in (
            "metric.mean",
            "metric.weighted_mean",
            "metric.ratio",
            "metric.linear",
            "state_rollup.mean",
            "state_rollup.weighted_mean",
            "state_rollup.ratio",
            "state_rollup.linear",
        ):
            reject(
                "an ordinary native numeric method",
                self.key.method.name,
                "Retain this method's independent precision contract.",
            )
        if self.precision == "certified_statistical" and self.key.method.name not in (
            "deviation.zscore",
            "deviation.mad",
            "association.pearson",
            "association.spearman",
            "association.kendall",
            "forecast.naive",
            "forecast.drift",
            "forecast.seasonal_naive",
        ):
            reject(
                "a connected statistical numeric certificate",
                self.key.method.name,
                "Use the existing precision contract for this method.",
            )
        if self.key.route == "artifact_python" and self.resources.owner != "caller":
            reject(
                "caller-owned fixed input resources",
                repr(self.resources),
                "Keep fixed input execution in the caller.",
            )
        if self.key.route != "artifact_python" and self.resources.owner != "producer":
            reject(
                "producer-owned source resources",
                repr(self.resources),
                "Keep source preparation and resources with the producer.",
            )
        if (
            any(type(item) is DecimalType for item in self.key.input_types)
            and self.key.method.name not in ("row.count", "row.count_defined")
            and self.precision not in ("exact", "native_numeric")
            and not (
                self.precision == "certified_statistical"
                and self.key.method.name
                in (
                    "deviation.zscore",
                    "deviation.mad",
                    "association.pearson",
                    "association.spearman",
                    "association.kendall",
                    "forecast.naive",
                    "forecast.drift",
                    "forecast.seasonal_naive",
                    "forecast.read",
                    "association.read",
                )
            )
        ):
            reject(
                "exact Decimal precision",
                self.precision,
                "Qualify the exact Decimal shape without float conversion.",
            )


def arrow_scalar_type(scalar: ValueType) -> pa.DataType:
    """Render a registered scalar's exact default Arrow carrier."""
    if isinstance(scalar, DecimalType):
        return pa.decimal128(scalar.precision, scalar.scale)
    if isinstance(scalar, DurationType):
        return pa.duration(scalar.unit)
    if scalar.name == "date":
        return pa.date32()
    if scalar.name == "timestamp":
        return pa.timestamp("us")
    return pa.type_for_alias(scalar.name)


def matches_arrow_scalar(dtype: object, scalar: ValueType) -> bool:
    """Check the scalar family; exact temporal metadata is frozen in the receipt schema."""
    import pyarrow as pa

    if not isinstance(dtype, pa.DataType):
        return False
    if isinstance(scalar, DecimalType):
        return bool(dtype == pa.decimal128(scalar.precision, scalar.scale))
    if isinstance(scalar, DurationType):
        return bool(dtype == pa.duration(scalar.unit))
    if scalar.name == "timestamp":
        return bool(pa.types.is_timestamp(dtype))
    if scalar.name == "date":
        return bool(pa.types.is_date(dtype))
    return bool(dtype == pa.type_for_alias(scalar.name))
