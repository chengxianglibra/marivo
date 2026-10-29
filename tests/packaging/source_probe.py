"""Public installed-package journeys; administration is limited to UUID fixtures."""

from __future__ import annotations

import hashlib
import importlib
import os
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.analysis.core.frame_data import MemoryFrameBacking, StoredFrameBacking
from marivo.datasource.adapters import PhysicalRequirement, SourceSession, provider_for
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import _scalar, forbidden, snapshot
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.packaging.graph_journeys import _frame
from tests.shared_fixtures import catalog_value, terminal_runs
from tests.support.json import Json, checked, encode, obj, read
from tests.support.source_trace import capture_source


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
order_day = ms.time_dimension_column(name="order_day", entity=orders, column="day", is_default=True)
customer = ms.relationship(name="customer", from_entity=orders, to_entity=customers,
    keys=[ms.join_on(order_customer, customer_key)])
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
weight = ms.measure_column(name="weight", entity=orders, column="weight", additivity=ms.additive_all())
unit = ms.measure_column(name="unit", entity=orders, column="unit", additivity=ms.additive_all())
# Named expressions
a = amount
w = weight
u = unit
t = order_day
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


# Literal facts are independently checked against original source rows in _raw.
MEMBER_VALUES = {
    "revenue": (Fraction(30), Fraction(70)),
    "mean_amount": (Fraction(15), Fraction(35)),
    "weighted_amount": (Fraction(35, 2), Fraction(30)),
    "ratio_amount": (Fraction(15), Fraction(35)),
}
ORIGINAL_VALUES = {
    "mean_amount": Fraction(100, 4),
    "weighted_amount": Fraction(10 * 1 + 20 * 3 + 30 * 2 + 40 * 0, 1 + 3 + 2 + 0),
    "ratio_amount": Fraction(100, 4),
}


