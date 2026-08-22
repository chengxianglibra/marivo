"""Explicit source-health contract tests against a real SQLite backend."""

from __future__ import annotations

import sqlite3
import textwrap
from datetime import timedelta
from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
import marivo.semantic.source_health as source_health_module
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticRuntimeError


def _seed_sources(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                customer_id INTEGER,
                status TEXT,
                amount REAL,
                created_at TIMESTAMP
            );
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY
            );
            CREATE TABLE decorator_orders (
                amount REAL
            );
            CREATE TABLE projected_orders (
                amount TEXT
            );
            INSERT INTO orders VALUES
                (1, 1, 'paid', 10.0, '2020-01-01 00:00:00'),
                (2, 1, 'unexpected', 10.0, '2020-01-02 00:00:00'),
                (3, 99, 'paid', 30.0, '2020-01-03 00:00:00');
            INSERT INTO customers VALUES (1), (2);
            INSERT INTO decorator_orders VALUES (10.0);
            INSERT INTO projected_orders VALUES ('10.0');
            """
        )
        connection.commit()
    finally:
        connection.close()


def _catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> tuple[SemanticCatalog, Path]:
    database_path = tmp_path / "warehouse.sqlite"
    _seed_sources(database_path)
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.sqlite(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Analytics', default=True)\n"
            ),
            "sales/model.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms

                orders = ms.entity(
                    name="orders",
                    datasource=ms.ref.datasource("warehouse"),
                    source=md.table("orders"),
                    primary_key=["order_id"],
                )
                customers = ms.entity(
                    name="customers",
                    datasource=ms.ref.datasource("warehouse"),
                    source=md.table("customers"),
                    primary_key=["customer_id"],
                )
                decorator_orders = ms.entity(
                    name="decorator_orders",
                    datasource=ms.ref.datasource("warehouse"),
                    source=md.table("decorator_orders"),
                )
                projected_orders = ms.entity(
                    name="projected_orders",
                    datasource=ms.ref.datasource("warehouse"),
                    source=md.table(
                        "projected_orders",
                        columns={
                            "amount": md.source_column("amount", data_type="float64"),
                        },
                    ),
                )
                order_id = ms.dimension_column(
                    name="order_id", entity=orders, column="order_id"
                )
                order_customer_id = ms.dimension_column(
                    name="customer_id", entity=orders, column="customer_id"
                )
                status = ms.dimension_column(
                    name="status", entity=orders, column="status"
                )
                amount = ms.measure_column(
                    name="amount",
                    entity=orders,
                    column="amount",
                    additivity="additive",
                    unit="USD",
                )
                created_at = ms.time_dimension_column(
                    name="created_at",
                    entity=orders,
                    column="created_at",
                    granularity="second",
                    parse=ms.timestamp(timezone="UTC"),
                )
                created_at_naive = ms.time_dimension_column(
                    name="created_at_naive",
                    entity=orders,
                    column="created_at",
                    granularity="second",
                    parse=ms.datetime(),
                )
                customer_id = ms.dimension_column(
                    name="customer_id", entity=customers, column="customer_id"
                )
                revenue = ms.aggregate(
                    name="revenue", measure=amount, agg="sum", unit="USD"
                )

                @ms.measure(entity=decorator_orders, additivity="additive")
                def decorator_amount(rows):
                    return rows.amount

                decorator_revenue = ms.aggregate(
                    name="decorator_revenue",
                    measure=decorator_amount,
                    agg="sum",
                )
                projected_amount = ms.measure_column(
                    name="amount",
                    entity=projected_orders,
                    column="amount",
                    additivity="additive",
                )
                projected_revenue = ms.aggregate(
                    name="projected_revenue",
                    measure=projected_amount,
                    agg="sum",
                )
                orders_to_customers = ms.relationship(
                    name="orders_to_customers",
                    from_entity=orders,
                    to_entity=customers,
                    keys=[ms.join_on(order_customer_id, customer_id)],
                )
                """
            ),
        }
    )
    monkeypatch.chdir(tmp_path)
    return SemanticCatalog(project), database_path


