"""Explicit online validation contract tests against a real DuckDB backend."""

from __future__ import annotations

import textwrap
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
import marivo.semantic.validation as validation_module
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticRuntimeError
from tests.support.refs import as_ref


def _seed_sources(path: Path) -> None:
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
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
    database_path = tmp_path / "warehouse.duckdb"
    _seed_sources(database_path)
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Analytics', default=True)\n"
            ),
            "sales/model.py": textwrap.dedent(
                """                import marivo.datasource as md
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
                            "amount": "amount",
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
                    additivity=ms.additive_all(),
                    unit="USD",
                )
                created_at = ms.time_dimension_column(
                    name="created_at",
                    entity=orders,
                    column="created_at",
                    parse=ms.timestamp(timezone="UTC"),
                )
                created_at_naive = ms.time_dimension_column(
                    name="created_at_naive",
                    entity=orders,
                    column="created_at",
                    parse=ms.timestamp(),
                )
                customer_id = ms.dimension_column(
                    name="customer_id", entity=customers, column="customer_id"
                )
                decorator_amount = ms.measure_column(
                    name="decorator_amount", entity=decorator_orders,
                    column="amount", additivity=ms.additive_all(),
                )
                projected_amount = ms.measure_column(
                    name="amount",
                    entity=projected_orders,
                    column="amount",
                    additivity=ms.additive_all(),
                )
                orders_to_customers = ms.relationship(
                    name="orders_to_customers",
                    from_entity=orders,
                    to_entity=customers,
                    keys=[ms.join_on(order_customer_id, customer_id)],
                )
                amount_value = amount
                decorator_value = decorator_amount
                projected_value = projected_amount
                @ms.metric()
                def revenue():
                    return amount_value.sum()
                @ms.metric()
                def decorator_revenue():
                    return decorator_value.sum()
                @ms.metric()
                def projected_revenue():
                    return projected_value.sum()
                """
            ),
        }
    )
    monkeypatch.chdir(tmp_path)
    return SemanticCatalog(project), database_path


