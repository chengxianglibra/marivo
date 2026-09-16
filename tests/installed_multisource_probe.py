"""Public installed-package journeys; administration is limited to UUID fixtures."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.installed_wheel_probe import assert_installed_origin


@contextmanager
def administrator(engine: str, project: Path) -> Iterator[Callable[[str], None]]:
    if engine == "sqlite":
        with sqlite3.connect(project / "source.sqlite") as connection:

            def execute(sql: str) -> None:
                connection.execute(sql)

            yield execute
    elif engine == "postgres":
        from tests.multisource_environment import postgres_analysis as pg

        with pg.connection(admin=True) as pg_connection:

            def execute_pg(sql: str) -> None:
                pg_connection.execute(sql)

            yield execute_pg
    elif engine == "mysql":
        from tests.multisource_environment import mysql_analysis as mysql

        with mysql.connection(admin=True) as mysql_connection, mysql_connection.cursor() as cursor:

            def execute_mysql(sql: str) -> None:
                cursor.execute(sql)

            yield execute_mysql
    elif engine == "trino":
        from tests.multisource_environment import trino_analysis as trino

        with trino.connection(admin=True) as trino_connection:
            trino_cursor = trino_connection.cursor()
            try:

                def execute_trino(sql: str) -> None:
                    trino_cursor.execute(sql).fetchall()

                yield execute_trino
            finally:
                trino_cursor.close()
    elif engine == "clickhouse":
        from tests.multisource_environment import clickhouse_analysis as ch

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
    if engine == "sqlite":
        declaration = f"md.sqlite(name='warehouse', path={str(project / 'source.sqlite')!r})"
    elif engine == "trino":
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
        "id": md.source_column("id", data_type="int64"),
        "customer_id": md.source_column("customer_id", data_type="int64"),
        "amount": md.source_column("amount", data_type="float64"),
        "weight": md.source_column("weight", data_type="float64"),
        "channel": md.source_column("channel", data_type="string"),
        "day": md.source_column("day", data_type="date")}}), primary_key=["id"])
customers = ms.entity(name="customers", datasource=ms.ref.datasource("warehouse"),
    source=md.table("{prefix}customers", columns={{
        "id": md.source_column("id", data_type="int64"),
        "region": md.source_column("region", data_type="string")}}), primary_key=["id"])
customer_key = ms.dimension_column(name="customer_key", entity=customers, column="id")
order_customer = ms.dimension_column(name="customer_key", entity=orders, column="customer_id")
region = ms.dimension_column(name="region", entity=customers, column="region")
channel = ms.dimension_column(name="channel", entity=orders, column="channel")
order_day = ms.time_dimension_column(name="order_day", entity=orders, column="day",
    granularity="day", is_default=True)
ms.relationship(name="customer", from_entity=orders, to_entity=customers,
    keys=[ms.join_on(order_customer, customer_key)])
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity="additive")
weight = ms.measure_column(name="weight", entity=orders, column="weight", additivity="additive")
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
count = ms.aggregate(name="count", measure=amount, agg="count")
ms.aggregate(name="mean_amount", measure=amount, agg="mean")
ms.weighted_mean(name="weighted_amount", value=amount, weight=weight)
ms.ratio(name="ratio_amount", numerator=revenue, denominator=count)
ms.aggregate(name="distinct_amount", measure=amount, agg="count_distinct")
''')


def prepare(engine: str, project: Path, prefix: str) -> None:
    with administrator(engine, project) as execute:
        integer, floating, string = (
            ("Int64", "Float64", "String")
            if engine == "clickhouse"
            else ("BIGINT", "DOUBLE PRECISION", "VARCHAR(20)")
        )
        if engine in {"postgres", "mysql"}:
            string = "TEXT"
        if engine == "trino":
            string = "VARCHAR"
        if engine == "sqlite":
            integer, floating, string = "INTEGER", "REAL", "TEXT"
        suffix = " ENGINE=MergeTree ORDER BY id" if engine == "clickhouse" else ""
        if engine == "mysql":
            suffix = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin"
        execute(
            f"CREATE TABLE {prefix}orders (id {integer}, customer_id {integer}, amount {floating}, weight {floating}, channel {string}, day DATE){suffix}"
        )
        execute(f"CREATE TABLE {prefix}customers (id {integer}, region {string}){suffix}")
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
                f"({index}, {1 if index < 3 else 2}, {amount}, {weight}, '{channel}', {date})"
            )
        execute(f"INSERT INTO {prefix}orders VALUES " + ", ".join(rows))
        execute(f"INSERT INTO {prefix}customers VALUES (1, 'EU'), (2, 'US')")


def remove(engine: str, project: Path, prefix: str) -> None:
    with administrator(engine, project) as execute:
        for table in ("orders", "customers"):
            execute(f"DROP TABLE IF EXISTS {prefix}{table}")


def grouped(session: mv.Session, name: str) -> mv.LogicalMetricDataset:
    return (
        session.observe(ms.ref.metric("sales." + name))
        .with_dimensions(ms.ref.dimension("sales.orders.channel"))
        .aggregate()
    )


def produce(project: Path) -> dict[str, object]:
    ms.load()
    session = mv.session.get_or_create("installed", report_timezone="UTC")
    logical = grouped(session, "revenue")
    assert session.runs().items == ()
    assert not session._runtime.statistics.statements
    receipts: list[dict[str, object]] = []
    artifacts: dict[str, str] = {}
    for name, expected in (
        ("revenue", [30.0, 70.0]),
        ("mean_amount", [15.0, 35.0]),
        ("weighted_amount", [17.5, 30.0]),
        ("ratio_amount", [15.0, 35.0]),
    ):
        result = grouped(session, name).execute()
        values = result.to_pandas().sort_values("channel")[name].tolist()
        assert values == pytest.approx(expected)
        assert session._runtime.statistics.primary_queries == 1
        assert session._runtime.statistics.transferred_rows == 2
        artifacts[name] = str(result.state.artifact_ref)
        receipts.append(
            {
                "method": name,
                "expected": expected,
                "actual": values,
                "statistics": asdict(session._runtime.statistics),
            }
        )
    relation = (
        session.observe(ms.ref.metric("sales.mean_amount"))
        .with_dimensions(ms.ref.dimension("sales.customers.region"))
        .aggregate()
        .execute()
    )
    frame = relation.to_pandas().sort_values("region")
    assert frame.region.tolist() == ["EU", "US"]
    assert frame.mean_amount.tolist() == [15.0, 35.0]
    receipts.append(
        {
            "method": "relationship",
            "actual": frame.to_dict("records"),
            "statistics": asdict(session._runtime.statistics),
        }
    )
    history = (
        session.observe(
            ms.ref.metric("sales.revenue"),
            time_scope=mv.time_scope(start="2026-02-01", end="2026-02-05"),
        )
        .with_time_axis(ms.ref.time_dimension("sales.orders.order_day"), grain=mv.grain("day"))
        .aggregate()
    )
    forecast = history.forecast(horizon=mv.periods(2), model=mv.naive()).execute().to_pandas()
    assert forecast.forecast_value.tolist() == [40.0, 40.0]
    assert forecast.training_row_count.tolist() == [4, 4]
    assert session._runtime.statistics.transferred_rows == 4
    receipts.append(
        {
            "method": "native-date-forecast",
            "expected": [40.0, 40.0],
            "statistics": asdict(session._runtime.statistics),
        }
    )
    before = len(session.runs().items)
    from marivo.analysis.compiler.errors import DatasetCompilationError

    with pytest.raises(DatasetCompilationError, match="source-private"):
        grouped(session, "distinct_amount").execute()
    assert len(session.runs().items) == before
    assert not session._runtime.statistics.statements
    saved = {"session": session.id, "artifacts": artifacts}
    (project / "saved.json").write_text(json.dumps(saved))
    return {**saved, "receipts": receipts, "rejection": "distinct before Run and source work"}


def failure(kind: str) -> dict[str, object]:
    from marivo.analysis.materialization.errors import MaterializationError

    session = mv.session.get_or_create("failure-" + kind, report_timezone="UTC")
    with sqlite3.connect(session._runtime.store.db_path) as connection:
        before = connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0]
    logical = session.observe(ms.ref.metric("sales.mean_amount"))
    if kind == "invalid":
        logical = logical.with_dimensions(ms.ref.dimension("sales.customers.region"))
    with pytest.raises(MaterializationError) as caught:
        logical.aggregate().execute()
    assert isinstance(session.runs().items[0], mv.FailedRun)
    assert session._runtime.statistics.validation_queries >= 1
    assert any(role == "source_schema" for role, _ in session._runtime.statistics.statements)
    if kind == "offline":
        # Missing SQLite metadata is a non-scalar result; other drivers report the missing table.
        message = str(caught.value).lower()
        assert any(
            token in message
            for token in (
                "non-scalar result",
                "does not exist",
                "doesn't exist",
                "not found",
                "unknown table",
                "no such table",
                "no columns were found for the requested relation",
            )
        ), message
    if kind == "invalid":
        assert isinstance(caught.value, MaterializationError)
        assert "identity" in str(caught.value).lower() or "duplicate" in str(caught.value).lower()
    with sqlite3.connect(session._runtime.store.db_path) as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == before
    return {
        "failure": kind,
        "error_type": type(caught.value).__name__,
        "error": str(caught.value),
        "new_artifacts": 0,
        "statistics": asdict(session._runtime.statistics),
    }


def cold(project: Path) -> dict[str, object]:
    from marivo.analysis.materialization import admission

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("cold retained work attempted source connection")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(admission, "_build_backend_from_effective", forbidden)
        return cold_retained(project)


def cold_retained(project: Path) -> dict[str, object]:
    saved = json.loads((project / "saved.json").read_text())
    session = mv.session.resume(saved["session"], by="id")
    results = {}
    receipts: list[dict[str, object]] = []
    for name, expected in (
        ("mean_amount", 25.0),
        ("weighted_amount", 130 / 6),
        ("ratio_amount", 25.0),
    ):
        result = session.artifact(saved["artifacts"][name])
        assert isinstance(result, mv.MaterializedMetricDataset)
        before = len(session.runs().items)
        assert grouped(session, name).execute().state.artifact_ref == result.state.artifact_ref
        assert len(session.runs().items) == before
        binding_statistics = asdict(session._runtime.statistics)
        assert not session._runtime.statistics.statements
        rolled = result.rollup(
            drop_dimensions=(ms.ref.dimension("sales.orders.channel"),)
        ).execute()
        values = rolled.to_pandas()[name].tolist()
        assert values == pytest.approx([expected])
        # Local retained DuckDB queries count as primary queries too.
        results[name] = values
        receipts.append(
            {
                "method": name,
                "binding_hit_statistics": binding_statistics,
                "rollup_statistics": asdict(session._runtime.statistics),
            }
        )
    return {
        "session": session.id,
        "source_tables_removed": True,
        "source_connection_forbidden": True,
        "rollups": results,
        "receipts": receipts,
    }


def privileges(engine: str, project: Path) -> dict[str, object]:
    prefix = (project / "prefix").read_text()
    statement = f"INSERT INTO {prefix}customers VALUES (999, 'forbidden')"

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

    if engine == "sqlite":
        return {"account": None, "boundary": "Marivo query-only connection; no server account"}
    if engine == "postgres":
        from tests.multisource_environment import postgres_analysis as pg

        with pg.connection() as connection:
            assert connection.execute("SELECT current_user").fetchone() == ("analysis_reader",)
            error = denied(connection.execute)
    elif engine == "mysql":
        from tests.multisource_environment import mysql_analysis as mysql

        with mysql.connection() as connection_mysql, connection_mysql.cursor() as cursor:
            cursor.execute("SELECT CURRENT_USER()")
            assert str(cursor.fetchone()[0]).startswith("analysis_reader@")
            error = denied(cursor.execute)
    elif engine == "trino":
        from tests.multisource_environment import trino_analysis as trino

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
        from tests.multisource_environment import clickhouse_analysis as ch

        with ch.connection() as client:
            assert client.query("SELECT currentUser(), getSetting('readonly')").first_row == (
                "analysis_reader",
                1,
            )
            error = denied(client.command)
    else:
        raise ValueError(engine)
    return {"account": "analysis_reader", "insert_denied": error}


def main() -> None:
    phase, engine, project_arg, report_arg = sys.argv[1:]
    project = Path(project_arg).resolve()
    origin = assert_installed_origin()
    os.environ["MARIVO_WHEEL_READER"] = "analysis_reader"
    if engine in {"postgres", "mysql", "clickhouse"}:
        from tests.multisource_environment.credentials import password

        os.environ["MARIVO_WHEEL_PASSWORD"] = password()
    if phase == "prepare":
        prefix = "wheel_" + uuid4().hex + "_"
        configure(engine, project, prefix)
        (project / "prefix").write_text(prefix)
        prepare(engine, project, prefix)
        result: dict[str, object] = {"prefix": prefix}
    else:
        os.chdir(project)
        if phase == "produce":
            result = produce(project)
        elif phase == "privileges":
            result = privileges(engine, project)
        elif phase == "cold":
            result = cold(project)
        elif phase == "invalidate":
            prefix = (project / "prefix").read_text()
            with administrator(engine, project) as execute:
                execute(f"INSERT INTO {prefix}customers VALUES (1, 'duplicate')")
            result = {"duplicate_target_inserted": True}
        elif phase in {"invalid", "offline"}:
            result = failure(phase)
        elif phase == "remove":
            remove(engine, project, (project / "prefix").read_text())
            result = {"removed": True}
        else:
            raise ValueError(phase)
    Path(report_arg).write_text(
        json.dumps(
            {
                "phase": phase,
                "backend": engine,
                "pid": os.getpid(),
                "origin": origin,
                "result": result,
                "wire_bytes": None,
                "server_scan_metrics": None,
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    assert_installed_origin()


if __name__ == "__main__":
    main()
