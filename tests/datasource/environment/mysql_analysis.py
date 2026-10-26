"""Explicit setup for the isolated SELECT-only MySQL analysis environment."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pymysql.connections import Connection

HOST = "127.0.0.1"
PORT = 23306
DATABASE = "analysis"
READER = "analysis_reader"


def password() -> str:
    for line in (Path.home() / ".cache/marivo-multisource/secrets.env").read_text().splitlines():
        if line.startswith("QUALIFICATION_PASSWORD="):
            return line.split("=", 1)[1]
    raise ValueError("Missing private qualification password")


@contextmanager
def connection(*, admin: bool = False, port: int = PORT) -> Iterator[Connection]:
    import pymysql

    with pymysql.connect(
        host=HOST,
        port=port,
        database=DATABASE,
        user="root" if admin else READER,
        password=password(),
        autocommit=True,
        charset="utf8mb4",
    ) as con:
        yield con


def setup(*, port: int = PORT) -> dict[str, object]:
    import pymysql

    with connection(admin=True, port=port) as con, con.cursor() as cur:
        cur.execute(
            "CREATE USER IF NOT EXISTS 'analysis_reader'@'%%' IDENTIFIED BY %s", (password(),)
        )
        cur.execute("ALTER USER 'analysis_reader'@'%%' IDENTIFIED BY %s", (password(),))
        cur.execute("REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'analysis_reader'@'%'")
        cur.execute("GRANT SELECT ON analysis.* TO 'analysis_reader'@'%'")
    with connection(port=port) as con, con.cursor() as cur:
        cur.execute("SELECT VERSION(),CURRENT_USER()")
        version, user = cur.fetchone()
        denied = []
        for sql in (
            "CREATE TABLE forbidden(id INT)",
            "CREATE TEMPORARY TABLE forbidden_temp(id INT)",
        ):
            try:
                cur.execute(sql)
            except pymysql.OperationalError:
                denied.append(sql.split("(")[0])
            else:
                raise AssertionError("Reader unexpectedly has object creation privileges")
    return {
        "backend": "mysql",
        "version": version,
        "user": user,
        "denied": denied,
        "dataset_acceptance": False,
    }


if __name__ == "__main__":
    print(json.dumps(setup(), sort_keys=True))
