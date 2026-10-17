"""Public installed-package journeys; administration is limited to UUID fixtures."""

from __future__ import annotations

import hashlib
import importlib
import os
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.datasource.adapters import SourceSession
from tests.support.json import Json, checked, encode


@contextmanager
def administrator(engine: str, project: Path) -> Iterator[Callable[[str], None]]:
    if engine == "postgres":
        from tests.datasource.environment import postgres_analysis as pg

        with pg.connection(admin=True) as pg_connection:

            def execute_pg(sql: str) -> None:
                pg_connection.execute(sql)

            yield execute_pg
    elif engine == "mysql":
        from tests.datasource.environment import mysql_analysis as mysql

        with mysql.connection(admin=True) as mysql_connection, mysql_connection.cursor() as cursor:

            def execute_mysql(sql: str) -> None:
                cursor.execute(sql)

            yield execute_mysql
    elif engine == "trino":
        from tests.datasource.environment import trino_analysis as trino

        with trino.connection(admin=True) as trino_connection:
            trino_cursor = trino_connection.cursor()
            try:

                def execute_trino(sql: str) -> None:
                    trino_cursor.execute(sql).fetchall()

                yield execute_trino
            finally:
                trino_cursor.close()
    elif engine == "clickhouse":
        from tests.datasource.environment import clickhouse_analysis as ch

        with ch.connection(admin=True) as client:

            def execute_ch(sql: str) -> None:
                client.command(sql)

            yield execute_ch
    else:
        raise ValueError(engine)


def configure(engine: str, project: Path, prefix: str) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / "marivo.toml").write_text('[project]\nname = "installed-multisource"\n')
    datasources = project / "models/datasources"
    datasources.mkdir(parents=True)
    common = "name='warehouse', host='127.0.0.1', user_env='MARIVO_WHEEL_READER'"
    if engine == "trino":
        declaration = (
            f"md.trino({common}, port=18080, catalog='iceberg', schema='analysis', timezone='UTC')"
        )
    else:
        port, database = {
            "postgres": (15432, "analysis"),
            "mysql": (23306, "analysis"),
            "clickhouse": (18123, "qualification"),
        }[engine]
        declaration = f"md.{engine}({common}, port={port}, database={database!r}, password_env='MARIVO_WHEEL_PASSWORD')"
    (datasources / "warehouse.py").write_text(
        "import marivo.datasource as md\n" + declaration + "\n"
    )
    domain = project / "models/semantic/sales"
    domain.mkdir(parents=True)
    (domain / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n"
    )
    (domain / "objects.py").write_text(f'''import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"),
    source=md.table("{prefix}orders", columns={{
        "id": "id",
        "customer_id": "customer_id",
        "amount": "amount",
        "weight": "weight",
        "unit": "unit",
        "channel": "channel",
        "day": "day"}}), primary_key=["id"])
customers = ms.entity(name="customers", datasource=ms.ref.datasource("warehouse"),
    source=md.table("{prefix}customers", columns={{
        "id": "id",
        "region": "region"}}), primary_key=["id"])
customer_key = ms.dimension_column(name="customer_key", entity=customers, column="id")
order_customer = ms.dimension_column(name="customer_key", entity=orders, column="customer_id")
region = ms.dimension_column(name="region", entity=customers, column="region")
channel = ms.dimension_column(name="channel", entity=orders, column="channel")
order_day = ms.time_dimension_column(name="order_day", entity=orders, column="day",
    granularity="day", is_default=True)
customer = ms.relationship(name="customer", from_entity=orders, to_entity=customers,
    keys=[ms.join_on(order_customer, customer_key)])
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
weight = ms.measure_column(name="weight", entity=orders, column="weight", additivity=ms.additive_all())
unit = ms.measure_column(name="unit", entity=orders, column="unit", additivity=ms.additive_all())
finalized = ms.finalize()
a = finalized.resolve(amount)
w = finalized.resolve(weight)
u = finalized.resolve(unit)
t = finalized.resolve(order_day)
@ms.metric()
def revenue():
    return a.sum(time=t)
@ms.metric()
def count():
    return u.sum(time=t)
@ms.metric()
def mean_amount():
    return a.mean(time=t)
@ms.metric()
def weighted_amount():
    return a.weighted_mean(weight=w, time=t)
@ms.metric()
def ratio_amount():
    return a.sum(time=t) / u.sum(time=t)
''')


