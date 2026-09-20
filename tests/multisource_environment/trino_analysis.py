"""Explicit Iceberg fixtures; SELECT-only identity is enforced by Trino access rules."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from trino.dbapi import Connection
from trino.exceptions import TrinoUserError

NON_ICEBERG_CATALOG = "noniceberg"


@contextmanager
def connection(*, admin: bool = False, catalog: str = "iceberg") -> Iterator[Connection]:
    con = Connection(
        host="127.0.0.1",
        port=18080,
        user="qualifier" if admin else "analysis_reader",
        catalog=catalog,
        schema="analysis",
        timezone="UTC",
    )
    try:
        yield con
    finally:
        # The driver leaves close unannotated; its connection protocol takes no arguments.
        close: Callable[[], None] = con.close
        close()


def setup() -> dict[str, object]:
    with connection(admin=True) as con:
        cur = con.cursor()
        try:
            cur.execute("CREATE SCHEMA IF NOT EXISTS iceberg.analysis").fetchall()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS iceberg.analysis.permission_probe(id BIGINT)"
            ).fetchall()
        finally:
            cur.close()
    denied = []
    with connection() as con:
        cur = con.cursor()
        try:
            version, user = cur.execute("SELECT version(), current_user").fetchone()
            for sql in (
                "CREATE TABLE iceberg.analysis.forbidden(id BIGINT)",
                "INSERT INTO iceberg.analysis.permission_probe VALUES (1)",
                "DELETE FROM iceberg.analysis.permission_probe",
                "DROP TABLE iceberg.analysis.permission_probe",
            ):
                try:
                    cur.execute(sql).fetchall()
                except TrinoUserError as error:
                    assert error.error_name == "PERMISSION_DENIED", str(error)
                    denied.append(sql)
                else:
                    raise AssertionError("Reader unexpectedly has write privileges")
        finally:
            cur.close()
    return {"backend": "trino", "version": version, "user": user, "denied": denied}


NON_ICEBERG_COLUMNS = (
    "id BIGINT",
    "label VARCHAR",
    "ratio DOUBLE",
    "day DATE",
    "amount DECIMAL(9, 2)",
)


def setup_non_iceberg() -> dict[str, object]:
    """Create the generic-metadata-path sample table once; make it reader-readable.

    The ``noniceberg`` catalog is the memory connector, so the table is
    intentionally provisioned as persistent fixture state for Task 5 runs. It
    carries one column of each plan-required type (varchar, bigint, double,
    date, decimal). Reader write denial is verified against a UUID-named
    disposable table, and a reader read of the sample table proves the
    read-only rule actually grants SELECT on this catalog.
    """
    table = "noniceberg_probe"
    with connection(admin=True, catalog=NON_ICEBERG_CATALOG) as con:
        cur = con.cursor()
        try:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {NON_ICEBERG_CATALOG}.analysis").fetchall()
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {NON_ICEBERG_CATALOG}.analysis.{table}"
                f" ({', '.join(NON_ICEBERG_COLUMNS)})"
            ).fetchall()
        finally:
            cur.close()
    denied: list[str] = []
    with connection(catalog=NON_ICEBERG_CATALOG) as con:
        cur = con.cursor()
        try:
            version, user = cur.execute("SELECT version(), current_user").fetchone()
            scratch = f"forbidden_{uuid.uuid4().hex}"
            for sql in (
                f"CREATE TABLE {NON_ICEBERG_CATALOG}.analysis.{scratch}"
                f" ({', '.join(NON_ICEBERG_COLUMNS)})",
                f"INSERT INTO {NON_ICEBERG_CATALOG}.analysis.{table}"
                " VALUES (1, 'x', CAST(0.5 AS DOUBLE), DATE '2024-01-02',"
                " CAST('1.00' AS DECIMAL(9, 2)))",
                f"DELETE FROM {NON_ICEBERG_CATALOG}.analysis.{table} WHERE id = 1",
                f"DROP TABLE {NON_ICEBERG_CATALOG}.analysis.{table}",
            ):
                try:
                    cur.execute(sql).fetchall()
                except TrinoUserError as error:
                    assert error.error_name == "PERMISSION_DENIED", str(error)
                    denied.append(sql)
                else:
                    raise AssertionError("Reader unexpectedly has write privileges")
            assert cur.execute(
                f"SELECT count(*) FROM {NON_ICEBERG_CATALOG}.analysis.{table}"
            ).fetchall() == [[0]], "Reader cannot read the noniceberg sample table"
        finally:
            cur.close()
    return {"backend": "trino-noniceberg", "version": version, "user": user, "denied": denied}


if __name__ == "__main__":
    print(json.dumps(setup(), sort_keys=True))
