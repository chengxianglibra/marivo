"""Explicit setup and connection helpers for the isolated PostgreSQL analysis service."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

HOST = "127.0.0.1"
PORT = 15432
DATABASE = "analysis"
ADMIN = "analysis_admin"
READER = "analysis_reader"


def password() -> str:
    """Read private test credentials without printing or persisting resolved values."""
    for line in (Path.home() / ".cache/marivo-multisource/secrets.env").read_text().splitlines():
        if line.startswith("QUALIFICATION_PASSWORD="):
            return line.split("=", 1)[1]
    raise ValueError("Private environment file has no QUALIFICATION_PASSWORD")


def connection(*, admin: bool = False) -> psycopg.Connection[tuple[object, ...]]:
    """Open a read-only test connection, or an explicitly requested fixture administrator."""
    return psycopg.connect(
        host=HOST,
        port=PORT,
        dbname=DATABASE,
        user=ADMIN if admin else READER,
        password=password(),
        autocommit=True,
        options="-c timezone=UTC",
    )


def setup() -> dict[str, object]:
    """Provision restrictive reader grants and verify disposable admin-owned fixture access."""
    fixture = sql.Identifier("smoke_" + uuid4().hex)
    with connection(admin=True) as admin:
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER,)).fetchone() is None:
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(READER)))
        admin.execute(
            sql.SQL(
                "ALTER ROLE {} WITH PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(READER), sql.Literal(password()))
        )
        admin.execute("REVOKE ALL ON DATABASE analysis FROM PUBLIC")
        admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        admin.execute("GRANT CONNECT ON DATABASE analysis TO analysis_reader")
        admin.execute("GRANT USAGE ON SCHEMA public TO analysis_reader")
        admin.execute("ALTER ROLE analysis_reader SET default_transaction_read_only = on")
        admin.execute("ALTER ROLE analysis_reader SET timezone = 'UTC'")
        admin.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO analysis_reader")
        admin.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE analysis_admin IN SCHEMA public "
            "GRANT SELECT ON TABLES TO analysis_reader"
        )
        try:
            admin.execute(
                sql.SQL("CREATE TABLE {} (id bigint, amount numeric(18, 2))").format(fixture)
            )
            admin.execute(sql.SQL("INSERT INTO {} VALUES (1, 10.25), (2, 20.50)").format(fixture))
            with connection() as reader:
                row = reader.execute(
                    "SELECT current_user, current_setting('transaction_read_only'), "
                    "has_database_privilege(current_user, current_database(), 'CREATE'), "
                    "has_database_privilege(current_user, current_database(), 'TEMP'), "
                    "has_schema_privilege(current_user, 'public', 'CREATE')"
                ).fetchone()
                assert row == (READER, "on", False, False, False), row
                result = reader.execute(
                    sql.SQL("SELECT count(*), sum(amount)::text FROM {}").format(fixture)
                ).fetchone()
                assert result == (2, "30.75"), result
                # Privilege denial must hold even when a client disables its read-only default.
                reader.execute("SET default_transaction_read_only = off")
                for statement in (
                    sql.SQL("INSERT INTO {} VALUES (3, 1)").format(fixture),
                    sql.SQL("CREATE TABLE public.forbidden (id integer)"),
                    sql.SQL("CREATE TEMP TABLE forbidden_temp (id integer)"),
                ):
                    try:
                        reader.execute(statement)
                    except psycopg.errors.InsufficientPrivilege:
                        pass
                    else:
                        raise AssertionError("Reader unexpectedly accepted a write or CREATE")
                version = reader.execute("SHOW server_version").fetchone()
        finally:
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(fixture))
    return {
        "backend": "postgres",
        "host": HOST,
        "port": PORT,
        "database": DATABASE,
        "reader": READER,
        "server_version": version[0] if version else None,
        "read_only_privileges_verified": True,
        "dataset_acceptance": False,
    }


if __name__ == "__main__":
    print(json.dumps(setup(), sort_keys=True))
