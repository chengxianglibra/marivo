"""Trino partition metadata projection stays an Ibis expression."""

from __future__ import annotations

import ibis

from marivo.datasource.engines.trino import _partition_projection


def test_iceberg_partition_fields_are_projected_from_nested_row() -> None:
    relation = ibis.table(
        {"partition": "struct<log_date: date, log_hour: string>", "record_count": "int64"},
        name="iceberg_partitions",
    )
    expression = _partition_projection(relation, ("log_date", "log_hour"), "desc", 101)

    assert expression.columns == ("log_date", "log_hour")
    assert str(expression.schema()["log_date"]) == "date"
    sql = ibis.duckdb.connect().compile(expression, limit=None)
    assert '"partition"."log_date"' in sql
    assert '"partition"."log_hour"' in sql
    assert "DESC" in sql
    assert "LIMIT 101" in sql


def test_non_iceberg_partition_columns_and_order_are_preserved() -> None:
    relation = ibis.table({"dt": "date", "hour": "string"}, name="hive_partitions")
    expression = _partition_projection(relation, ("dt", "hour"), "asc", 2)

    assert expression.columns == ("dt", "hour")
    sql = ibis.duckdb.connect().compile(expression, limit=None)
    assert '"partition"' not in sql
    assert "ASC" in sql
    assert "LIMIT 2" in sql
