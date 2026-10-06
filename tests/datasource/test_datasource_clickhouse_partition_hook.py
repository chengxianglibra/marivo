"""ClickHouse physical partition lookup composes a bounded Ibis expression."""

from __future__ import annotations

import ibis

from marivo.datasource.engines.clickhouse import _system_parts_projection


def test_system_parts_filter_and_ascending_order() -> None:
    relation = ibis.table(
        {
            "active": "uint8",
            "database": "string",
            "table": "string",
            "partition": "string",
        },
        name="parts",
    )
    expression = _system_parts_projection(relation, "analytics", "orders", "dt", "asc", 2)

    assert expression.columns == ("dt",)
    sql = ibis.duckdb.connect().compile(expression, limit=None)
    assert "analytics" in sql
    assert "orders" in sql
    assert "DISTINCT" in sql
    assert "ASC" in sql
    assert "LIMIT 2" in sql