def prepare(engine: str, project: Path, prefix: str) -> None:
    with administrator(engine, project) as execute:
        integer, string = (
            ("Int64", "String") if engine == "clickhouse" else ("BIGINT", "VARCHAR(20)")
        )
        if engine in {"postgres", "mysql"}:
            string = "TEXT"
        if engine == "trino":
            string = "VARCHAR"

        customer_identity = string
        suffix = " ENGINE=MergeTree ORDER BY id" if engine == "clickhouse" else ""
        if engine == "mysql":
            suffix = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin"
        execute(
            f"CREATE TABLE {prefix}orders (id {integer}, customer_id {customer_identity}, amount {integer}, weight {integer}, unit {integer}, channel {string}, day DATE){suffix}"
        )
        execute(f"CREATE TABLE {prefix}customers (id {customer_identity}, region {string}){suffix}")
        rows = []
        for index, amount, weight, channel in (
            (1, 10, 1, "a"),
            (2, 20, 3, "a"),
            (3, 30, 2, "b"),
            (4, 40, 0, "b"),
        ):
            date = f"'2026-02-0{index}'"
            if engine == "trino":
                date = "DATE " + date
            rows.append(
                f"({index}, '{1 if index < 3 else 2}', {amount}, {weight}, 1, '{channel}', {date})"
            )
        execute(f"INSERT INTO {prefix}orders VALUES " + ", ".join(rows))
        execute(f"INSERT INTO {prefix}customers VALUES ('1', 'EU'), ('2', 'US')")


def remove(engine: str, project: Path, prefix: str) -> None:
    with administrator(engine, project) as execute:
        for table in ("orders", "customers"):
            execute(f"DROP TABLE IF EXISTS {prefix}{table}")


# These are retained business oracles, not claimed remote Query execution evidence.
UNQUALIFIED_NUMERIC: dict[str, Json] = {
    "member_values": {
        "revenue": [30, 70],
        "mean_amount": [15, 35],
        "weighted_amount": [17.5, 30],
        "ratio_amount": [15, 35],
    },
    "original_rollups": {"mean_amount": 25, "weighted_amount": "130/6", "ratio_amount": 25},
    "pandas_naive_forecast": [40, 40],
    "classification_failure": "mapping_total",
    "qualification": "remote numerical Query and Artifact cold production are not admitted",
}


def observed_rows(session: mv.Session, name: str) -> mv.Query:
    members = session.members(ms.ref.entity("sales.customers"))
    return session.observe(
        {"value": session.catalog.metrics.get("sales." + name)}, by=members, members=members
    )


def _integer(value: object) -> int:
    assert isinstance(value, (int, Decimal))
    return int(value)


def _artifact_count(session: mv.Session) -> int:
    with session._runtime.store._read() as connection:
        value = connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0]
    assert isinstance(value, int)
    return value


def _raw(project: Path) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    """Read the original facts and independent SQL totals through the datasource API."""
    prefix = (project / "prefix").read_text()
    rows = md.raw_sql(
        ms.ref.datasource("warehouse"),
        f"SELECT id, customer_id, amount, weight, unit, channel, day FROM {prefix}orders ORDER BY id",
        reason="verify installed datasource fixture facts",
    )
    assert [
        (
            _integer(row["id"]),
            str(row["customer_id"]),
            _integer(row["amount"]),
            _integer(row["weight"]),
            _integer(row["unit"]),
            str(row["channel"]),
            str(row["day"])[:10],
        )
        for row in rows.rows
    ] == [
        (1, "1", 10, 1, 1, "a", "2026-02-01"),
        (2, "1", 20, 3, 1, "a", "2026-02-02"),
        (3, "2", 30, 2, 1, "b", "2026-02-03"),
        (4, "2", 40, 0, 1, "b", "2026-02-04"),
    ]
    customers = md.raw_sql(
        ms.ref.datasource("warehouse"),
        f"SELECT id, region FROM {prefix}customers ORDER BY id",
        reason="verify installed datasource identity mapping facts",
    )
    assert [(str(row["id"]), str(row["region"])) for row in customers.rows] == [
        ("1", "EU"),
        ("2", "US"),
    ]
    totals = md.raw_sql(
        ms.ref.datasource("warehouse"),
        f"SELECT customer_id, SUM(amount) AS amount FROM {prefix}orders GROUP BY customer_id ORDER BY customer_id",
        reason="verify installed datasource native SQL totals",
    )
    assert [(str(row["customer_id"]), _integer(row["amount"])) for row in totals.rows] == [
        ("1", 30),
        ("2", 70),
    ]
    return rows.rows, totals.rows


