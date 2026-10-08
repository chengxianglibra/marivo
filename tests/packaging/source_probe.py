"""Public installed-package journeys; administration is limited to UUID fixtures."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource import backends
from marivo.datasource.adapters import SourceSession
from tests.packaging.wheel_probe import assert_installed_origin
from tests.support.source_trace import capture_source


@contextmanager
def administrator(engine: str, project: Path) -> Iterator[Callable[[str], None]]:
    if engine == "sqlite":
        with sqlite3.connect(project / "source.sqlite") as connection:

            def execute(sql: str) -> None:
                connection.execute(sql)

            yield execute
    elif engine == "postgres":
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
ms.relationship(name="customer", from_entity=orders, to_entity=customers,
    keys=[ms.join_on(order_customer, customer_key)])
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
weight = ms.measure_column(name="weight", entity=orders, column="weight", additivity=ms.additive_all())
unit = ms.measure_column(name="unit", entity=orders, column="unit", additivity=ms.additive_all())
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
count = ms.aggregate(name="count", measure=unit, agg="sum")
ms.aggregate(name="mean_amount", measure=amount, agg="mean")
ms.weighted_mean(name="weighted_amount", value=amount, weight=weight)
ms.ratio(name="ratio_amount", numerator=revenue, denominator=count)
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
        if engine == "sqlite":
            integer, string = "INTEGER", "TEXT"
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


def observed_rows(
    session: mv.Session, name: str
) -> mv.LogicalNumericRelation | mv.LogicalRatioRelation:
    return session.members(ms.ref.entity("sales.customers")).observe(
        ms.ref.metric("sales." + name), via=ms.ref.relationship("sales.customer")
    )


def produce(project: Path) -> dict[str, object]:
    ms.load()
    session = mv.session.get_or_create("installed", report_timezone="UTC")
    logical = observed_rows(session, "revenue")
    assert session.runs().items == ()
    receipts: list[dict[str, object]] = []
    artifacts: dict[str, str] = {}
    for name, expected in (
        ("revenue", [30.0, 70.0]),
        ("mean_amount", [15.0, 35.0]),
        ("weighted_amount", [17.5, 30.0]),
        ("ratio_amount", [15.0, 35.0]),
    ):
        result = observed_rows(session, name).execute()
        values = result.to_pandas().sort_values("member")["value"].tolist()
        assert values == pytest.approx(expected)
        artifacts[name] = result.state.artifact_ref.ref
        receipts.append(
            {
                "method": name,
                "expected": expected,
                "actual": values,
            }
        )
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-02-01", end="2026-02-05"),
        grain=mv.grain("day"),
        timezone="UTC",
    )
    history = (
        session.members(ms.ref.entity("sales.orders"))
        .observe(
            ms.ref.metric("sales.revenue"),
            during=grid,
        )
        .group_by(grid)
        .rollup()
    )
    forecast = (
        history.execute().forecast(horizon=mv.periods(2), model=mv.naive()).execute().to_pandas()
    )
    assert forecast["value"].tolist() == [40.0, 40.0]
    receipts.append(
        {
            "method": "native-date-fixed-forecast",
            "expected": [40.0, 40.0],
        }
    )
    saved = {"session": session.id, "artifacts": artifacts}
    (project / "saved.json").write_text(json.dumps(saved))
    return {**saved, "receipts": receipts}


def native_audit(engine: str, project: Path) -> dict[str, object]:
    """Compare one installed scalar journey with native driver submissions."""
    observed: list[str] = []
    ms.load()
    with pytest.MonkeyPatch.context() as patch:
        trace = capture_source(patch)
        if engine == "sqlite":
            original_connect = sqlite3.connect
            source = project / "source.sqlite"

            def connect(
                database: str | Path, *args: object, **kwargs: object
            ) -> sqlite3.Connection:
                # Forward the driver's dynamic connection arguments unchanged.
                connection = original_connect(database, *args, **kwargs)  # type: ignore[call-overload]
                assert isinstance(connection, sqlite3.Connection)
                if Path(database).resolve() == source:
                    connection.set_trace_callback(observed.append)
                return connection

            patch.setattr(sqlite3, "connect", connect)
        elif engine == "mysql":
            ss_cursor_type = importlib.import_module("MySQLdb.cursors").SSCursor
            original_execute = ss_cursor_type.execute

            def execute(cursor: object, query: str | bytes, args: object = None) -> int:
                observed.append(query.decode() if isinstance(query, bytes) else query)
                count = original_execute(cursor, query, args)
                assert isinstance(count, int)
                return count

            patch.setattr(ss_cursor_type, "execute", execute)
        else:
            raise ValueError(engine)
        session = mv.session.get_or_create("native-audit", report_timezone="UTC")
        result = observed_rows(session, "revenue").execute().to_pandas().sort_values("member")
        assert result["value"].tolist() == [30.0, 70.0]
        submitted = [item for owner in trace.owners for item in owner.submissions]
    assert sum(item.purpose == "analysis.graph.stage" for item in submitted) == 1
    assert all(owner._closed for owner in trace.owners)
    assert all(item.state == "succeeded" and item.connection_disconnected for item in submitted)
    assert all(item.cursor_state in {"closed", "connection_owned"} for item in submitted)
    expected = Counter(item.sql for item in submitted)
    actual = Counter(observed)
    missing = {sql: count - actual[sql] for sql, count in expected.items() if actual[sql] < count}
    assert not missing, {"unmatched_purposes": [item.purpose for item in submitted]}
    return {
        "boundary": "sqlite3 trace callback" if engine == "sqlite" else "MySQLdb SSCursor.execute",
        "matched": [
            {
                "purpose": item.purpose,
                "state": item.state,
                "sql_sha256": hashlib.sha256(item.sql.encode()).hexdigest(),
            }
            for item in submitted
        ],
        "native_statement_count": len(observed),
        "source_statement_count": len(submitted),
    }


def failure(kind: str) -> dict[str, object]:
    from ibis.common.exceptions import TableNotFound

    from marivo.analysis.materialization.errors import MaterializationError

    session = mv.session.get_or_create("failure-" + kind, report_timezone="UTC")
    with sqlite3.connect(session._runtime.store.db_path) as connection:
        before = connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0]
    logical: mv.LogicalNumericRelation | mv.LogicalRatioRelation | mv.LogicalCategoryRelation
    # Missing-table metadata lookup can fail before the typed analysis boundary.
    with pytest.raises(Exception if kind == "offline" else AnalysisError) as caught:
        if kind == "invalid":
            missing_match = session.members(ms.ref.entity("sales.orders")).read(
                ms.ref.dimension("sales.customers.region"),
                via=ms.ref.relationship("sales.customer"),
            )
            assert isinstance(missing_match, mv.LogicalCategoryRelation)
            logical = missing_match
        else:
            logical = observed_rows(session, "mean_amount")
        logical.execute()
    if kind == "offline":
        # Drivers can reject missing metadata before materialization begins.
        message = str(caught.value).lower()
        missing_table = isinstance(
            caught.value, (KeyError, TableNotFound)
        ) and caught.value.args in (
            (Path("prefix").read_text() + "orders",),
            (Path("prefix").read_text() + "customers",),
        )
        assert missing_table or any(
            token in message
            for token in (
                "non-scalar result",
                "does not exist",
                "doesn't exist",
                "not found",
                "unknown table",
                "no such table",
                "no columns were found for the requested relation",
                "missing_column",
                "no information_schema.tables row",
            )
        ), message
    if kind == "invalid":
        assert isinstance(caught.value, MaterializationError)
        assert "mapping_total" in str(caught.value)
    with sqlite3.connect(session._runtime.store.db_path) as connection:
        assert connection.execute("SELECT count(*) FROM dataset_artifacts").fetchone()[0] == before
    return {
        "failure": kind,
        "error_type": type(caught.value).__name__,
        "error": str(caught.value),
        "new_artifacts": 0,
    }


def cold(project: Path, phase: str) -> dict[str, object]:

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("cold retained work attempted source connection")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(SourceSession, "__init__", forbidden)
        patch.setattr(
            backends,
            "_build_backend_from_effective",
            forbidden,
        )
        if phase == "cold":
            from marivo.analysis.materialization import graph_local_execution

            patch.setattr(graph_local_execution, "execute_verified_fixed", forbidden)
        return cold_retained(project, phase)


def cold_retained(project: Path, phase: str) -> dict[str, object]:
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
        assert isinstance(
            result,
            (
                mv.MaterializedNumericRelation,
                mv.MaterializedRatioRelation,
                mv.MaterializedRolledNumericRelation,
                mv.MaterializedRolledRatioRelation,
            ),
        )
        before = len(session.runs().items)
        continuation = result.rollup()
        rolled = continuation.execute()
        assert continuation.execute().state.artifact_ref == rolled.state.artifact_ref
        new_runs = 1 if phase == "fixed" else 0
        assert len(session.runs().items) == before + new_runs
        values = rolled.to_pandas()["value"].tolist()
        assert values == pytest.approx([expected])
        results[name] = values
        receipts.append(
            {
                "method": name,
                "new_runs": new_runs,
                "cache_reused": True,
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

    if engine == "sqlite":
        return {"account": None, "boundary": "Marivo query-only connection; no server account"}
    if engine == "postgres":
        from tests.datasource.environment import postgres_analysis as pg

        with pg.connection() as connection:
            assert connection.execute("SELECT current_user").fetchone() == ("analysis_reader",)
            error = denied(connection.execute)
    elif engine == "mysql":
        from tests.datasource.environment import mysql_analysis as mysql

        with mysql.connection() as connection_mysql, connection_mysql.cursor() as cursor:
            cursor.execute("SELECT CURRENT_USER()")
            assert str(cursor.fetchone()[0]).startswith("analysis_reader@")
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


def main() -> None:
    phase, engine, project_arg, report_arg = sys.argv[1:]
    project = Path(project_arg).resolve()
    origin = assert_installed_origin()
    os.environ["MARIVO_WHEEL_READER"] = "analysis_reader"
    if engine in {"postgres", "mysql", "clickhouse"}:
        from tests.datasource.environment.credentials import password

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
        elif phase == "native":
            result = native_audit(engine, project)
        elif phase == "privileges":
            result = privileges(engine, project)
        elif phase in {"fixed", "cold"}:
            result = cold(project, phase)
        elif phase == "invalidate":
            prefix = (project / "prefix").read_text()
            with administrator(engine, project) as execute:
                execute(f"DELETE FROM {prefix}customers WHERE id = '2'")
            result = {"required_target_removed": True}
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
            },
            indent=2,
            default=str,
        )
        + "\n"
    )
    assert_installed_origin()


if __name__ == "__main__":
    main()