def test_source_health_without_declared_checks_is_metadata_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    state_files_before = {
        path.relative_to(catalog._project.state_root)
        for path in catalog._project.state_root.rglob("*")
        if path.is_file()
    }

    report = catalog.source_health([ms.ref.metric("sales.revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")
    state_files_after = {
        path.relative_to(catalog._project.state_root)
        for path in catalog._project.state_root.rglob("*")
        if path.is_file()
    }

    assert report.status == "current"
    assert tuple(check.kind for check in report.checks) == ("connectivity", "schema")
    assert all(check.user_data_queried is False for check in report.checks)
    assert all(check.scopes == () for check in report.checks)
    assert schema.datasource == ms.ref.datasource("warehouse")
    assert schema.source == md.table("orders")
    assert schema.observed_schema_fingerprint is not None
    assert schema.observed_schema_fingerprint.startswith("sha256:")
    assert schema.observed_capability_fingerprint is not None
    assert schema.observed_capability_fingerprint.startswith("sha256:")
    new_state_files = state_files_after - state_files_before
    assert all(path.parts[0] == "telemetry" for path in new_state_files)
    assert {
        "not_null",
        "allowed_values",
        "unique",
        "freshness",
        "relationship_matches",
        "relationship_cardinality",
    }.isdisjoint(check.kind for check in report.checks)


def test_source_health_runs_only_explicit_bounded_data_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    relationship = ms.ref.relationship("sales.orders_to_customers")
    scopes = {
        ms.ref.entity("sales.orders"): md.unpruned(max_rows=20, timeout_seconds=5),
        ms.ref.entity("sales.customers"): md.unpruned(max_rows=20, timeout_seconds=5),
    }

    report = catalog.source_health(
        [ms.ref.metric("sales.revenue"), relationship],
        checks=[
            ms.source_check.not_null(ms.ref.measure("sales.orders.amount")),
            ms.source_check.allowed_values(
                ms.ref.dimension("sales.orders.status"),
                values=("paid", "refunded"),
            ),
            ms.source_check.unique(fields=[ms.ref.measure("sales.orders.amount")]),
            ms.source_check.freshness(
                ms.ref.time_dimension("sales.orders.created_at"),
                max_age=timedelta(days=30),
            ),
            ms.source_check.relationship_matches(relationship, side="from"),
            ms.source_check.relationship_cardinality(
                relationship,
                expected="many_to_one",
            ),
        ],
        scope=scopes,
    )

    explicit = {check.kind: check for check in report.checks if check.user_data_queried}
    assert report.status == "failed"
    assert explicit["not_null"].status == "current"
    assert explicit["allowed_values"].status == "failed"
    assert explicit["unique"].status == "failed"
    assert explicit["freshness"].status == "failed"
    assert explicit["relationship_matches"].status == "failed"
    assert explicit["relationship_cardinality"].status == "current"
    assert all(check.scopes for check in explicit.values())


def test_schema_drift_reports_current_affected_refs_and_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("ALTER TABLE orders RENAME COLUMN amount TO amount_old")
        connection.commit()
    finally:
        connection.close()

    report = catalog.source_health([ms.ref.metric("sales.revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert ms.ref.measure("sales.orders.amount") in schema.affected_refs
    assert ms.ref.metric("sales.revenue") in schema.affected_refs
    assert schema.repair is not None
    assert schema.repair.kind == "reauthor"


def test_source_health_inspection_stays_bound_to_loaded_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    monkeypatch.chdir(tmp_path.parent)

    report = catalog.source_health([ms.ref.metric("sales.revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "current"
    assert schema.status == "current"
    assert schema.observed["metadata_authority"] == "authoritative"


def test_decorator_field_schema_drift_fails_closed_with_affected_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("ALTER TABLE decorator_orders RENAME COLUMN amount TO amount_old")
        connection.commit()
    finally:
        connection.close()

    report = catalog.source_health([ms.ref.metric("sales.decorator_revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert ms.ref.measure("sales.decorator_orders.decorator_amount") in schema.affected_refs
    assert ms.ref.metric("sales.decorator_revenue") in schema.affected_refs
    assert schema.repair is not None
    assert schema.repair.kind == "reauthor"


def test_projected_type_drift_is_failed_schema_not_connection_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)

    report = catalog.source_health([ms.ref.metric("sales.projected_revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert schema.observed["code"] == "declared_type_mismatch"
    assert schema.repair is not None
    assert schema.repair.kind == "reauthor"
    assert ms.ref.measure("sales.projected_orders.amount") in schema.affected_refs
    assert ms.ref.metric("sales.projected_revenue") in schema.affected_refs


def test_permission_failure_is_distinct_from_failed_and_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)

    def deny_inspection(*_args: object, **_kwargs: object) -> object:
        raise PermissionError("permission denied for source metadata")

    monkeypatch.setattr(source_health_module, "_inspect_in_project", deny_inspection)
    report = catalog.source_health([ms.ref.metric("sales.revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "unavailable"
    assert schema.status == "unavailable"
    assert schema.observed["code"] == "permission_denied"
    assert schema.status not in {"failed", "unknown"}


def test_source_unavailability_has_its_own_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DROP TABLE orders")
        connection.commit()
    finally:
        connection.close()

    report = catalog.source_health([ms.ref.metric("sales.revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "unavailable"
    assert schema.status == "unavailable"
    assert schema.observed["code"] == "source_unavailable"


def test_unprovable_freshness_is_unknown_instead_of_guessed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)

    report = catalog.source_health(
        [ms.ref.metric("sales.revenue")],
        checks=[
            ms.source_check.freshness(
                ms.ref.time_dimension("sales.orders.created_at_naive"),
                max_age=timedelta(days=30),
            )
        ],
        scope=md.unpruned(max_rows=20, timeout_seconds=5),
    )
    freshness = next(check for check in report.checks if check.kind == "freshness")

    assert report.status == "unknown"
    assert freshness.status == "unknown"
    assert freshness.observed["reason"] == "naive_observed_time"


def test_data_checks_require_scope_and_metadata_checks_forbid_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    scope = md.unpruned(max_rows=10, timeout_seconds=5)

    with pytest.raises(SemanticRuntimeError, match="requires one explicit AuthoringScope"):
        catalog.source_health(
            [ms.ref.metric("sales.revenue")],
            checks=[ms.source_check.not_null(ms.ref.measure("sales.orders.amount"))],
        )
    with pytest.raises(SemanticRuntimeError, match="requires at least one explicit data check"):
        catalog.source_health([ms.ref.metric("sales.revenue")], scope=scope)
