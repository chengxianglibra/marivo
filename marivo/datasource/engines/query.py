"""Immutable compiler hooks and admission policies for governed SQL reads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol, TypeAlias

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.schema as sch
import ibis.expr.types as ir
from ibis.backends import BaseBackend

from marivo.datasource.engines.aggregate_policy import NativeAggregatePolicy
from marivo.datasource.ir import DatasourceIR, TableSourceIR
from marivo.datasource.temporal_primitives import localize, timestamp

KeyRows: TypeAlias = tuple[tuple[object, ...], ...]
ReadStringColumns: TypeAlias = Callable[[BaseBackend, DatasourceIR, TableSourceIR], frozenset[str]]
ReadStringColumn: TypeAlias = Callable[[BaseBackend, DatasourceIR, TableSourceIR, str], bool]
BindKeyMembership: TypeAlias = Callable[
    [sch.Schema, KeyRows, Mapping[str, ir.Value], Callable[[], int] | None],
    ir.BooleanValue | None,
]


class ReadOriginalInput(Protocol):
    def __call__(self, backend: BaseBackend, table: ir.Table, /, *, file_source: bool) -> bool: ...


def _identity(value: ir.Value) -> ir.Value:
    return value


def _identity_table(table: ir.Table) -> ir.Table:
    return table


def _date_literal(day: date, dtype: dt.DataType) -> ir.Value:
    return ibis.literal(day, type=dtype)


def _finite_clock_signature(value: datetime) -> bool:
    raise NotImplementedError("native timestamp finiteness")


finite_calendar_clock: Callable[[ir.TimestampValue], ir.BooleanValue] = ibis.udf.scalar.builtin(
    _finite_clock_signature, name="isfinite", signature=((dt.timestamp,), dt.boolean)
)


def _row_number() -> tuple[ir.IntegerValue, int]:
    return ibis.row_number(), 0


def _utc_day(value: ir.DateValue, zone: str) -> ir.TimestampValue:
    return localize(zone, timestamp(value))


def floating_order_keys(
    value: ir.Value, descending: bool, *, has_native_nan: bool = True
) -> tuple[ir.Value, ...]:
    """Keep numbers, valid NaNs and SQL NULL in that order in either direction."""
    if not isinstance(value, ir.FloatingValue):
        raise TypeError("Floating order keys require a floating-point expression")
    missing = value.isnull()
    if not has_native_nan:
        return missing.ifelse(1, 0), value.desc() if descending else value.asc()
    is_nan = value.isnan()
    category = missing.ifelse(2, is_nan.ifelse(1, 0))
    # The caller's complete key breaks ties among NaNs and among NULLs.
    numeric = (missing | is_nan).ifelse(ibis.literal(0, type=value.type()), value)
    return category, numeric.desc() if descending else numeric.asc()


def nullable_join_value(value: ir.Value) -> ir.Value:
    """Keep an absent outer-join payload null on servers with non-null defaults."""
    result = ops.Cast(value, to=value.type().copy(nullable=True)).to_expr()
    assert isinstance(result, ir.Value)
    return result


def zipped_key_arrays(schema: sch.Schema, records: KeyRows) -> ir.Table:
    """Unnest equal-length source parameters by position to retain complete tuples."""
    arrays = (
        ibis.literal(True)
        .as_table()
        .select(
            **{
                f"__keys_{index}": ibis.literal(
                    tuple(row[index] for row in records), type=dt.Array(dtype)
                )
                for index, dtype in enumerate(schema.types)
            }
        )
    )
    expanded = arrays.unnest("__keys_0", offset="__keys_position")
    return expanded.select(
        **{
            name: expanded["__keys_0"]
            if index == 0
            else expanded[f"__keys_{index}"][expanded["__keys_position"]]
            for index, name in enumerate(schema.names)
        }
    )


@dataclass(frozen=True)
class OriginalStringPolicy:
    """Physical qualification of strings used by original source recomputation."""

    column: ReadStringColumn | None = None
    columns: ReadStringColumns | None = None
    unavailable: str = "Remote original strings require visible deterministic collation metadata"
    unsupported: str = "Remote original strings retain a nondeterministic collation boundary"


@dataclass(frozen=True)
class EngineQueryPolicy:
    """Backend admission for native Query, fusion and parameter routes."""

    key_fusion: Literal["none", "local", "key_counts", "collated", "numeric"] = "none"
    sample_fusion: bool = False
    owns_arrow_key_relations: bool = False
    semi_join_membership: bool = False
    inline_key_relation: Callable[[sch.Schema, KeyRows], ir.Table | None] | None = None
    bind_key_membership: BindKeyMembership | None = None
    query_packet_limit: Callable[[BaseBackend], tuple[int, str]] | None = None
    accepts_nonfinite_inline_time_keys: bool = False
    supports_file_parameters: bool = False
    requires_reducer_admission: bool = True

    @property
    def supports_fused_arithmetic(self) -> bool:
        return self.key_fusion in ("local", "numeric")

    @property
    def uses_identity_alignment_domain(self) -> bool:
        return self.key_fusion == "numeric"

    @property
    def needs_remote_carrier_qualification(self) -> bool:
        return self.key_fusion in ("collated", "numeric")


@dataclass(frozen=True)
class QueryDialectProfile:
    """Native expression contracts and physical facts exposed through Datasource."""

    aggregates: NativeAggregatePolicy = field(default_factory=NativeAggregatePolicy)
    string_bytes: Callable[[ir.Value], ir.Value] = _identity
    string_without_collation: Callable[[ir.Value], ir.Value] = _identity
    returned_string: Callable[[ir.Value], ir.Value] | None = None
    source_membership_uses_returned_strings: bool = False
    string_equality: Callable[[ops.Equals], ops.Value] | None = None
    original_input: ReadOriginalInput | None = None
    original_strings: OriginalStringPolicy | None = None
    query_string_columns: ReadStringColumns | None = None
    original_string_column: ReadStringColumn | None = None
    date_literal: Callable[[date, dt.DataType], ir.Value] = _date_literal
    utc_day: Callable[[ir.DateValue, str], ir.TimestampValue] = _utc_day
    row_number: Callable[[], tuple[ir.IntegerValue, int]] = _row_number
    join_value: Callable[[ir.Value], ir.Value] | None = None
    lower_query_numeric: Callable[[ir.Table], ir.Table] = _identity_table
    source_timezones: Callable[[str], tuple[str, ...]] | None = None
    timestamp_has_implicit_timezone: bool = False
    civil_date_needs_neutral_carrier: bool = False
    calendar_clock_needs_utc_carrier: bool = False
    preserve_utc_conversions: bool = False
    timezone_endpoint_years: tuple[int, int] = (1, 9999)
    native_subday_grains: frozenset[str] = frozenset()
    finite_calendar_clock: Callable[[ir.TimestampValue], ir.BooleanValue] | None = None
    preview_requires_terminal_scope: bool = False
    supports_per_row_attribute_time: bool = False
    supports_native_frame_top: bool = False
    conditional_aggregate_style: Literal["none", "filter_where", "case_when", "clickhouse_if"] = (
        "none"
    )
    supports_sql_window_frames: bool = False
    stored_date_can_be_incomplete: bool = False
    # SUM on nullable inputs returns NULL with no valid value in the frame.
    window_frame_nulls_safe: bool = False
    supports_grouping_sets: bool = False
    # Exact integer SUM can use a sufficiently wide native accumulator even
    # when the declared result remains int64.
    grouping_sets_wide_integer_sum_bytes: frozenset[int] = frozenset()
    grouping_sets_decimal_accumulator_precision: int = 0
    float_order_keys: Callable[[ir.Value, bool], tuple[ir.Value, ...]] | None = None
