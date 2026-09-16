"""Explicit Iceberg fixtures; SELECT-only identity is enforced by Trino access rules."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

from trino.dbapi import Connection


@contextmanager
def connection(*, admin: bool = False) -> Iterator[Connection]:
    con = Connection(
        host="127.0.0.1",
        port=18080,
        user="qualifier" if admin else "analysis_reader",
        catalog="iceberg",
        schema="analysis",
        timezone="UTC",
    )
    try:
        yield con
    finally:
        con.close()


def setup() -> dict[str, object]:
    from trino.exceptions import TrinoUserError

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


if __name__ == "__main__":
    print(json.dumps(setup(), sort_keys=True))
