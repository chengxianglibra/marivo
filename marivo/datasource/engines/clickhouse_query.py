"""ClickHouse expression contracts for governed Query reads."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.schema as sch
import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import sqlglot
from ibis.backends import BaseBackend
from sqlglot import expressions as sge

from marivo._kernel.numeric_types import native_unsigned_aggregates
from marivo._kernel.timezones import normalize_timezone
from marivo.datasource.arrow_types import native_pandas_type
from marivo.datasource.engines.query import KeyRows
from marivo.datasource.errors import DatasourceSourceCapabilityError, repair
from marivo.datasource.temporal_primitives import localize, timestamp

_UTC_CLOCK = dt.Timestamp(timezone="UTC", scale=6)


def _zone_signature(value: datetime, zone: str) -> datetime:
    raise NotImplementedError("native timezone interpretation")


clickhouse_zone: Callable[[ir.TimestampValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _zone_signature,
    name="toTimeZone",
    signature=(
        (dt.Timestamp(timezone="UTC", scale=6), dt.string),
        dt.Timestamp(timezone="UTC", scale=6),
    ),
)


def _clickhouse_date32_literal(value: str) -> date:
    """Return a ClickHouse Date32 literal without Date's 1970-2149 bounds."""
    raise NotImplementedError("native ClickHouse Date32 literal")


_clickhouse_date32 = ibis.udf.scalar.builtin(
    _clickhouse_date32_literal,
    name="toDate32",
    signature=((dt.string,), dt.date),
)


def _row_number_signature() -> int:
    raise NotImplementedError("native one-based row number")


_clickhouse_row_number: Callable[[], ir.IntegerScalar] = ibis.udf.agg.builtin(
    _row_number_signature, name="row_number", signature=((), dt.int64)
)


def _native_signature(day: date, precision: int, zone: str) -> datetime:
    raise NotImplementedError("native ClickHouse DATE conversion")


_native_start: Callable[[ir.DateValue, int, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _native_signature,
    name="toDateTime64",
    signature=((dt.date, dt.int64, dt.string), _UTC_CLOCK),
)


def date_literal(day: date, dtype: dt.DataType) -> ir.Value:
    if date(1970, 1, 1) <= day <= date(2149, 6, 6):
        return ibis.literal(day, type=dtype)
    return _clickhouse_date32(day.isoformat())


def utc_day(day: ir.DateValue, zone: str) -> ir.TimestampValue:
    normalized = normalize_timezone(zone)
    if normalized.kind == "fixed_offset":
        return localize(zone, timestamp(day))
    return clickhouse_zone(_native_start(day, 6, normalized.name), "UTC")


def row_number() -> tuple[ir.IntegerValue, int]:
    return _clickhouse_row_number(), 1


def inline_key_relation(schema: sch.Schema, records: KeyRows) -> ir.Table | None:
    if not schema or any(
        dtype.copy(nullable=True) not in (dt.int64, dt.string) for dtype in schema.types
    ):
        return None
    fields = {f"field_{index}": dtype for index, dtype in enumerate(schema.types)}
    array_type = dt.Array(dt.Struct(fields))
    literal = ibis.literal(
        tuple(dict(zip(fields, row, strict=True)) for row in records), type=array_type
    )
    # Empty arrays need explicit tuple types to avoid inferring Nothing.
    literal = ops.Cast(literal, to=array_type).to_expr()
    expanded = literal.name("__keys").as_table().unnest("__keys")
    return expanded.select(
        **{
            name: expanded["__keys"][field]
            for field, name in zip(fields, schema.names, strict=True)
        }
    )


def lower_query_numeric(expression: ir.Table) -> ir.Table:
    exact_nodes: dict[ops.Node, ops.Node] = {}
    for node in expression.op().find((ops.IsNan, ops.IsInf)):
        quotient = node.arg
        while isinstance(quotient, ops.Field) and isinstance(
            quotient.rel, (ops.Project, ops.Aggregate)
        ):
            quotient = quotient.rel.values[quotient.name]
        if (
            isinstance(quotient, ops.Divide)
            and (quotient.left.dtype.is_decimal() or quotient.right.dtype.is_decimal())
            and not (quotient.left.dtype.is_floating() or quotient.right.dtype.is_floating())
        ):
            # ClickHouse Decimal division stays Decimal although Ibis infers
            # float64. Decimal has no NaN/infinity; native overflow still
            # fails the statement, and zero/error checks remain separate.
            exact_nodes[node] = ibis.literal(False).op()
    rewritten = expression.op().replace(exact_nodes).to_expr()
    assert isinstance(rewritten, ir.Table)
    return native_unsigned_aggregates(rewritten)


def read_issued_query(
    backend: BaseBackend,
    sql: str,
    table: ir.Table,
    schema: pa.Schema,
    mark_submitted: Callable[[], None],
) -> pd.DataFrame:
    # Ibis' pandas reader rounds nullable Int64 through float64;
    # its Arrow reader also rejects ClickHouse's UInt8 booleans.
    # Decode the same issued SQL from native Python columns first.
    external = backend._collect_in_memory_tables(table, {})
    external_data = backend._normalize_external_tables(external)
    batches: list[pa.RecordBatch] = []
    mark_submitted()
    with backend.con.query_column_block_stream(sql, external_data=external_data) as blocks:
        for block in blocks:
            arrays: list[pa.Array] = []
            for field, values in zip(schema, block, strict=True):
                if pa.types.is_boolean(field.type):
                    values = [None if value is None else bool(value) for value in values]
                arrays.append(
                    pa.array(
                        values,
                        type=field.type,
                        from_pandas=not pa.types.is_floating(field.type),
                    )
                )
            batches.append(pa.RecordBatch.from_arrays(arrays, schema=schema))
    result: pd.DataFrame = pa.Table.from_batches(batches, schema=schema).to_pandas(
        types_mapper=native_pandas_type
    )
    return result


def validate_temporal_parsers(backend: BaseBackend, sql: str) -> None:
    """Qualify explicit-format parsers remaining in the final executable SQL."""
    if "parsedatetimeornull" not in sql.lower():
        return
    version = backend.version
    if tuple(int(part) for part in version.split(".")[:2]) >= (23, 3):
        return
    for function in sqlglot.parse_one(sql, read="clickhouse").find_all(sge.Anonymous):
        if function.name.lower() != "parsedatetimeornull":
            continue
        fields = ", ".join(sorted({column.name for column in function.find_all(sge.Column)}))
        argument = function.expressions[1]
        format = argument.this if isinstance(argument, sge.Literal) else "<expression>"
        raise DatasourceSourceCapabilityError(
            message="The selected ClickHouse server cannot parse this explicit time format.",
            expected="ClickHouse 23.3 or newer for nonordered ms.strptime formats",
            received=f"ClickHouse {version}; fields={fields or '<scalar>'}; format={format[:200]}",
            location="datasource.clickhouse.strptime",
            repair=repair(
                kind="configure",
                canonical_id="clickhouse",
                action="Use a canonical year/month/day encoding, expose a native Date32/DateTime64 column, or upgrade ClickHouse to 23.3 or newer.",
            ),
        )
