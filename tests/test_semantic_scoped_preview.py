"""Runtime semantic previews bound to persisted discovery snapshots."""

from __future__ import annotations

import json
import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
from marivo.preview import PreviewLimitError
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticRuntimeError


class _QuerySpy:
    def __init__(self) -> None:
        self.user_data_queries = 0
        self.sql: list[str] = []


@pytest.fixture
def query_spy(monkeypatch: pytest.MonkeyPatch) -> _QuerySpy:
    from ibis.backends.duckdb import Backend

    spy = _QuerySpy()
    original_execute = Backend.execute

    def counted_execute(self: Backend, expr: object, *args: object, **kwargs: object) -> object:
        spy.user_data_queries += 1
        spy.sql.append(str(self.compile(expr)))
        return original_execute(self, expr, *args, **kwargs)

    monkeypatch.setattr(Backend, "execute", counted_execute)
    return spy


@pytest.fixture
def scoped_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query_spy: _QuerySpy,
    semantic_project_factory,
):
    database_path = tmp_path / "warehouse.duckdb"
    backend = ibis.duckdb.connect(str(database_path))
    backend.raw_sql("CREATE TABLE orders (order_id INT, amount DOUBLE, region TEXT, dt TEXT)")
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, 10.0, 'east', '2026-07-10'), "
        "(2, 20.0, 'west', '2026-07-10'), "
        "(3, 30.0, 'north', '2026-07-11')"
    )
    backend.raw_sql("CREATE TABLE refunds (refund_id INT, amount DOUBLE, dt TEXT)")
    backend.raw_sql("INSERT INTO refunds VALUES (1, 3.0, '2026-07-10'), (2, 5.0, '2026-07-11')")
    backend.disconnect()

    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Mina Zhang', default=True)\n"
            ),
            "sales/models.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms

                orders = ms.entity(
                    name="orders",
                    datasource=md.ref("datasource.warehouse"),
                    source=md.table("orders"),
                    ai_context=ms.ai_context(
                        business_definition="Sales orders.",
                        guardrails=["Use only for sales analysis."],
                    ),
                )
                refunds = ms.entity(
                    name="refunds",
                    datasource=md.ref("datasource.warehouse"),
                    source=md.table("refunds"),
                    ai_context=ms.ai_context(
                        business_definition="Sales refunds.",
                        guardrails=["Use only for refund analysis."],
                    ),
                )

                region = ms.dimension_column(name="region", entity=orders, column="region")
                order_id = ms.dimension_column(name="order_id", entity=orders, column="order_id")
                refund_order_id = ms.dimension_column(
                    name="refund_order_id", entity=refunds, column="refund_id"
                )
                orders_to_refunds = ms.relationship(
                    name="orders_to_refunds",
                    from_entity=orders,
                    to_entity=refunds,
                    keys=[ms.join_on(order_id, refund_order_id)],
                )
                @ms.measure(entity=orders, additivity="additive", unit="USD")
                def amount(orders):
                    return orders.amount

                gross_revenue = ms.aggregate(name="gross_revenue", measure=amount, agg="sum")
                double_revenue = ms.linear(
                    name="double_revenue",
                    add=[gross_revenue, gross_revenue],
                )

                @ms.metric(entities=[orders], additivity="additive")
                def revenue(orders):
                    return orders.amount.sum()

                @ms.metric(
                    entities=[orders, refunds],
                    root_entity=orders,
                    additivity="additive",
                )
                def net_revenue(orders, refunds):
                    return orders.amount.sum()
                """
            ),
        }
    )
    monkeypatch.chdir(tmp_path)
    orders_snapshot = md.inspect(md.ref("datasource.warehouse"), md.table("orders")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("order_id", "amount", "region", "dt"),
    )
    refunds_snapshot = md.inspect(md.ref("datasource.warehouse"), md.table("refunds")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("refund_id", "amount", "dt"),
    )
    query_spy.user_data_queries = 0
    query_spy.sql.clear()
    return SemanticCatalog(project), orders_snapshot, refunds_snapshot


def test_preview_requires_using(scoped_catalog) -> None:
    catalog, _orders_snapshot, _refunds_snapshot = scoped_catalog
    revenue = catalog.get("metric.sales.revenue")

    with pytest.raises(TypeError):
        catalog.preview(revenue)  # type: ignore[call-arg]


@pytest.mark.parametrize("invalid", ["snapshot-id", ()])
def test_preview_rejects_non_binding_shapes_before_connection(
    scoped_catalog,
    monkeypatch: pytest.MonkeyPatch,
    invalid: object,
) -> None:
    catalog, _orders_snapshot, _refunds_snapshot = scoped_catalog
    revenue = catalog.get("metric.sales.revenue")
    monkeypatch.setattr(
        catalog._project,
        "_connection_service",
        lambda: pytest.fail("connection opened"),
    )

    with pytest.raises(SemanticRuntimeError, match="DiscoverySnapshot"):
        catalog.preview(revenue, using=invalid)  # type: ignore[arg-type]


def test_preview_rejects_stale_or_mismatched_snapshot_before_connection(
    scoped_catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, orders_snapshot, _refunds_snapshot = scoped_catalog
    revenue = catalog.get("metric.sales.revenue")
    mismatched = replace(orders_snapshot, schema_fingerprint="changed-schema")
    monkeypatch.setattr(
        catalog._project,
        "_connection_service",
        lambda: pytest.fail("connection opened"),
    )

    with pytest.raises(SemanticRuntimeError, match="schema fingerprint") as exc_info:
        catalog.preview(revenue, using=mismatched)

    assert exc_info.value.details["query_executed"] is False


def test_preview_rejects_mutated_snapshot_freshness_before_connection(
    scoped_catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, orders_snapshot, _refunds_snapshot = scoped_catalog
    revenue = catalog.get("metric.sales.revenue")
    mismatched = replace(
        orders_snapshot,
        expires_at=orders_snapshot.expires_at + timedelta(days=7),
    )
    monkeypatch.setattr(
        catalog._project,
        "_connection_service",
        lambda: pytest.fail("connection opened"),
    )

    with pytest.raises(SemanticRuntimeError, match="freshness") as exc_info:
        catalog.preview(revenue, using=mismatched)

    assert exc_info.value.details["query_executed"] is False


def test_multi_entity_preview_requires_exact_entity_mapping_before_connection(
    scoped_catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, orders_snapshot, refunds_snapshot = scoped_catalog
    net_revenue = catalog.get("metric.sales.net_revenue")
    orders = catalog.get("entity.sales.orders")
    refunds = catalog.get("entity.sales.refunds")
    monkeypatch.setattr(
        catalog._project,
        "_connection_service",
        lambda: pytest.fail("connection opened"),
    )

    with pytest.raises(SemanticRuntimeError, match="exactly"):
        catalog.preview(net_revenue, using={orders: orders_snapshot})
    with pytest.raises(SemanticRuntimeError, match="entity-kind"):
        catalog.preview(
            net_revenue,
            using={
                orders: orders_snapshot,
                refunds.ref: refunds_snapshot,
                net_revenue.ref: refunds_snapshot,
            },
        )
    with pytest.raises(SemanticRuntimeError, match="entity keys"):
        catalog.preview(
            net_revenue,
            using={
                "sales.orders": orders_snapshot,  # type: ignore[dict-item]
                "sales.refunds": refunds_snapshot,
            },
        )


def test_preview_rejects_invalid_limit_before_connection(
    scoped_catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, orders_snapshot, _refunds_snapshot = scoped_catalog
    monkeypatch.setattr(
        catalog._project,
        "_connection_service",
        lambda: pytest.fail("connection opened"),
    )

    with pytest.raises(PreviewLimitError):
        catalog.preview(
            catalog.get("metric.sales.revenue"),
            using=orders_snapshot,
            limit=0,
        )


def test_preview_rejects_invalid_context_columns_before_connection(
    scoped_catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, orders_snapshot, _refunds_snapshot = scoped_catalog
    monkeypatch.setattr(
        catalog._project,
        "_connection_service",
        lambda: pytest.fail("connection opened"),
    )

    with pytest.raises(SemanticRuntimeError, match="context_columns"):
        catalog.preview(
            catalog.get("metric.sales.revenue"),
            using=orders_snapshot,
            context_columns=("order_id",),
        )


def test_scoped_preview_executes_once_through_timeout_and_never_persists_rows(
    scoped_catalog,
    query_spy: _QuerySpy,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from marivo.datasource.engines import DUCKDB_PROFILE
    from marivo.semantic import catalog as catalog_module
    from marivo.semantic.resolver import SemanticResolver

    catalog, orders_snapshot, _refunds_snapshot = scoped_catalog
    events: list[str] = []
    original_metric = SemanticResolver.metric
    original_preview = catalog_module.preview_ibis_value

    @contextmanager
    def timeout(_backend: object, seconds: int) -> Iterator[None]:
        events.append(f"enter:{seconds}")
        try:
            yield
        finally:
            events.append("exit")

    def tracked_metric(self: SemanticResolver, *args: object, **kwargs: object):
        events.append("materialize")
        return original_metric(self, *args, **kwargs)

    def tracked_preview(*args: object, **kwargs: object):
        events.append("execute")
        return original_preview(*args, **kwargs)

    monkeypatch.setattr(
        catalog_module,
        "require_profile_for_backend_type",
        lambda _backend_type: replace(DUCKDB_PROFILE, authoring_timeout=timeout),
        raising=False,
    )
    monkeypatch.setattr(SemanticResolver, "metric", tracked_metric)
    monkeypatch.setattr(catalog_module, "preview_ibis_value", tracked_preview)

    result = catalog.preview(
        catalog.get("metric.sales.revenue"),
        using=orders_snapshot,
    )

    assert result.status == "passed"
    assert result.rows == ({"value": 30.0},)
    assert result.coverage.scopes == (("sales.orders", orders_snapshot.scope),)
    assert result.coverage.snapshot_ids == (orders_snapshot.id,)
    assert result.coverage.rows_observed == 1
    assert query_spy.user_data_queries == 1
    assert events == ["enter:30", "materialize", "execute", "exit"]
    assert "LIMIT 2" in query_spy.sql[0].upper()

    check_path = next((tmp_path / ".marivo" / "authoring" / "checks").glob("*.json"))
    check_payload = json.loads(check_path.read_text())
    assert check_payload["status"] == "passed"
    assert check_payload["snapshot_ids"] == [orders_snapshot.id]
    assert check_payload["expires_at"] == orders_snapshot.expires_at.isoformat()
    assert "rows" not in check_payload


def test_preview_timeout_cleanup_follows_execution_error(
    scoped_catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.datasource.engines import DUCKDB_PROFILE
    from marivo.semantic import catalog as catalog_module
    from marivo.semantic.resolver import SemanticResolver

    catalog, orders_snapshot, _refunds_snapshot = scoped_catalog
    events: list[str] = []
    original_metric = SemanticResolver.metric

    @contextmanager
    def timeout(_backend: object, _seconds: int) -> Iterator[None]:
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def tracked_metric(self: SemanticResolver, *args: object, **kwargs: object):
        events.append("materialize")
        return original_metric(self, *args, **kwargs)

    def failing_preview(*_args: object, **_kwargs: object):
        events.append("execute_error")
        raise RuntimeError("preview failed")

    monkeypatch.setattr(
        catalog_module,
        "require_profile_for_backend_type",
        lambda _backend_type: replace(DUCKDB_PROFILE, authoring_timeout=timeout),
        raising=False,
    )
    monkeypatch.setattr(SemanticResolver, "metric", tracked_metric)
    monkeypatch.setattr(catalog_module, "preview_ibis_value", failing_preview)

    with pytest.raises(RuntimeError, match="preview failed"):
        catalog.preview(
            catalog.get("metric.sales.revenue"),
            using=orders_snapshot,
        )

    assert events == ["enter", "materialize", "execute_error", "exit"]


def test_preview_check_fingerprints_complete_transitive_dependency_closure(
    scoped_catalog,
    tmp_path: Path,
) -> None:
    catalog, orders_snapshot, _refunds_snapshot = scoped_catalog
    double_revenue = catalog.get("metric.sales.double_revenue")

    catalog.preview(double_revenue, using=orders_snapshot)
    check_dir = tmp_path / ".marivo" / "authoring" / "checks"
    initial = {
        payload["id"]: payload
        for path in check_dir.glob("*.json")
        if (payload := json.loads(path.read_text()))["semantic_ref"] == "sales.double_revenue"
    }
    catalog.preview(double_revenue, using=orders_snapshot)
    unchanged = {
        payload["id"]: payload
        for path in check_dir.glob("*.json")
        if (payload := json.loads(path.read_text()))["semantic_ref"] == "sales.double_revenue"
    }

    model_path = tmp_path / "models" / "semantic" / "sales" / "models.py"
    model_path.write_text(
        model_path.read_text().replace(
            "return orders.amount\n",
            "return orders.amount * 2\n",
        )
    )
    catalog.load()
    catalog.preview(
        catalog.get("metric.sales.double_revenue"),
        using=orders_snapshot,
    )
    changed = {
        payload["id"]: payload
        for path in check_dir.glob("*.json")
        if (payload := json.loads(path.read_text()))["semantic_ref"] == "sales.double_revenue"
    }

    assert unchanged.keys() == initial.keys()
    assert {payload["dependency_fingerprint"] for payload in unchanged.values()} == {
        payload["dependency_fingerprint"] for payload in initial.values()
    }
    assert len(initial) == 1
    assert len(changed) == 2
    assert {payload["dependency_fingerprint"] for payload in changed.values()} != {
        next(iter(initial.values()))["dependency_fingerprint"]
    }


def test_readiness_requires_preview_only_for_direct_executable_refs(
    scoped_catalog,
    query_spy: _QuerySpy,
) -> None:
    catalog, orders_snapshot, _refunds_snapshot = scoped_catalog
    revenue = catalog.get("metric.sales.revenue")

    report = catalog.readiness(refs=[revenue])

    preview_blockers = [
        issue for issue in report.blockers if issue.kind == "runtime_preview_missing"
    ]
    assert [issue.refs for issue in preview_blockers] == [("sales.revenue",)]
    assert "catalog.preview(" in preview_blockers[0].suggested_action
    assert "using=" in preview_blockers[0].suggested_action
    assert 'columns=("order_id", "amount", "region", "dt")' in preview_blockers[0].suggested_action
    assert query_spy.user_data_queries == 0

    catalog.preview(revenue, using=orders_snapshot)
    query_spy.user_data_queries = 0
    report = catalog.readiness(refs=[revenue])

    assert all(issue.kind != "runtime_preview_missing" for issue in report.blockers)
    assert query_spy.user_data_queries == 0


def test_readiness_domain_is_structural_and_does_not_require_dependency_previews(
    scoped_catalog,
    query_spy: _QuerySpy,
) -> None:
    catalog, _orders_snapshot, _refunds_snapshot = scoped_catalog

    report = catalog.readiness(refs=[catalog.get("domain.sales")])

    assert all(issue.kind != "runtime_preview_missing" for issue in report.blockers)
    assert all(issue.kind != "snapshot_missing" for issue in report.blockers)
    assert query_spy.user_data_queries == 0


def test_readiness_snapshot_missing_emits_only_exact_inspection_call(
    scoped_catalog,
    query_spy: _QuerySpy,
    tmp_path: Path,
) -> None:
    catalog, _orders_snapshot, _refunds_snapshot = scoped_catalog
    for path in (tmp_path / ".marivo" / "authoring" / "snapshots").glob("*.json"):
        path.unlink()

    report = catalog.readiness(refs=[catalog.get("metric.sales.revenue")])

    blocker = next(issue for issue in report.blockers if issue.kind == "snapshot_missing")
    assert blocker.refs == ("sales.revenue",)
    assert blocker.suggested_action == (
        'md.inspect(md.ref("datasource.warehouse"), md.table("orders"))'
    )
    assert ".sample(" not in blocker.suggested_action
    assert "columns=" not in blocker.suggested_action
    assert query_spy.user_data_queries == 0


def test_readiness_stale_definition_blocks_once_with_multi_entity_mapping(
    scoped_catalog,
    query_spy: _QuerySpy,
    tmp_path: Path,
) -> None:
    catalog, orders_snapshot, refunds_snapshot = scoped_catalog
    net_revenue = catalog.get("metric.sales.net_revenue")
    catalog.preview(
        net_revenue,
        using={
            catalog.get("entity.sales.orders"): orders_snapshot,
            catalog.get("entity.sales.refunds"): refunds_snapshot,
        },
    )
    model_path = tmp_path / "models" / "semantic" / "sales" / "models.py"
    model_path.write_text(
        model_path.read_text().replace(
            "def net_revenue(orders, refunds):\n    return orders.amount.sum()\n",
            "def net_revenue(orders, refunds):\n    return (orders.amount * 2).sum()\n",
        )
    )
    catalog.load()
    query_spy.user_data_queries = 0

    report = catalog.readiness(refs=[catalog.get("metric.sales.net_revenue")])

    blockers = [issue for issue in report.blockers if issue.kind == "runtime_preview_missing"]
    assert [issue.refs for issue in blockers] == [("sales.net_revenue",)]
    assert 'catalog.get("entity.sales.orders"):' in blockers[0].suggested_action
    assert 'catalog.get("entity.sales.refunds"):' in blockers[0].suggested_action
    assert query_spy.user_data_queries == 0


def test_relationship_preview_check_satisfies_direct_readiness_gate(
    scoped_catalog,
    query_spy: _QuerySpy,
) -> None:
    catalog, orders_snapshot, refunds_snapshot = scoped_catalog
    relationship = catalog.get("relationship.sales.orders_to_refunds")

    missing = catalog.readiness(refs=[relationship])
    blocker = next(issue for issue in missing.blockers if issue.kind == "runtime_preview_missing")
    assert 'catalog.get("entity.sales.orders"):' in blocker.suggested_action
    assert 'catalog.get("entity.sales.refunds"):' in blocker.suggested_action
    assert query_spy.user_data_queries == 0

    catalog.preview(
        relationship,
        using={
            catalog.get("entity.sales.orders"): orders_snapshot,
            catalog.get("entity.sales.refunds"): refunds_snapshot,
        },
    )
    query_spy.user_data_queries = 0
    report = catalog.readiness(refs=[relationship])

    assert all(issue.kind != "runtime_preview_missing" for issue in report.blockers)
    assert all(issue.kind != "snapshot_missing" for issue in report.blockers)
    assert query_spy.user_data_queries == 0


@pytest.mark.parametrize(
    ("target", "tamper"),
    [
        ("snapshot", "scope_guard"),
        ("snapshot", "id_path"),
        ("snapshot", "evidence_format"),
        ("check", "check_id"),
        ("check", "check_scopes"),
    ],
)
def test_readiness_rejects_tampered_persisted_evidence_without_query(
    scoped_catalog,
    query_spy: _QuerySpy,
    tmp_path: Path,
    target: str,
    tamper: str,
) -> None:
    catalog, orders_snapshot, _refunds_snapshot = scoped_catalog
    revenue = catalog.get("metric.sales.revenue")
    catalog.preview(revenue, using=orders_snapshot)
    evidence_dir = tmp_path / ".marivo" / "authoring"
    path = (
        evidence_dir / "snapshots" / f"{orders_snapshot.id}.json"
        if target == "snapshot"
        else next((evidence_dir / "checks").glob("*.json"))
    )
    payload = json.loads(path.read_text())
    if tamper == "scope_guard":
        payload["scope"]["max_rows"] = 0
    elif tamper == "id_path":
        path.rename(path.with_name(f"not-{path.name}"))
        path = path.with_name(f"not-{path.name}")
    elif tamper == "evidence_format":
        payload["evidence_format_version"] += 1
    elif tamper == "check_id":
        payload["id"] = "tampered-check-id"
    elif tamper == "check_scopes":
        payload["scopes"][0][1]["max_rows"] += 1
    path.write_text(json.dumps(payload))
    query_spy.user_data_queries = 0

    report = catalog.readiness(refs=[revenue])

    assert report.status == "blocked"
    assert any(
        issue.kind in {"snapshot_missing", "runtime_preview_missing"} for issue in report.blockers
    )
    assert query_spy.user_data_queries == 0


@pytest.mark.parametrize("tamper", ["revived_expiry", "future_created", "check_predates_snapshot"])
def test_readiness_rejects_tampered_evidence_timestamps_without_query(
    scoped_catalog,
    query_spy: _QuerySpy,
    tmp_path: Path,
    tamper: str,
) -> None:
    catalog, orders_snapshot, _refunds_snapshot = scoped_catalog
    revenue = catalog.get("metric.sales.revenue")
    catalog.preview(revenue, using=orders_snapshot)
    evidence_dir = tmp_path / ".marivo" / "authoring"
    snapshot_path = evidence_dir / "snapshots" / f"{orders_snapshot.id}.json"
    check_path = next((evidence_dir / "checks").glob("*.json"))
    snapshot_payload = json.loads(snapshot_path.read_text())
    check_payload = json.loads(check_path.read_text())
    if tamper == "revived_expiry":
        revived = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        snapshot_payload["expires_at"] = revived
        check_payload["expires_at"] = revived
    elif tamper == "future_created":
        future_created = datetime.now(UTC) + timedelta(days=2)
        future_expiry = future_created + timedelta(hours=24)
        snapshot_payload["created_at"] = future_created.isoformat()
        snapshot_payload["expires_at"] = future_expiry.isoformat()
        check_payload["created_at"] = future_created.isoformat()
        check_payload["expires_at"] = future_expiry.isoformat()
    elif tamper == "check_predates_snapshot":
        snapshot_created = datetime.fromisoformat(snapshot_payload["created_at"])
        check_payload["created_at"] = (snapshot_created - timedelta(seconds=1)).isoformat()
    snapshot_path.write_text(json.dumps(snapshot_payload))
    check_path.write_text(json.dumps(check_payload))
    query_spy.user_data_queries = 0

    report = catalog.readiness(refs=[revenue])

    assert report.status == "blocked"
    assert any(
        issue.kind in {"snapshot_missing", "runtime_preview_missing"} for issue in report.blockers
    )
    assert query_spy.user_data_queries == 0


def test_multi_entity_preview_uses_each_explicit_scope(
    scoped_catalog,
    query_spy: _QuerySpy,
) -> None:
    catalog, orders_snapshot, refunds_snapshot = scoped_catalog
    net_revenue = catalog.get("metric.sales.net_revenue")

    result = catalog.preview(
        net_revenue,
        using={
            catalog.get("entity.sales.orders"): orders_snapshot,
            catalog.get("entity.sales.refunds").ref: refunds_snapshot,
        },
    )

    assert result.rows == ({"value": 30.0},)
    assert result.coverage.scopes == (
        ("sales.orders", orders_snapshot.scope),
        ("sales.refunds", refunds_snapshot.scope),
    )
    assert result.coverage.snapshot_ids == (orders_snapshot.id, refunds_snapshot.id)
    assert query_spy.user_data_queries == 1
