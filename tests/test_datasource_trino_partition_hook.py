"""Trino partition-value enumeration hook contracts.

The hook builds a query against the ``$partitions`` system table. Hive-connector
tables expose partition columns as top-level columns, but Iceberg tables nest
partition values under a ``partition`` row column — so ``SELECT log_date`` raises
``COLUMN_NOT_FOUND`` on Iceberg even though the column exists in ``SHOW COLUMNS``.
The hook must detect the ``$partitions`` schema and reference partition columns
through the ``partition`` row when the table is Iceberg. See issue #21.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from marivo.datasource.authoring import TrinoSpec
from marivo.datasource.engines.base import PartitionProbeRequest
from marivo.datasource.engines.trino import inspect_partition_values
from marivo.datasource.ir import DatasourceSourceLocation, TableSourceIR


@dataclass
class _Cursor:
    description: tuple[tuple[str, Any], ...]
    rows: tuple[tuple[Any, ...], ...]

    def fetchall(self) -> tuple[tuple[Any, ...], ...]:
        return self.rows


class _Backend:
    """Minimal backend that maps generated SQL to a scripted cursor."""

    def __init__(self, table_ref: str, cursors: dict[str, _Cursor]) -> None:
        self._table_ref = table_ref
        self._cursors = cursors
        self.calls: list[str] = []

    def raw_sql(self, sql: str) -> _Cursor:
        self.calls.append(sql)
        for needle, cursor in self._cursors.items():
            if needle in sql:
                return cursor
        raise AssertionError(f"unexpected trino raw_sql: {sql!r}")


def _request(
    backend: object,
    database: str,
    table: str,
    columns: tuple[str, ...],
    limit: int,
) -> PartitionProbeRequest:
    ir = TrinoSpec(name="trino_olap", host="trino.example", catalog="hive").to_ir(
        location=DatasourceSourceLocation(file="<test>", line=1)
    )
    return PartitionProbeRequest(
        backend=backend,  # type: ignore[arg-type]
        datasource_ir=ir,
        source=TableSourceIR(table=table, database=database),
        partition_columns=columns,
        limit=limit,
    )


def test_iceberg_partitions_table_nests_values_under_partition_row() -> None:
    columns = ("log_date", "log_hour")
    table_ref = '"hive"."iceberg_inf"."dwd_olap_trino_query_info_i_hr$partitions"'
    backend = _Backend(
        table_ref,
        {
            "SELECT *": _Cursor(
                description=(
                    ("partition", "row"),
                    ("record_count", "bigint"),
                    ("file_count", "bigint"),
                    ("file_size_in_bytes", "bigint"),
                ),
                rows=(),
            ),
            f"FROM {table_ref} ORDER BY": _Cursor(
                description=(("log_date", "varchar"), ("log_hour", "varchar")),
                rows=(("2026-07-16", "10"), ("2026-07-16", "09")),
            ),
        },
    )
    request = _request(backend, "iceberg_inf", "dwd_olap_trino_query_info_i_hr", columns, 101)

    result = inspect_partition_values(request)

    assert result.value_source == "metadata"
    assert result.rows == (
        {"log_date": "2026-07-16", "log_hour": "10"},
        {"log_date": "2026-07-16", "log_hour": "09"},
    )
    # The enumeration query must reach through the ``partition`` row column, not
    # reference the partition columns as top-level columns of ``$partitions``.
    enum_query = next(sql for sql in backend.calls if "ORDER BY" in sql)
    assert '"partition"."log_date"' in enum_query
    assert '"partition"."log_hour"' in enum_query
    # Returned columns are aliased back to the partition column names so the
    # downstream value extraction (row.get(field.name)) resolves them.
    assert 'AS "log_date"' in enum_query
    assert 'AS "log_hour"' in enum_query


def test_hive_partitions_table_exposes_partition_columns_top_level() -> None:
    columns = ("dt",)
    table_ref = '"hive"."warehouse"."orders$partitions"'
    backend = _Backend(
        table_ref,
        {
            "SELECT *": _Cursor(
                description=(("dt", "date"), ("record_count", "bigint")),
                rows=(),
            ),
            f"FROM {table_ref} ORDER BY": _Cursor(
                description=(("dt", "date"),),
                rows=(("2026-07-16",), ("2026-07-15",)),
            ),
        },
    )
    request = _request(backend, "warehouse", "orders", columns, 101)

    result = inspect_partition_values(request)

    assert result.value_source == "metadata"
    assert result.rows == ({"dt": "2026-07-16"}, {"dt": "2026-07-15"})
    enum_query = next(sql for sql in backend.calls if "ORDER BY" in sql)
    # Hive-connector $partitions exposes partition columns directly: the hook
    # must NOT route them through a ``partition`` row that does not exist here.
    assert '"partition"' not in enum_query
    assert '"dt"' in enum_query