def observed_rows(session: mv.Session, name: str) -> mv.AnalysisFrame:
    members = session.members(session.catalog.get("entity:sales.customers"))
    return session.observe(
        {"value": catalog_value(session.catalog, "sales." + name, kind="metric")}, by=members
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


def _native_weighted(session: mv.Session, project: Path) -> dict[str, Json]:
    """Compare original facts with independent SQL and native Ibis true division."""
    prefix = (project / "prefix").read_text()
    sql = (
        "SELECT SUM(amount*weight) AS numerator, SUM(weight) AS denominator, "
        f"SUM(amount*weight)/SUM(weight) AS weighted FROM {prefix}orders"
    )
    raw = md.raw_sql(
        ms.ref.datasource("warehouse"),
        sql,
        reason="independent installed weighted source precision oracle",
    )
    assert len(raw.rows) == 1
    assert _integer(raw.rows[0]["numerator"]) == 130
    assert _integer(raw.rows[0]["denominator"]) == 6
    datasource = session.catalog._state.registry.datasources["warehouse"]
    with (
        session._runtime.connection_config.operation() as service,
        service.use_backend("warehouse", read_only=True) as backend,
        SourceSession(
            provider_for(datasource.backend_type),
            datasource,
            backend,
            owns_backend=False,
            project_root=project,
        ) as owner,
    ):
        binding = owner.bind(md.table(prefix + "orders"), source_identity="independent.weighted")
        table = binding.relation
        numerator = (table.amount * table.weight).sum()
        denominator = table.weight.sum()
        # Match declared Trino true division; retain native product/sum accumulation.
        division_numerator = (
            numerator.cast("float64") if owner.provider.name == "trino" else numerator
        )
        expression = table.aggregate(
            numerator=numerator,
            denominator=denominator,
            weighted=division_numerator / denominator,
        )
        qualified = owner.qualify(
            binding,
            PhysicalRequirement(
                "installed.native_weighted_oracle",
                1,
                frozenset({"scan", "project", "group"}),
            ),
        )
        issued = owner.compile(
            qualified,
            expression,
            purpose="installed.native_weighted_oracle",
            expected_schema=expression.schema().to_pyarrow(),
        )
        native = owner.execute(issued)
        assert len(native) == 1
        assert _integer(native.numerator.tolist()[0]) == 130
        assert _integer(native.denominator.tolist()[0]) == 6
        returned: object = native.weighted.tolist()[0]
        assert isinstance(returned, (int, float, Decimal))
        value = str(returned)
        oracle: dict[str, Json] = {
            "sql": issued.sql,
            "dtype": str(native.weighted.dtype),
            "scalar": _scalar(returned),
            "value": value,
            "components": {"numerator": 130, "denominator": 6},
            "mathematical_fraction": str(Fraction(130, 6)),
            "native_fraction": str(Fraction(value)),
            "native_deviation": str(Fraction(value) - Fraction(130, 6)),
            "raw_sql": sql,
            "raw_rows": _scalar(raw.rows),
            "physical_operations": checked(sorted(qualified.requirement.operations)),
            "backend": owner.provider.name,
        }
    assert owner._closed
    return oracle


def _numeric_receipts(
    results: dict[str, mv.AnalysisFrame],
    exports: dict[str, pd.DataFrame],
) -> dict[str, Json]:
    receipts: dict[str, Json] = {}
    for name, result in results.items():
        frame = _frame(result, frame=exports[name])
        scalar = name.startswith("original_")
        metric = name.removeprefix("original_")
        expected: tuple[Fraction, ...]
        if scalar:
            assert result.axes == () and frame.columns.tolist() == ["value"]
            # Native decimal division is a diagnostic fact, not the semantic
            # oracle for the independently evaluated weighted expression.
            expected = (ORIGINAL_VALUES[metric],)
        else:
            assert len(result.axes) == 1 and result.axes[0].role == "identity"
            assert frame.columns.tolist() == ["key_0", "value"]
            assert frame.key_0.tolist() == ["1", "2"]
            expected = MEMBER_VALUES[metric]
        assert frame.value.isna().tolist() == [False] * len(expected)
        actual = frame.value.tolist()
        assert len(actual) == len(expected)
        for returned, wanted in zip(actual, expected, strict=True):
            assert isinstance(returned, (int, float, Decimal))
            assert float(returned) == pytest.approx(float(wanted), rel=1e-14, abs=1e-14), (
                name,
                returned,
                str(wanted),
            )
        assert isinstance(result._backing, (MemoryFrameBacking, StoredFrameBacking))
        assert [column.unit for column in result._backing.data.schema.values] == [None]
        assert result.presence_names == ()
        receipts[name] = snapshot(result, frame=exports[name])
    return receipts


def produce(project: Path) -> dict[str, object]:
    """Evaluate admitted native numerics without publication, then save complete data once."""
    ms.load()
    rows, totals = _raw(project)
    session = mv.session.get_or_create("installed", timezone="UTC")
    before_runs = len(terminal_runs(session))
    before = publication_counts(session)
    assert before_runs == 0 and _artifact_count(session) == 0
    native_oracle = _native_weighted(session, project)
    with pytest.MonkeyPatch.context() as patch:
        trace = capture_source(patch)
        completed = {name: observed_rows(session, name).compute() for name in MEMBER_VALUES}
        for name in ORIGINAL_VALUES:
            completed["original_" + name] = session.observe(
                {"value": catalog_value(session.catalog, "sales." + name, kind="metric")}
            ).compute()
        exports = {name: result.to_pandas() for name, result in completed.items()}
        receipts = _numeric_receipts(completed, exports)
        assert len(terminal_runs(session)) == before_runs and publication_counts(session) == before
        assert trace.native_sql and all(owner._closed for owner in trace.owners)
        submitted = tuple(trace.native_sql)
        saved = {name: result.persist() for name, result in completed.items()}
        assert tuple(trace.native_sql) == submitted
        saved_exports = {name: result.to_pandas() for name, result in saved.items()}
        assert _numeric_receipts(saved, saved_exports) == receipts
        descriptors: dict[str, Json] = {}
        for name, result in saved.items():
            assert isinstance(result._backing, StoredFrameBacking)
            descriptors[name] = _scalar(asdict(result._backing.data))
        state: dict[str, Json] = {
            "session": session.id,
            "artifacts": {name: result.artifact_id for name, result in saved.items()},
            "snapshots": receipts,
            "descriptors": descriptors,
            "native_oracle": native_oracle,
            "run_count": len(terminal_runs(session)),
            "publication_counts": checked(publication_counts(session)),
        }
        (project / "source-results.json").write_bytes(encode(state))
    assert session._runtime.store.resources(session.id) == ()
    return {
        **state,
        "datasource_facts": [{**row, "day": str(row["day"])[:10]} for row in rows],
        "datasource_totals": [
            {"customer_id": str(row["customer_id"]), "amount": _integer(row["amount"])}
            for row in totals
        ],
        "compute_new_runs": 0,
        "compute_new_artifacts": 0,
        "native_statements": list(submitted),
        "qualification": trace.qualifications,
        "persist_replayed_source": False,
        "original_oracles": {name: str(value) for name, value in ORIGINAL_VALUES.items()},
    }


def cold(project: Path) -> dict[str, object]:
    """Read the precise saved outputs in a new process without models or sources."""
    assert not (project / "models").exists()
    state = read(project / "source-results.json")
    identity = state["session"]
    assert isinstance(identity, str)
    native_oracle = obj(state["native_oracle"])
    native_value = native_oracle["value"]
    assert isinstance(native_value, str)
    native_weighted = Fraction(native_value)
    mathematical = Fraction(10 * 1 + 20 * 3 + 30 * 2 + 40 * 0, 1 + 3 + 2 + 0)
    assert native_oracle["mathematical_fraction"] == str(mathematical) == "65/3"
    assert native_oracle["native_deviation"] == str(native_weighted - mathematical)
    assert native_oracle["components"] == {"numerator": 130, "denominator": 6}
    with pytest.MonkeyPatch.context() as patch:
        for owner, name in (
            (ms, "load"),
            (SemanticProject, "load"),
            (SourceSession, "__init__"),
            (SourceSession, "__enter__"),
            (SourceSession, "execute"),
            (DatasourceConnectionService, "use_backend"),
        ):
            patch.setattr(owner, name, forbidden)
        patch.setattr(mv.Session, "catalog", property(forbidden))
        session = mv.session.resume(identity)
        before_runs = len(terminal_runs(session))
        before = publication_counts(session)
        assert before_runs == state["run_count"] and before == state["publication_counts"]
        saved: dict[str, mv.AnalysisFrame] = {}
        for name, reference in obj(state["artifacts"]).items():
            assert isinstance(reference, str)
            result = session.persisted_frame(reference)
            assert isinstance(result._backing, StoredFrameBacking)
            assert _scalar(asdict(result._backing.data)) == obj(state["descriptors"])[name]
            saved[name] = result
        exports = {name: result.to_pandas() for name, result in saved.items()}
        receipts = _numeric_receipts(saved, exports)
        assert receipts == state["snapshots"]
        for name, result in saved.items():
            result.show(n=1)
            result.show(n=0)
            result.findings()
            result.evidence(limit=100)
            continued = result.select({"value": result["value"]}).compute()
            pd.testing.assert_frame_equal(continued.to_pandas(), exports[name])
        assert len(terminal_runs(session)) == before_runs and publication_counts(session) == before
        assert session._runtime.store.resources(session.id) == ()
    return {
        "session": session.id,
        "native_oracle": native_oracle,
        "snapshots": receipts,
        "descriptors": state["descriptors"],
        "new_runs": 0,
        "new_artifacts": 0,
        "resources": 0,
        "models_absent": True,
        "source_access_forbidden": True,
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
        cursors = importlib.import_module("pymysql.cursors")
        original = cursors.Cursor.execute

        def execute(cursor: object, query: str | bytes, args: object = None) -> object:
            observed.append(query.decode() if isinstance(query, bytes) else query)
            return original(cursor, query, args)

        patch.setattr(cursors.Cursor, "execute", execute)
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
        "boundary": "PyMySQL Cursor.execute",
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
    """Run native production, retained source failure and independent cold Frame reading."""
    assert engine in ("postgres", "mysql", "trino", "clickhouse")
    assert phase in ("prepare", "privileges", "produce", "native", "remove", "offline", "cold")
    project = project.resolve()
    project.mkdir(parents=True, exist_ok=True)
    os.environ["MARIVO_PROJECT_ROOT"] = str(project)
    os.environ["MARIVO_WHEEL_READER"] = "analysis_reader"
    if phase != "cold" and engine != "trino" and "MARIVO_WHEEL_PASSWORD" not in os.environ:
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
    elif phase == "cold":
        result = cold(project)
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
