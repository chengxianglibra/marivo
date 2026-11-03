"""Typed finite SQL parameters and native output-position ranges."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256

import ibis
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


class CalendarRange(ops.PhysicalTable):  # type: ignore[misc]
    """An invocation's finite output positions, never a source membership table."""

    schema: sch.Schema
    count: int


def calendar_range(count: int) -> ir.Table:
    return CalendarRange(
        name="__calendar_positions", schema=ibis.schema({"position": "!int64"}), count=count
    ).to_expr()


def compile_calendar_range(node: CalendarRange, provider: str) -> sge.Expr:
    count = node.count
    if provider == "clickhouse":
        sql = f"SELECT toInt64(arrayJoin(range({count}))) AS position"
    elif provider == "duckdb":
        sql = f"SELECT CAST(range AS BIGINT) AS position FROM range({count})"
    elif provider == "postgres":
        sql = f"SELECT CAST(generate_series AS BIGINT) AS position FROM generate_series(0, {count - 1})"
    elif provider == "trino":
        sql = f"SELECT CAST(position AS BIGINT) AS position FROM UNNEST(sequence(0, {count - 1})) AS positions(position)"
        if not count:
            sql = "SELECT CAST(0 AS BIGINT) AS position WHERE FALSE"
    else:
        # MySQL has no native sequence table. A fixed digit product keeps the
        # statement bounded; it is solely an output-index evaluation.
        digits = max(1, len(str(max(0, count - 1))))
        terms = " + ".join(f"d{i}.digit * {10**i}" for i in range(digits))
        tables = " CROSS JOIN ".join(
            f"JSON_TABLE('[0,1,2,3,4,5,6,7,8,9]', '$[*]' COLUMNS(digit BIGINT PATH '$')) AS d{i}"
            for i in range(digits)
        )
        sql = f"SELECT {terms} AS position FROM {tables} WHERE {terms} < {count}"
    return sg.parse_one(sql, read=provider)


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
    # Keep logical slots such as key.0 in a projection over physical names.
    return (
        table
        if physical == names
        else table.select(
            **{name: table[field] for name, field in zip(names, physical, strict=True)}
        )
    )


def compile_calendar_data(node: CalendarData, provider: str) -> sge.Expr:
    """Expand one JSON parameter into a relation, without per-row SQL syntax."""
    if provider == "clickhouse":
        return _clickhouse_calendar_data(node)
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


def _clickhouse_calendar_data(node: CalendarData) -> sge.Expr:
    """Decode finite keys in SQL, including on ClickHouse 22.3 HTTP readers.

    JSONExtract does not decode Date or DateTime on that release. Wire values
    use strings for dates/decimals/floats and integer microseconds for instants.
    Tuple positions also avoid imposing identifier rules on logical slot names.
    """
    from ibis.backends.sql.datatypes import ClickHouseType

    rows = json.loads(node.payload)
    wire_rows: list[list[object]] = [[] for _ in rows]
    wire_types: list[str] = []
    selections: list[str] = []
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    for index, (name, dtype) in enumerate(node.schema.items()):
        logical_type = ClickHouseType.from_ibis(dtype).sql(dialect="clickhouse")
        if dtype.is_date():
            # Finite analysis keys use Arrow date32. Do not narrow them through
            # ClickHouse's unsigned Date carrier before restoring that type.
            logical_type = "Nullable(Date32)" if dtype.nullable else "Date32"
        value = f"tupleElement(__calendar_row, {index + 1})"
        wire_type = (
            "Int64"
            if dtype.is_timestamp()
            else "String"
            if dtype.is_date() or dtype.is_decimal() or dtype.is_floating() or dtype.is_string()
            else "UInt8"
            if dtype.is_boolean()
            else ClickHouseType.from_ibis(dtype.copy(nullable=False)).sql(dialect="clickhouse")
        )
        wire_types.append(f"Nullable({wire_type})" if dtype.nullable else wire_type)
        for row, wire in zip(rows, wire_rows, strict=True):
            item = row[name]
            if item is None:
                if not dtype.nullable:
                    raise TypeError(f"Nonnullable calendar field {name!r} received NULL")
            elif dtype.is_timestamp():
                point = datetime.fromisoformat(item).replace(tzinfo=timezone.utc)
                delta = point - epoch
                item = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
            elif dtype.is_boolean():
                item = int(item)
            elif dtype.is_floating():
                item = repr(item)
            wire.append(item)
        if dtype.is_timestamp():
            # 22.3 rejects Nullable(Int64) inside fromUnixTimestamp64Micro.
            instant = f"fromUnixTimestamp64Micro(ifNull({value}, 0), 'UTC')"
            value = f"if(isNull({value}), NULL, {instant})" if dtype.nullable else instant
        quoted = sge.to_identifier(name, quoted=True).sql(dialect="clickhouse")
        selections.append(f"CAST({value} AS {logical_type}) AS {quoted}")
    payload = sge.Literal.string(json.dumps(wire_rows, separators=(",", ":"), allow_nan=False)).sql(
        dialect="clickhouse"
    )
    wire_schema = sge.Literal.string(f"Array(Tuple({', '.join(wire_types)}))").sql(
        dialect="clickhouse"
    )
    query = (
        f"SELECT {', '.join(selections)} FROM "
        f"(SELECT arrayJoin(JSONExtract({payload}, {wire_schema})) AS __calendar_row)"
    )
    return sg.parse_one(query, read="clickhouse")
