"""Trino SQL expressions and catalog namespace normalization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.schema as sch
import ibis.expr.types as ir
from ibis.backends import BaseBackend

from marivo.datasource.engines.query import KeyRows, zipped_key_arrays
from marivo.datasource.ir import DatasourceIR, TableSourceIR


def _trino_namespace(
    database: str | tuple[str, ...] | None,
    *,
    catalog: str,
    default_schema: str | None,
) -> tuple[str, str | None]:
    if isinstance(database, tuple):
        if len(database) >= 2:
            return str(database[0]), str(database[1])
        if len(database) == 1:
            return catalog, str(database[0])
        return catalog, None
    if database is not None:
        parts = str(database).split(".")
        if len(parts) == 2 and all(parts):
            return parts[0], parts[1]
        return catalog, str(database)
    return catalog, default_schema


def _trino_encode(value: str) -> bytes:
    raise NotImplementedError("native Trino to_utf8")


_TRINO_ENCODE: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="to_utf8", signature=(("string",), "binary")
)(_trino_encode)


def string_bytes(value: ir.Value) -> ir.Value:
    return _TRINO_ENCODE(value)


def inline_key_relation(schema: sch.Schema, records: KeyRows) -> ir.Table | None:
    if not schema or any(
        dtype.copy(nullable=True) not in (dt.int64, dt.string) for dtype in schema.types
    ):
        return None
    return zipped_key_arrays(schema, records)


class ReadColumns(Protocol):
    def __call__(
        self, backend: BaseBackend, statement_id: str, *, values: Mapping[str, object]
    ) -> tuple[dict[str, object], ...]: ...


def varchar_columns(
    backend: BaseBackend,
    datasource: DatasourceIR,
    source: TableSourceIR,
    read: ReadColumns,
) -> frozenset[str]:
    catalog, schema = _trino_namespace(
        source.database,
        catalog=str(datasource.fields["catalog"]),
        default_schema=str(datasource.fields.get("schema", "")),
    )
    rows = read(
        backend,
        "trino.columns",
        values={"catalog": catalog, "schema": schema, "table": source.table},
    )
    return frozenset(
        str(row.get("column_name"))
        for row in rows
        if str(row.get("data_type", "")).lower().startswith("varchar")
    )
