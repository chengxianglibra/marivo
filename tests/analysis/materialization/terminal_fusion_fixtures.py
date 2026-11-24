"""Repeatable complete-key rows for the native Frame terminal tests."""

from dataclasses import replace
from datetime import date

from tests.datasource.source_cases import SourceData

TERMINAL_SOURCE_PROFILES = [
    ("duckdb", "table"),
    ("postgres", "table"),
    ("mysql", "innodb-table"),
    ("trino", "iceberg"),
    ("clickhouse", "mergetree"),
]


def terminal_key(backend: str, tenant: str, identity: int) -> tuple[str | int, int]:
    # ClickHouse String may carry bytes without the same host-side identity.
    return (ord(tenant) if backend == "clickhouse" else tenant), identity


def terminal_key_rows(backend: str, source: SourceData) -> SourceData:
    if backend != "clickhouse":
        return source
    return replace(
        source,
        clickhouse_columns=source.clickhouse_columns.replace("tenant String", "tenant Int64"),
        rows=[{**row, "tenant": ord(str(row["tenant"]))} for row in source.rows],
    )


def terminal_rows() -> SourceData:
    rows: list[dict[str, object]] = [
        {
            "id": identity,
            "tenant": tenant,
            "category": "x",
            "amount": 1,
            "big": score,
            "weight": 1 if tenant == "a" else 0,
            "day": date(2026, 7, 1),
        }
        for tenant, identity, score in (
            ("b", 1, 2**53 + 1),
            ("a", 2, None),
            ("a", 1, 2**53 + 1),
            ("b", 2, 2**53),
        )
    ]
    return SourceData(
        "id BIGINT, tenant VARCHAR(10), category VARCHAR(10), amount BIGINT, big BIGINT, weight BIGINT, day DATE",
        ",".join(
            f"({row['id']},'{row['tenant']}','x',1,"
            + ("NULL" if row["big"] is None else str(row["big"]))
            + f",{row['weight']},DATE '2026-07-01')"
            for row in rows
        ),
        "id Int64, tenant String, category String, amount Int64, big Nullable(Int64), weight Int64, day Date",
        rows,
    )
