"""Owned source writers shared by dependency and scalar-type acceptance."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt

Engine = Literal["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"]


@contextmanager
def source_writer(engine: Engine, database: Path) -> Iterator[Callable[[str], None]]:
    if engine == "duckdb":
        with duckdb.connect(str(database)) as duckdb_connection:

            def execute(sql: str) -> None:
                duckdb_connection.execute(sql)

            yield execute
    elif engine == "sqlite":
        with sqlite3.connect(database) as sqlite_connection:

            def execute(sql: str) -> None:
                sqlite_connection.execute(sql)

            yield execute
    elif engine == "postgres":
        from tests.multisource_environment import postgres_analysis as pg

        with pg.connection(admin=True) as postgres_connection:

            def execute(sql: str) -> None:
                postgres_connection.execute(sql.replace("DOUBLE", "DOUBLE PRECISION"))

            yield execute
    elif engine == "mysql":
        from tests.multisource_environment import mysql_analysis as mysql

        with mysql.connection(admin=True) as mysql_connection, mysql_connection.cursor() as cursor:

            def execute(sql: str) -> None:
                cursor.execute(sql)

            yield execute
    elif engine == "clickhouse":
        from tests.multisource_environment import clickhouse_analysis as clickhouse

        with clickhouse.connection(admin=True) as clickhouse_connection:

            def execute(sql: str) -> None:
                clickhouse_connection.command(sql)

            yield execute
    else:
        from tests.multisource_environment import trino_analysis as trino

        with trino.connection(admin=True) as trino_connection:
            cursor = trino_connection.cursor()
            try:

                def execute(sql: str) -> None:
                    cursor.execute(sql)
                    cursor.fetchall()

                yield execute
            finally:
                cursor.close()


def arrow_result(runtime: DatasetRuntime, artifact: str) -> pa.Table:
    record = runtime.store.artifact(artifact)
    assert record is not None
    receipt = record.descriptor.storage_receipt
    assert isinstance(receipt, LocalReceipt)
    return pa.concat_tables(
        [
            pq.read_table(
                runtime.store.project_root / receipt.project_relative_path / f.relative_path
            )
            for f in receipt.file_manifest
        ]
    )


def cold_check(
    project: Path, session: str, artifact: str, expected: pa.Table, tmp_path: Path
) -> None:
    output = tmp_path / "cold.parquet"
    code = """
import sys
from pathlib import Path
import pyarrow.parquet as pq
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import LocalReceipt
import marivo.analysis.materialization.execution as execution

def denied(*args, **kwargs):
    raise AssertionError('cold read accessed source')
execution.resolve_execution = denied
runtime = DatasetRuntime.open(Path(sys.argv[1]), sys.argv[2])
result = runtime.artifact(sys.argv[3])
result.to_pandas()
assert runtime.statistics.primary_queries == 0
record = runtime.store.artifact(sys.argv[3])
receipt = record.descriptor.storage_receipt
assert isinstance(receipt, LocalReceipt)
pq.write_table(pq.read_table(Path(sys.argv[1]) / receipt.project_relative_path / receipt.file_manifest[0].relative_path), sys.argv[4])
"""
    subprocess.run(
        [sys.executable, "-c", code, str(project), session, artifact, str(output)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "TZ": "Pacific/Honolulu"},
    )
    assert pq.read_table(output).equals(expected)
