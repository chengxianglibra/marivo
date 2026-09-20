"""Explicit setup for SELECT-only local MergeTree and Distributed cluster accounts."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from clickhouse_connect.driver.exceptions import DatabaseError

from tests.multisource_environment.credentials import password


@contextmanager
def connection(*, admin: bool = False, port: int = 18123) -> Iterator[Client]:
    client = clickhouse_connect.get_client(
        host="127.0.0.1",
        port=port,
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


CLUSTER = "marivo_multisource"
CLUSTER_HTTP_PORTS = (18201, 18203)


def setup_cluster() -> dict[str, object]:
    """Prepare the two-shard Distributed fixture exactly once per shard.

    Every loopback HTTP endpoint receives the same SELECT-only
    ``analysis_reader`` account, the ``qualification_cluster`` database, the
    local MergeTree table, and the Distributed table over the static
    ``remote_servers`` topology. Reader privileges are checked for denial on
    every node.
    """
    for port in CLUSTER_HTTP_PORTS:
        with connection(admin=True, port=port) as con:
            con.command(
                "CREATE USER IF NOT EXISTS analysis_reader IDENTIFIED BY {password:String}",
                parameters={"password": password()},
            )
            con.command("REVOKE ALL ON *.* FROM analysis_reader")
            con.command("GRANT SELECT ON qualification.* TO analysis_reader")
            con.command("ALTER USER analysis_reader SETTINGS readonly=1, join_use_nulls=1")
            con.command("CREATE DATABASE IF NOT EXISTS qualification_cluster")
            con.command(
                "CREATE TABLE IF NOT EXISTS qualification_cluster.orders_local"
                " (id Int64, amount Decimal(9, 2)) ENGINE=MergeTree ORDER BY id"
            )
            con.command("GRANT SELECT ON qualification_cluster.* TO analysis_reader")
    for port in CLUSTER_HTTP_PORTS:
        with connection(admin=True, port=port) as con:
            con.command(
                "CREATE TABLE IF NOT EXISTS qualification_cluster.orders_distributed"
                " AS qualification_cluster.orders_local"
                f" ENGINE=Distributed({CLUSTER}, qualification_cluster, orders_local, rand())"
            )
            shards = con.query(
                "SELECT count() FROM system.clusters WHERE cluster = {cluster:String}",
                parameters={"cluster": CLUSTER},
            ).first_row[0]
            assert shards == 2, f"Expected two shards on {CLUSTER}, saw {shards}"
    receipt: dict[str, object] = {"backend": "clickhouse-cluster", "shards": shards}
    for port in CLUSTER_HTTP_PORTS:
        with connection(port=port) as con:
            version, user = con.query("SELECT version(), currentUser()").first_row
            assert user == "analysis_reader", f"Unexpected reader identity {user!r} on {port}"
            receipt[f"version_{port}"] = version
            denied: list[str] = []
            for sql in (
                "CREATE TABLE qualification_cluster.forbidden(id Int64)"
                " ENGINE=MergeTree ORDER BY id",
                "CREATE TEMPORARY TABLE forbidden_temp(id Int64)",
                "INSERT INTO qualification_cluster.orders_local VALUES (1, '1.00')",
                "INSERT INTO qualification_cluster.orders_distributed VALUES (1, '1.00')",
                "DROP TABLE qualification_cluster.orders_distributed",
            ):
                try:
                    con.command(sql)
                except DatabaseError:
                    denied.append(sql)
                else:
                    raise AssertionError("Reader unexpectedly has write privileges")
            receipt[f"denied_{port}"] = denied
    return receipt


def drop_cluster_fixture() -> None:
    """Remove Distributed and local tables plus the fixture database on both shards."""
    for port in CLUSTER_HTTP_PORTS:
        with connection(admin=True, port=port) as con:
            con.command("DROP TABLE IF EXISTS qualification_cluster.orders_distributed")
            con.command("DROP TABLE IF EXISTS qualification_cluster.orders_local")
            con.command("DROP DATABASE IF EXISTS qualification_cluster")


def create_cluster_tables(suffix: str, *, split_at: int = 1) -> None:
    """Seed per-shard local MergeTree tables for one UUID-suffixed probe.

    ``suffix`` must be unique per run and may contain only characters that are
    valid inside an unquoted ClickHouse identifier. The fixed five-row span
    ids 0..4 carries ``amount = id + 0.25``; rows with ``id < split_at`` land
    on shard A and the remainder on shard B; every node also receives a
    Distributed table over the seeded local tables. Call
    ``drop_cluster_tables`` with the same suffix in a ``finally`` block.
    """
    shard_ids = {18201: list(range(split_at)), 18203: list(range(split_at, 5))}
    for port, ids in shard_ids.items():
        with connection(admin=True, port=port) as con:
            con.command(
                f"CREATE TABLE qualification_cluster.orders_{suffix}"
                " (id Int64, amount Decimal(9, 2)) ENGINE=MergeTree ORDER BY id"
            )
            con.command(
                f"CREATE TABLE qualification_cluster.orders_{suffix}_distributed"
                f" AS qualification_cluster.orders_{suffix}"
                f" ENGINE=Distributed({CLUSTER}, qualification_cluster,"
                f" orders_{suffix}, rand())"
            )
            if ids:
                values = ", ".join(f"({identity}, '{identity}.25')" for identity in ids)
                con.command(f"INSERT INTO qualification_cluster.orders_{suffix} VALUES {values}")


def drop_cluster_tables(suffix: str) -> None:
    """Remove the UUID-suffixed probe tables on both shards."""
    for port in CLUSTER_HTTP_PORTS:
        with connection(admin=True, port=port) as con:
            con.command(f"DROP TABLE IF EXISTS qualification_cluster.orders_{suffix}_distributed")
            con.command(f"DROP TABLE IF EXISTS qualification_cluster.orders_{suffix}")


if __name__ == "__main__":
    print(json.dumps(setup(), sort_keys=True))