def produce(project: Path) -> dict[str, object]:
    """Preserve datasource positives and prove unsupported Query refusal before business I/O."""
    ms.load()
    rows, totals = _raw(project)
    session = mv.session.get_or_create("installed", report_timezone="UTC")
    assert session.runs().items == () and _artifact_count(session) == 0
    receipts: list[dict[str, object]] = []
    with pytest.MonkeyPatch.context() as patch:

        def forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("unqualified remote Query submitted a business source read")

        patch.setattr(SourceSession, "execute", forbidden)
        for name in ("revenue", "mean_amount", "weighted_amount", "ratio_amount"):
            query = observed_rows(session, name)
            with pytest.raises(AnalysisStageUnavailableError) as caught:
                query.execute()
            assert "duckdb" in str(caught.value).lower()
            receipts.append(
                {
                    "method": name,
                    "error_type": type(caught.value).__name__,
                    "error": str(caught.value),
                    "new_runs": 0,
                    "new_artifacts": 0,
                }
            )
    assert session.runs().items == () and _artifact_count(session) == 0
    return {
        "session": session.id,
        "datasource_facts": [{**row, "day": str(row["day"])[:10]} for row in rows],
        "datasource_totals": [
            {"customer_id": str(row["customer_id"]), "amount": _integer(row["amount"])}
            for row in totals
        ],
        "query_status": "unqualified",
        "remote_artifact_cold_status": "unqualified",
        "receipts": receipts,
        "retained_unqualified_oracles": UNQUALIFIED_NUMERIC,
    }


def native_audit(engine: str, project: Path) -> dict[str, object]:
    """Match the datasource's native SQL with the real MySQL driver submission."""
    if engine != "mysql":
        raise ValueError(engine)
    ms.load()
    prefix = (project / "prefix").read_text()
    statement = f"SELECT customer_id, SUM(amount) AS amount FROM {prefix}orders GROUP BY customer_id ORDER BY customer_id"
    observed: list[str] = []
    with pytest.MonkeyPatch.context() as patch:
        cursors = importlib.import_module("MySQLdb.cursors")
        # Ibis versions may choose either buffered or streaming cursors for raw SQL.
        for cursor_type in (cursors.Cursor, cursors.SSCursor):
            original = cursor_type.execute

            def execute(
                cursor: object,
                query: str | bytes,
                args: object = None,
                *,
                original: Callable[..., object] = original,
            ) -> object:
                observed.append(query.decode() if isinstance(query, bytes) else query)
                return original(cursor, query, args)

            patch.setattr(cursor_type, "execute", execute)
        result = md.raw_sql(
            ms.ref.datasource("warehouse"),
            statement,
            reason="audit installed native datasource submissions",
        )
    assert [(str(row["customer_id"]), _integer(row["amount"])) for row in result.rows] == [
        ("1", 30),
        ("2", 70),
    ]
    assert observed.count(statement) == 1
    return {
        "boundary": "MySQLdb Cursor/SSCursor.execute",
        "purpose": "datasource.raw_sql",
        "native_statement_count": len(observed),
        "matched_business_statements": 1,
        "sql_sha256": hashlib.sha256(statement.encode()).hexdigest(),
    }


def failure(project: Path) -> dict[str, object]:
    """The datasource still reports a missing physical table after fixture removal."""
    ms.load()
    prefix = (project / "prefix").read_text()
    with pytest.raises(Exception) as caught:
        md.raw_sql(
            ms.ref.datasource("warehouse"),
            f"SELECT amount FROM {prefix}orders",
            reason="verify removed source table failure",
        )
    message = str(caught.value).lower()
    assert any(
        token in message
        for token in (
            "does not exist",
            "doesn't exist",
            "not found",
            "unknown table",
            "no such table",
            "unknown_table",
            "unknown identifier",
        )
    ), message
    return {
        "boundary": "datasource.raw_sql",
        "failure": "missing physical table",
        "error_type": type(caught.value).__name__,
        "error": str(caught.value),
    }


