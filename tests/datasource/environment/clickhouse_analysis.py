"""Explicit setup for SELECT-only MergeTree and Distributed reads on one server."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from tests.datasource.environment.credentials import password

CLUSTER = "marivo_single_shard"


def port() -> int:
    return int(os.environ.get("MARIVO_CLICKHOUSE_TEST_PORT", "18123"))


def _reset_unexpected_grants(con: Client, allowed: set[str]) -> None:
    grants = {row[0] for row in con.query("SHOW GRANTS FOR analysis_reader").result_rows}
    if grants - allowed:
        con.command("REVOKE ALL ON *.* FROM analysis_reader")


@contextmanager
def connection(*, admin: bool = False) -> Iterator[Client]:
    client = clickhouse_connect.get_client(
        host="127.0.0.1",
        port=port(),
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
        assert con.server_version is not None, "Expected the connected server version"
        con.command(
            "CREATE USER IF NOT EXISTS analysis_reader IDENTIFIED BY {password:String}",
            parameters={"password": password()},
        )
        _reset_unexpected_grants(
            con,
            {
                "GRANT SELECT ON qualification.* TO analysis_reader",
                "GRANT SELECT(query, query_id, user) ON system.processes TO analysis_reader",
            },
        )
        con.command("GRANT SELECT ON qualification.* TO analysis_reader")
        con.command("GRANT SELECT(query, query_id, user) ON system.processes TO analysis_reader")
        con.command(
            "ALTER USER analysis_reader SETTINGS readonly=1, join_use_nulls=1, "
            "enable_materialized_cte=0 CHANGEABLE_IN_READONLY, "
            "max_execution_time=0 CHANGEABLE_IN_READONLY, "
            "cancel_http_readonly_queries_on_client_close=0 CHANGEABLE_IN_READONLY, "
            "timeout_before_checking_execution_speed=0 CHANGEABLE_IN_READONLY"
        )
        con.command(
            "CREATE TABLE IF NOT EXISTS permission_probe(id Int64) ENGINE=MergeTree ORDER BY id"
        )
    denied = []
    with connection() as con:
        row = con.query("SELECT version(), currentUser()").first_row
        assert row is not None, "Expected server version and reader identity"
        version, user = row
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
