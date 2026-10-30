"""Typed finite calendar data and native elapsed-lattice expressions."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256

import ibis
import ibis.expr.datashape as ds
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.schema as sch
import ibis.expr.types as ir
import pyarrow as pa
import sqlglot as sg
import sqlglot.expressions as sge


class CalendarData(ops.PhysicalTable):  # type: ignore[misc]
    """A closed query literal, never an independently readable physical source."""

    schema: sch.Schema
    payload: str


class CalendarFloor(ops.Value):  # type: ignore[misc]
    """Floor an instant on an exact whole-second elapsed lattice."""

    arg: ops.Value
    origin: str
    width: int

    @property
    def dtype(self) -> dt.DataType:
        return dt.Timestamp(timezone="UTC", scale=6, nullable=self.arg.dtype.nullable)

    @property
    def shape(self) -> ds.DataShape:
        return self.arg.shape


def calendar_data(schema: sch.Schema, rows: tuple[tuple[object, ...], ...]) -> ir.Table:
    """Capture typed date/instant directory rows as one compact payload."""

    def encode(value: object) -> str | int | float | bool | None:
        if isinstance(value, datetime):
            return (
                value.astimezone(timezone.utc)
                .replace(tzinfo=None)
                .isoformat(sep=" ", timespec="microseconds")
            )
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise TypeError(f"Unsupported calendar literal: {type(value).__name__}")

    payload = json.dumps(
        [dict(zip(schema.names, (encode(value) for value in row), strict=True)) for row in rows],
        separators=(",", ":"),
    )
    name = "_marivo_calendar_" + sha256((str(schema) + payload).encode()).hexdigest()[:24]
    return CalendarData(name=name, schema=schema, payload=payload).to_expr()


def calendar_arrow(node: CalendarData) -> pa.Table:
    """Decode the owned literal without precision-losing timestamp conversions."""
    values = json.loads(node.payload)
    arrays = []
    schema = node.schema.to_pyarrow()
    for field in schema:
        items = [row[field.name] for row in values]
        array = pa.array(
            items,
            type=pa.string()
            if pa.types.is_temporal(field.type) or pa.types.is_decimal(field.type)
            else field.type,
        )
        arrays.append(
            array.cast(pa.timestamp(field.type.unit)).cast(field.type)
            if pa.types.is_timestamp(field.type)
            else array.cast(field.type)
        )
    return pa.Table.from_arrays(arrays, schema=schema)


def memory_calendar(node: CalendarData) -> ir.Table:
    """Give a captured directory a deterministic invocation-local relation name."""
    data = calendar_arrow(node)
    names = node.schema.names
    physical = (
        names
        if all(name.isidentifier() for name in names)
        else tuple(f"_calendar_field_{i}" for i in range(len(names)))
    )
    table = ibis.memtable(data.rename_columns(physical)).op().copy(name=node.name).to_expr()
    # ClickHouse's external-data structure writer does not quote field names.
    # Keep logical slots such as key.0 in a projection over safe physical names.
    return (
        table
        if physical == names
        else table.select(
            **{name: table[field] for name, field in zip(names, physical, strict=True)}
        )
    )


def compile_calendar_data(node: CalendarData, provider: str) -> sge.Expr:
    """Expand one JSON parameter into a relation, without per-row SQL syntax."""
    payload = sge.Literal.string(node.payload).sql(dialect=provider)
    names = [
        sge.to_identifier(name, quoted=True).sql(dialect=provider) for name in node.schema.names
    ]
    types = []
    for dtype in node.schema.types:
        if dtype.is_timestamp():
            types.append(
                {"postgres": "TIMESTAMPTZ", "mysql": "DATETIME(6)", "trino": "TIMESTAMP(6)"}[
                    provider
                ]
            )
        elif dtype.is_date():
            types.append("DATE")
        elif dtype.is_integer():
            types.append("BIGINT")
        elif dtype.is_boolean():
            types.append("BOOLEAN")
        elif isinstance(dtype, dt.Decimal):
            types.append(f"DECIMAL({dtype.precision},{dtype.scale})")
        elif dtype.is_floating():
            types.append("DOUBLE PRECISION")
        else:
            types.append({"postgres": "TEXT", "mysql": "LONGTEXT", "trino": "VARCHAR"}[provider])
    if provider == "postgres":
        columns = ", ".join(f"{name} {dtype}" for name, dtype in zip(names, types, strict=True))
        query = f"SELECT * FROM jsonb_to_recordset(CAST({payload} AS JSONB)) AS calendar_data({columns})"
    elif provider == "mysql":
        columns = ", ".join(
            f"{name} {dtype} PATH "
            + sge.Literal.string("$." + json.dumps(raw)).sql(dialect=provider)
            for name, raw, dtype in zip(names, node.schema.names, types, strict=True)
        )
        # A derived LIMIT prevents MySQL/MariaDB from merging this constant
        # table function into a per-fact nested loop. Materialization permits
        # an indexed equality lookup; the maximum unsigned limit removes no rows.
        query = f"SELECT * FROM JSON_TABLE({payload}, '$[*]' COLUMNS ({columns})) AS calendar_data LIMIT 18446744073709551615"
    else:
        # JSON carries ISO UTC civil strings. Attach UTC explicitly after casting.
        selections = []
        for name, raw, dtype, original in zip(
            names, node.schema.names, types, node.schema.types, strict=True
        ):
            path = sge.Literal.string("$." + json.dumps(raw)).sql(dialect=provider)
            value = f"CAST(json_extract_scalar(item, {path}) AS {dtype})"
            if original.is_timestamp():
                value = f"with_timezone({value}, 'UTC')"
            selections.append(f"{value} AS {name}")
        query = f"SELECT {', '.join(selections)} FROM UNNEST(CAST(json_parse({payload}) AS ARRAY(JSON))) AS calendar_data(item)"
    return sg.parse_one(query, read=provider)


def compile_calendar_floor(node: CalendarFloor, arg: sge.Expr, provider: str) -> sge.Expr:
    """Native whole-second arithmetic; timestamps never pass through floats."""
    literal = sge.Literal.string(node.origin).sql(dialect=provider)
    origin = {
        "duckdb": f"CAST({literal} AS TIMESTAMPTZ)",
        "postgres": f"CAST({literal} AS TIMESTAMPTZ)",
        "mysql": f"CAST({literal} AS DATETIME(6))",
        "trino": f"with_timezone(CAST({literal} AS TIMESTAMP(6)), 'UTC')",
        "clickhouse": f"toDateTime64({literal}, 6, 'UTC')",
    }[provider]
    delta = {
        "duckdb": f"date_diff('second', {origin}, date_trunc('second', __calendar_time))",
        "postgres": f"EXTRACT(EPOCH FROM (date_trunc('second', __calendar_time) - {origin}))",
        "mysql": f"TIMESTAMPDIFF(SECOND, {origin}, CAST(DATE_FORMAT(__calendar_time, '%Y-%m-%d %H:%i:%s') AS DATETIME))",
        "trino": f"date_diff('second', {origin}, date_trunc('second', __calendar_time))",
        "clickhouse": f"dateDiff('second', {origin}, dateTrunc('second', __calendar_time))",
    }[provider]
    offset = f"FLOOR(({delta}) / {node.width}) * {node.width}"
    expression = {
        "duckdb": f"{origin} + CAST({offset} AS BIGINT) * INTERVAL '1 second'",
        "postgres": f"{origin} + ({offset}) * INTERVAL '1 second'",
        "mysql": f"TIMESTAMPADD(SECOND, CAST({offset} AS SIGNED), {origin})",
        "trino": f"date_add('second', CAST({offset} AS BIGINT), {origin})",
        "clickhouse": f"addSeconds({origin}, toInt64({offset}))",
    }[provider]
    return sg.parse_one(expression, read=provider).transform(
        lambda item: (
            arg.copy() if isinstance(item, sge.Column) and item.name == "__calendar_time" else item
        )
    )
