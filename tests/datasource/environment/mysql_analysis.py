"""Explicit setup for the isolated SELECT-only MySQL analysis environment."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

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
def connection(*, admin: bool = False):
    import MySQLdb

    con = MySQLdb.connect(
        host=HOST,
        port=PORT,
        database=DATABASE,
        user="root" if admin else READER,
        password=password(),
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        yield con
    finally:
        con.close()


def setup() -> dict[str, object]:
    import MySQLdb

    with connection(admin=True) as con, con.cursor() as cur:
        cur.execute(
            "CREATE USER IF NOT EXISTS 'analysis_reader'@'%%' IDENTIFIED BY %s", (password(),)
        )
        cur.execute("ALTER USER 'analysis_reader'@'%%' IDENTIFIED BY %s", (password(),))
        cur.execute("REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'analysis_reader'@'%'")
        cur.execute("GRANT SELECT ON analysis.* TO 'analysis_reader'@'%'")
    with connection() as con, con.cursor() as cur:
        cur.execute("SELECT VERSION(),CURRENT_USER()")
        version, user = cur.fetchone()
        denied = []
        for sql in (
            "CREATE TABLE forbidden(id INT)",
            "CREATE TEMPORARY TABLE forbidden_temp(id INT)",
        ):
            try:
                cur.execute(sql)
            except MySQLdb.OperationalError:
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