def test_validate_without_declared_checks_is_metadata_only(
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

    report = catalog.validate([ms.ref.measure("sales.orders.amount")])
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
    assert as_ref(schema.datasource) == ref_factory.datasource("warehouse")
    assert catalog.readiness(refs=schema.target_refs)._analysis_ready_inputs == schema._target_refs
    assert schema.source == md.table("orders")
    assert schema.observed_schema_fingerprint is not None
    assert schema.observed_schema_fingerprint.startswith("sha256:")
    assert schema.observed_capability_fingerprint is not None
    assert schema.observed_capability_fingerprint.startswith("sha256:")
    new_state_files = state_files_after - state_files_before
    assert all(path.parts[0] in {"telemetry", "logs"} for path in new_state_files)
    assert {
        "not_null",
        "allowed_values",
        "unique",
        "freshness",
        "relationship_matches",
        "relationship_cardinality",
    }.isdisjoint(check.kind for check in report.checks)


def test_validate_runs_only_explicit_bounded_data_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    relationship = ms.ref.relationship("sales.orders_to_customers")
    scopes = (
        (ms.ref.entity("sales.orders"), md.unpruned(max_rows=20, timeout_seconds=5)),
        (ms.ref.entity("sales.customers"), md.unpruned(max_rows=20, timeout_seconds=5)),
    )

    report = catalog.validate(
        [ms.ref.measure("sales.orders.amount"), relationship],
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


def test_missing_relationship_rows_do_not_change_structural_cardinality_or_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    relationship = ms.ref.relationship("sales.orders_to_customers")
    before = catalog.readiness(refs=[relationship])
    details = catalog.get("relationship:sales.orders_to_customers").value
    assert details.cardinality == "many_to_one"

    connection = duckdb.connect(str(database_path))
    try:
        connection.execute(
            "INSERT INTO orders VALUES (4, NULL, 'paid', 40.0, '2020-01-04 00:00:00')"
        )
        connection.commit()
    finally:
        connection.close()

    scope = md.unpruned(max_rows=20, timeout_seconds=5)
    report = catalog.validate(
        [relationship],
        checks=[ms.source_check.relationship_matches(relationship, side="from")],
        scope=((ms.ref.entity("sales.orders"), scope), (ms.ref.entity("sales.customers"), scope)),
    )
    check = next(item for item in report.checks if item.kind == "relationship_matches")
    assert check.status == "failed"
    assert check.user_data_queried is True
    assert check.observed["from_unmatched_count"] == 2
    assert len(check.scopes) == 2
    assert catalog.get("relationship:sales.orders_to_customers").value.cardinality == "many_to_one"
    assert catalog.readiness(refs=[relationship]).to_dict() | {"checked_at": None} == (
        before.to_dict() | {"checked_at": None}
    )


def test_schema_drift_reports_current_affected_refs_and_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("ALTER TABLE orders RENAME COLUMN amount TO amount_old")
        connection.commit()
    finally:
        connection.close()

    report = catalog.validate([ms.ref.measure("sales.orders.amount")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert ref_factory.measure("sales.orders.amount") in schema._affected_refs
    assert schema.repair is not None
    assert schema.repair.kind == "reauthor"


def test_validate_inspection_stays_bound_to_loaded_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    monkeypatch.chdir(tmp_path.parent)

    report = catalog.validate([ms.ref.measure("sales.orders.amount")])
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
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("ALTER TABLE decorator_orders RENAME COLUMN amount TO amount_old")
        connection.commit()
    finally:
        connection.close()

    report = catalog.validate([ms.ref.measure("sales.decorator_orders.decorator_amount")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert ref_factory.measure("sales.decorator_orders.decorator_amount") in schema._affected_refs
    assert schema.repair is not None
    assert schema.repair.kind == "reauthor"


def test_projected_validate_checks_the_actual_type_without_a_declared_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)

    report = catalog.validate([ms.ref.measure("sales.projected_orders.amount")])
    schema = next(check for check in report.checks if check.kind == "schema")

    assert report.status == "failed"
    assert schema.status == "failed"
    assert schema.observed["metadata_authority"] == "authoritative"
    assert schema.observed["field_types"] == {"measure:sales.projected_orders.amount": "string"}
    assert "a numeric or duration physical type" in str(schema.observed["contract_violations"])


def test_permission_failure_is_distinct_from_failed_and_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    catalog, _database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)

    def deny_inspection(*_args: object, **_kwargs: object) -> object:
        raise PermissionError("permission denied for source metadata")

    monkeypatch.setattr(validation_module, "_inspect_in_project", deny_inspection)
    report = catalog.validate([ms.ref.measure("sales.orders.amount")])
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
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("DROP TABLE orders")
        connection.commit()
    finally:
        connection.close()

    report = catalog.validate([ms.ref.measure("sales.orders.amount")])
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

    report = catalog.validate(
        [ms.ref.measure("sales.orders.amount")],
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
        catalog.validate(
            [ms.ref.measure("sales.orders.amount")],
            checks=[ms.source_check.not_null(ms.ref.measure("sales.orders.amount"))],
        )
    with pytest.raises(SemanticRuntimeError, match="requires at least one explicit data check"):
        catalog.validate([ms.ref.measure("sales.orders.amount")], scope=scope)


def test_online_validation_rejects_timezone_conflict_before_data_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, semantic_project_factory
) -> None:
    catalog, database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("ALTER TABLE orders ALTER created_at TYPE TIMESTAMPTZ")
    finally:
        connection.close()

    def deny_connection(*args: object, **kwargs: object) -> None:
        pytest.fail("offline loading or readiness connected to the datasource")

    with monkeypatch.context() as offline:
        offline.setattr(DatasourceConnectionService, "session_backend", deny_connection)
        catalog = ms.load(workspace_dir=catalog.workspace_dir)
        assert (
            catalog.readiness(refs=[ms.ref.time_dimension("sales.orders.created_at")]).status
            == "ready"
        )

    def deny_collection(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid physical contracts reached a business-data read")

    monkeypatch.setattr(DatasourceConnectionService, "collect_source", deny_collection)
    report = catalog.validate([ms.ref.metric("sales.revenue")])
    schema = next(check for check in report.checks if check.kind == "schema")
    assert report.status == "failed"
    assert all(not check.user_data_queried for check in report.checks)
    failures = schema.observed["contract_violations"]
    assert isinstance(failures, list) and len(failures) == 1
    assert failures[0]["refs"] == ["time_dimension:sales.orders.created_at"]
    assert failures[0]["expected"] == "an aware source without parser timezone"
    assert failures[0]["location"]["file"].endswith("model.py")
    assert schema.repair is not None
    assert "parse=ms.timestamp()" in schema.repair.action
    assert "sales.orders.created_at" in schema.repair.action

    with pytest.raises(SemanticRuntimeError) as preview:
        catalog.preview(
            ms.ref.time_dimension("sales.orders.created_at"),
            scope=md.unpruned(max_rows=5, timeout_seconds=5),
        )
    session = mv.session.get_or_create("physical-type-validation", timezone="UTC")
    amount = session.catalog.get("measure:sales.orders.amount").value
    event = session.catalog.get("time_dimension:sales.orders.created_at").value
    period = session.time_scope(start="2020-01-01", end="2020-02-01")
    with pytest.raises(SemanticRuntimeError) as analysis:
        session.observe({"amount": amount.sum(time=event)}, by=(period,)).compute()
    assert preview.value.expected == analysis.value.expected == failures[0]["expected"]
    assert preview.value.received == analysis.value.received == failures[0]["received"]
    assert preview.value.semantic_refs == analysis.value.semantic_refs == tuple(failures[0]["refs"])


def test_validation_and_preview_recheck_measure_type_after_schema_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, semantic_project_factory
) -> None:
    catalog, database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    amount = ms.ref.measure("sales.orders.amount")
    assert catalog.validate([amount]).status == "current"
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("ALTER TABLE orders ALTER amount TYPE VARCHAR")
    finally:
        connection.close()
    schema = next(check for check in catalog.validate([amount]).checks if check.kind == "schema")
    assert schema.status == "failed"
    with pytest.raises(SemanticRuntimeError, match="a numeric or duration physical type"):
        catalog.preview(amount, scope=md.unpruned(max_rows=5, timeout_seconds=5))


def test_source_health_interface_is_removed() -> None:
    from marivo.semantic._capabilities.registry import REGISTRY

    assert not hasattr(SemanticCatalog, "source_health")
    assert not hasattr(ms, "SourceHealthReport")
    assert "ValidationReport" in ms.__all__
    with pytest.raises(KeyError):
        REGISTRY.by_canonical_id("source_health")


def test_validation_accepts_native_duration_measure_carriers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, semantic_project_factory
) -> None:
    catalog, database_path = _catalog(tmp_path, monkeypatch, semantic_project_factory)
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute(
            "ALTER TABLE orders ALTER amount TYPE INTERVAL "
            "USING amount::BIGINT * INTERVAL '1 microsecond'"
        )
    finally:
        connection.close()
    report = catalog.validate([ms.ref.measure("sales.orders.amount")])
    assert report.status == "current", report.to_dict()
    schema = next(check for check in report.checks if check.kind == "schema")
    assert schema.observed["field_types"]["measure:sales.orders.amount"] == "interval('us')"


def test_validation_uses_entity_and_field_output_types(
    tmp_path: Path, semantic_project_factory
) -> None:
    database_path = tmp_path / "physical.duckdb"
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("CREATE TABLE raw (amount VARCHAR, point TIMESTAMPTZ)")
        connection.execute("INSERT INTO raw VALUES ('12.5', '2026-08-01 00:00:00+00')")
    finally:
        connection.close()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/model.py": textwrap.dedent("""\
            import marivo.datasource as md
            import marivo.semantic as ms
            @ms.entity(datasource=ms.ref.datasource("warehouse"), source=md.table("raw"))
            def events(raw):
                return raw.mutate(amount=raw.amount.cast("float64"))
            amount=ms.measure_column(name="amount", entity=events, column="amount", additivity=ms.additive_all())
            @ms.time_dimension(entity=events, parse=ms.timestamp(timezone="UTC"))
            def point(rows):
                return rows.point.cast("timestamp")
        """),
        }
    )
    catalog = SemanticCatalog(project)
    report = catalog.validate([ms.ref.entity("sales.events")])
    assert report.status == "current", report.to_dict()
    schema = next(check for check in report.checks if check.kind == "schema")
    assert schema.observed["field_types"] == {
        "measure:sales.events.amount": "float64",
        "time_dimension:sales.events.point": "timestamp",
    }
    assert catalog.preview(
        ms.ref.measure("sales.events.amount"),
        scope=md.unpruned(max_rows=5, timeout_seconds=5),
    ).rows == ({"amount": 12.5},)
