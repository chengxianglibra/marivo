"""Explicit setup for a SELECT-only local MergeTree qualification account."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from tests.multisource_environment.mysql_analysis import password


@contextmanager
def connection(*, admin: bool = False) -> Iterator[Client]:
    client = clickhouse_connect.get_client(
        host="127.0.0.1",
        port=18123,
        database="qualification",
        username="qualifier" if admin else "analysis_reader",
        password=password(),
        autogenerate_session_id=False,
    )
    try:
        yield client
    finally:
        client.close()


def setup() -> dict[str, object]:
    with connection(admin=True) as con:
        con.command(
            "CREATE USER IF NOT EXISTS analysis_reader IDENTIFIED BY {password:String}",
            parameters={"password": password()},
        )
        con.command("REVOKE ALL ON *.* FROM analysis_reader")
        con.command("GRANT SELECT ON qualification.* TO analysis_reader")
        con.command("ALTER USER analysis_reader SETTINGS readonly=1, join_use_nulls=1")
        con.command(
            "CREATE TABLE IF NOT EXISTS permission_probe(id Int64) ENGINE=MergeTree ORDER BY id"
        )
    denied = []
    with connection() as con:
        version, user = con.query("SELECT version(), currentUser()").first_row
        for sql in (
            "CREATE TABLE forbidden(id Int64) ENGINE=MergeTree ORDER BY id",
            "CREATE TEMPORARY TABLE forbidden_temp(id Int64)",
            "INSERT INTO permission_probe VALUES (1)",
            "DROP TABLE permission_probe",
        ):
            try:
                con.command(sql)
            except DatabaseError:
                denied.append(sql)
            else:
                raise AssertionError("Reader unexpectedly has write privileges")
        assert con.query("SELECT getSetting('join_use_nulls')").first_row == (1,)
    return {"backend": "clickhouse", "version": version, "user": user, "denied": denied}


if __name__ == "__main__":
    print(json.dumps(setup(), sort_keys=True))
