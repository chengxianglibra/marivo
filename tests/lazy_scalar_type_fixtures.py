"""Owned source writers shared by dependency and scalar-type acceptance."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import duckdb

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