def privileges(engine: str, project: Path) -> dict[str, object]:
    prefix = (project / "prefix").read_text()
    statement = f"INSERT INTO {prefix}customers VALUES ('999', 'forbidden')"

    def denied(execute: Callable[[str], object]) -> str:
        execute("SELECT 1")
        with pytest.raises(Exception) as caught:
            execute(statement)
        message = str(caught.value).lower()
        assert any(
            token in message
            for token in (
                "denied",
                "permission",
                "read-only",
                "readonly",
                "not allowed",
                "not enough privileges",
            )
        ), message
        return type(caught.value).__name__

    if engine == "postgres":
        from tests.datasource.environment import postgres_analysis as pg

        with pg.connection() as connection:
            assert connection.execute("SELECT current_user").fetchone() == ("analysis_reader",)
            error = denied(connection.execute)
    elif engine == "mysql":
        from tests.datasource.environment import mysql_analysis as mysql

        with mysql.connection() as connection_mysql, connection_mysql.cursor() as cursor:
            cursor.execute("SELECT CURRENT_USER()")
            row = cursor.fetchone()
            assert row is not None and str(row[0]).startswith("analysis_reader@")
            error = denied(cursor.execute)
    elif engine == "trino":
        from tests.datasource.environment import trino_analysis as trino

        with trino.connection() as connection_trino:
            cursor_trino = connection_trino.cursor()
            try:
                assert cursor_trino.execute("SELECT current_user").fetchone() == ["analysis_reader"]

                def execute_trino(sql: str) -> object:
                    return cursor_trino.execute(sql).fetchall()

                error = denied(execute_trino)
            finally:
                cursor_trino.close()
    elif engine == "clickhouse":
        from tests.datasource.environment import clickhouse_analysis as ch

        with ch.connection() as client:
            assert client.query("SELECT currentUser(), getSetting('readonly')").first_row == (
                "analysis_reader",
                1,
            )
            error = denied(client.command)
    else:
        raise ValueError(engine)
    return {"account": "analysis_reader", "insert_denied": error}


def run(phase: str, engine: str, project: Path) -> dict[str, object]:
    """Run only currently owned source and qualification phases; never synthesize cold Artifacts."""
    assert engine in ("postgres", "mysql", "trino", "clickhouse")
    assert phase in ("prepare", "privileges", "produce", "native", "remove", "offline")
    project = project.resolve()
    project.mkdir(parents=True, exist_ok=True)
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    os.environ["MARIVO_WHEEL_READER"] = "analysis_reader"
    if engine != "trino" and "MARIVO_WHEEL_PASSWORD" not in os.environ:
        from tests.datasource.environment.credentials import password

        os.environ["MARIVO_WHEEL_PASSWORD"] = password()
    os.chdir(project)
    prefix_path = project / "prefix"
    if phase == "prepare":
        prefix = "wheel_" + uuid.uuid4().hex[:12] + "_"
        prefix_path.write_text(prefix)
        prepare(engine, project, prefix)
        configure(engine, project, prefix)
        result: dict[str, object] = {"prefix": prefix}
    elif phase == "privileges":
        result = privileges(engine, project)
    elif phase == "produce":
        result = produce(project)
    elif phase == "native":
        result = native_audit(engine, project)
    elif phase == "remove":
        remove(engine, project, prefix_path.read_text())
        result = {"source_tables_removed": True}
    else:
        result = failure(project)
    return {"pid": os.getpid(), "phase": phase, "engine": engine, "result": result}


if __name__ == "__main__":
    from tests.packaging.wheel_probe import assert_installed_origin

    assert_installed_origin()
    phase, engine, project_path, destination = sys.argv[1:]
    report = run(phase, engine, Path(project_path))
    report["origin"] = assert_installed_origin()
    Path(destination).write_bytes(encode(checked(report)))
