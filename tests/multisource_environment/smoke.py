"""Explicit opt-in local service smoke; never collected as a Dataset acceptance test."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

# This pinned driver has no py.typed marker; keep untyped values at the smoke boundary.
import clickhouse_connect  # type: ignore[import-untyped]
import trino.dbapi
from trino.exceptions import TrinoQueryError

CLICKHOUSE_SETTINGS = {
    "enable_shared_storage_snapshot_in_query": 1,
    "join_use_nulls": 1,
    "max_memory_usage": 536870912,
    "max_execution_time": 30,
    "use_query_cache": 0,
}


def verify_clickhouse_settings(rows: Sequence[Sequence[object]]) -> None:
    expected = {name: str(value) for name, value in CLICKHOUSE_SETTINGS.items()}
    received: dict[str, str] = {}
    for row in rows:
        if len(row) != 2 or not isinstance(row[0], str) or not isinstance(row[1], str):
            raise ValueError("Malformed ClickHouse setting row")
        if row[0] in received:
            raise ValueError("Duplicate ClickHouse setting row")
        received[row[0]] = row[1]
    if received != expected:
        raise ValueError("Effective ClickHouse settings differ from qualification requirements")


def clickhouse_smoke() -> dict[str, object]:
    password_file = Path.home() / ".cache/marivo-multisource/secrets.env"
    password = password_file.read_text().strip().split("=", 1)[1]
    client = clickhouse_connect.get_client(
        host="127.0.0.1",
        port=18123,
        username="qualifier",
        password=password,
        database="qualification",
        settings=CLICKHOUSE_SETTINGS,
    )
    name = "smoke_" + uuid4().hex
    try:
        version: object = client.command("SELECT version()")
        client.command(
            f"CREATE TABLE {name} (id Int64, amount Decimal(18, 2)) ENGINE=MergeTree ORDER BY id"
        )
        client.command(f"INSERT INTO {name} VALUES (1, 10.25), (2, 20.50), (3, -0.75)")
        result = client.query(f"SELECT count(), toString(sum(amount)) FROM {name}")
        assert result.result_rows == [(3, "30")], result.result_rows
        settings = client.query(
            "SELECT name, value FROM system.settings WHERE name IN "
            "('enable_shared_storage_snapshot_in_query', 'join_use_nulls', "
            "'max_memory_usage', 'max_execution_time', 'use_query_cache') ORDER BY name"
        )
        verify_clickhouse_settings(settings.result_rows)
        return {
            "backend": "clickhouse",
            "scope": "local MergeTree environment smoke only",
            "version": version,
            "query_id": result.query_id,
            "result": result.result_rows,
            "settings": settings.result_rows,
            "fixture": name,
            "dataset_acceptance": False,
        }
    finally:
        try:
            client.command(f"DROP TABLE IF EXISTS {name}")
        finally:
            client.close()


def trino_smoke() -> dict[str, object]:
    connection = trino.dbapi.Connection(
        host="127.0.0.1",
        port=18080,
        user="qualifier",
        catalog="iceberg",
        timezone="UTC",
        max_attempts=1,
        request_timeout=30,
    )
    cursor = connection.cursor()
    namespace = "smoke_" + uuid4().hex
    table = f"iceberg.{namespace}.orders"
    try:
        cursor.execute("SELECT version()")
        version: object = cursor.fetchall()[0][0]
        cursor.execute(f"CREATE SCHEMA iceberg.{namespace}")
        cursor.fetchall()
        cursor.execute(
            f"CREATE TABLE {table} (id BIGINT, amount DECIMAL(18,2)) "
            "WITH (format='PARQUET', format_version=2)"
        )
        cursor.fetchall()
        cursor.execute(f"INSERT INTO {table} VALUES (1, 10.25), (2, 20.50)")
        cursor.fetchall()
        cursor.execute(
            f"SELECT snapshot_id FROM iceberg.{namespace}.\"orders$refs\" WHERE name = 'main'"
        )
        snapshots = cursor.fetchall()
        assert len(snapshots) == 1
        snapshot: object = snapshots[0][0]
        assert type(snapshot) is int
        cursor.execute(f"INSERT INTO {table} VALUES (3, -0.75)")
        cursor.fetchall()
        cursor.execute(
            f"SELECT count(*), CAST(sum(amount) AS VARCHAR) FROM {table} FOR VERSION AS OF {snapshot}"
        )
        pinned: object = cursor.fetchall()
        pinned_query: object = cursor.query_id
        cursor.execute(f"SELECT count(*), CAST(sum(amount) AS VARCHAR) FROM {table}")
        current: object = cursor.fetchall()
        assert pinned == [[2, "30.75"]], pinned
        assert current == [[3, "30.00"]], current
        missing_rejected = False
        try:
            cursor.execute(f"SELECT * FROM {table} FOR VERSION AS OF CAST(1 AS BIGINT)")
            cursor.fetchall()
        except TrinoQueryError as error:
            # An arbitrary query error is not evidence of snapshot-specific rejection.
            if "snapshot" not in str(error).lower():
                raise
            missing_rejected = True
        assert missing_rejected
        return {
            "backend": "trino",
            "scope": "single-node Iceberg/JDBC V1/local warehouse environment smoke only",
            "version": version,
            "snapshot": snapshot,
            "pinned_query_id": pinned_query,
            "pinned": pinned,
            "current": current,
            "missing_snapshot_rejected": missing_rejected,
            "fixture": namespace,
            "dataset_acceptance": False,
        }
    finally:
        try:
            cursor.execute(f"DROP SCHEMA IF EXISTS iceberg.{namespace} CASCADE")
            cursor.fetchall()
        finally:
            cursor.close()
            # The pinned Trino driver leaves Connection.close unannotated.
            connection.close()  # type: ignore[no-untyped-call]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=("trino", "clickhouse"))
    arguments = parser.parse_args()
    result = trino_smoke() if arguments.backend == "trino" else clickhouse_smoke()
    print(json.dumps(result, sort_keys=True, indent=2))
