"""Snapshot-independent semantic preview and artifact certification."""

from __future__ import annotations

import json
import re
import textwrap
from datetime import date, datetime
from pathlib import Path

import ibis
import pyarrow as pa
import pytest

import marivo
import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource import runtime as datasource_runtime
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import (
    DomainEntry,
    PeriodCalendarEntry,
    SemanticCatalog,
    TemporalSetEntry,
)
from marivo.semantic.errors import SemanticRuntimeError
from tests.semantic.preview_helpers import certified_project, default_scope
from tests.shared_fixtures import catalog_entry, catalog_value
from tests.support.refs import as_ref


@pytest.fixture
def scoped_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
):
    database_path = tmp_path / "warehouse.duckdb"
    backend = ibis.duckdb.connect(str(database_path))
    backend.raw_sql("CREATE TABLE orders (order_id INT, amount DOUBLE, region TEXT, ts TIMESTAMP)")
    backend.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, 10.0, 'east', TIMESTAMP '2026-07-10 01:00:00'), "
        "(2, 20.0, 'west', TIMESTAMP '2026-07-10 12:00:00'), "
        "(3, 30.0, 'east', TIMESTAMP '2026-07-11 01:00:00')"
    )
    backend.raw_sql("CREATE TABLE refunds (refund_id INT, amount DOUBLE)")
    backend.raw_sql("INSERT INTO refunds VALUES (1, 3.0), (2, 5.0)")
    backend.disconnect()

    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Data', default=True)\n"
            ),
            "sales/models.py": textwrap.dedent(
                """                import marivo.datasource as md
                import marivo.semantic as ms

                orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["order_id"])
                refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("warehouse"), source=md.table("refunds"), primary_key=["refund_id"])
                region = ms.dimension_column(name="region", entity=orders, column="region")
                order_id = ms.dimension_column(name="order_id", entity=orders, column="order_id")
                refund_id = ms.dimension_column(name="refund_id", entity=refunds, column="refund_id")
                occurred_at = ms.time_dimension_column(name="occurred_at", entity=orders, column="ts")
                amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD")
                ms.relationship(name="order_refund", from_entity=orders, to_entity=refunds, keys=[ms.join_on(order_id, refund_id)])

                amount = amount_ref

                @ms.metric()
                def revenue():
                    return amount.sum()
                """
            ),
        }
    )
    monkeypatch.chdir(tmp_path)
    return SemanticCatalog(project), tmp_path


def test_preview_requires_explicit_scope(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    with pytest.raises(TypeError):
        catalog.preview(ms.ref.measure("sales.orders.amount"))  # type: ignore[call-arg]


@pytest.mark.parametrize("operation", ("preview", "preview_many"))
@pytest.mark.parametrize("limit", (0, 101, "20", True))
def test_invalid_preview_limits_are_structured_before_connection(
    scoped_catalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    limit: object,
) -> None:
    catalog, _root = scoped_catalog
    amount = catalog_value(catalog, "sales.orders.amount", kind="field")
    monkeypatch.setattr(
        "marivo.datasource.runtime.open_backend",
        lambda *args, **kwargs: pytest.fail("invalid preview limit opened a connection"),
    )

    with pytest.raises(SemanticRuntimeError) as captured:
        # Runtime invalid inputs deliberately bypass the annotated int contract.
        if operation == "preview":
            catalog.preview(amount, scope=default_scope(), limit=limit)  # type: ignore[arg-type]
        else:
            catalog.preview_many([amount], scope=default_scope(), limit=limit)  # type: ignore[arg-type]

    error = captured.value
    assert error.expected == "an integer preview limit from 1 to 100 inclusive"
    assert error.received == f"{limit!r} ({type(limit).__name__})"
    assert error.details["query_executed"] is False
    assert error.hint is not None
    assert "integer from 1 to 100 inclusive" in error.hint
    assert "retained preview rows" in error.hint
    assert "scope.max_rows separately guards source acquisition" in error.hint
    assert error.repair is not None
    assert "limit=20" in error.repair.action
    assert error.repair.help_target.display == f"semantic.{operation}"
    rendered = str(error)
    assert f"hint: {error.hint}" in rendered
    assert f"Retry catalog.{operation} with limit=20." in rendered
    assert "metric bodies" not in rendered
    assert "dataset references" not in rendered
    assert "backend_factory" not in rendered
    marivo.help(error.repair.help_target.display)
    help_text = capsys.readouterr().out
    assert "integer from 1 to 100 inclusive" in help_text
    assert "scope.max_rows" in help_text


def test_preview_value_and_ref_have_equivalent_current_results(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    amount = catalog_value(catalog, "sales.orders.amount", kind="field")

    by_value = catalog.preview(amount, scope=default_scope())
    by_ref = catalog.preview(ms.ref.measure("sales.orders.amount"), scope=default_scope())

    assert by_value.rows == by_ref.rows
    assert sorted(row["amount"] for row in by_value.rows) == [10.0, 20.0, 30.0]
    assert by_value.coverage.scopes == (("sales.orders", default_scope()),)


def test_published_metric_preview_uses_bounded_scope(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    for value in (catalog.get("metric:sales.revenue").value, ms.ref.metric("sales.revenue")):
        preview = catalog.preview(value, scope=default_scope())
        assert preview.kind == "semantic_metric"
        assert preview.coverage.scopes == (("sales.orders", default_scope()),)
        assert preview.sample_policy.method == "pre_aggregate_limit"


def test_ordinary_preview_ignores_history_and_writes_no_check(scoped_catalog) -> None:
    catalog, root = scoped_catalog
    check_dir = root / ".marivo" / "authoring" / "checks"
    check_dir.mkdir(parents=True)
    historical = check_dir / "historical.json"
    historical.write_text('{"old": true}', encoding="utf-8")

    amount = ms.ref.measure("sales.orders.amount")
    before = catalog.readiness(refs=[amount]).to_dict()
    result = catalog.preview(amount, scope=default_scope(max_rows=3))
    after = catalog.readiness(refs=[amount]).to_dict()

    assert result.status == "passed"
    assert historical.read_text(encoding="utf-8") == '{"old": true}'
    assert tuple(check_dir.iterdir()) == (historical,)
    before.pop("checked_at")
    after.pop("checked_at")
    assert after == before


def test_partition_and_half_open_time_range_are_applied(scoped_catalog) -> None:
    catalog, _root = scoped_catalog

    partitioned = catalog.preview(
        ms.ref.entity("sales.orders"),
        scope=md.partition({"region": "east"}, max_rows=10, timeout_seconds=30),
    )
    ranged = catalog.preview(
        ms.ref.entity("sales.orders"),
        scope=md.time_range(
            "ts",
            start=datetime(2026, 7, 10),
            end=datetime(2026, 7, 11),
            max_rows=10,
            timeout_seconds=30,
        ),
    )

    assert [row["order_id"] for row in partitioned.rows] == [1, 3]
    assert [row["order_id"] for row in ranged.rows] == [1, 2]


def test_scope_row_guard_reports_real_truncation(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    result = catalog.preview(
        ms.ref.entity("sales.orders"), scope=default_scope(max_rows=2), limit=20
    )

    assert result.returned_row_count == 2
    assert result.is_truncated is True
    assert result.coverage.rows_observed == 3
    assert result.coverage.scope_exhaustion == "truncated"
    assert result.coverage.scope_exactness == "sample_only"
    card = result.render()
    assert "preview_rows_truncated: true" in card
    assert "scope_coverage: truncated/sample_only" in card


def test_time_dimension_reports_native_naive_parse_risk(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    time_ref = ms.ref.time_dimension("sales.orders.occurred_at")
    before = catalog.readiness(refs=[time_ref]).to_dict()
    result = catalog.preview(time_ref, scope=default_scope())
    after = catalog.readiness(refs=[time_ref]).to_dict()

    assert [warning.kind for warning in result.warnings] == ["time_parse_risk"]
    before.pop("checked_at")
    after.pop("checked_at")
    assert after == before
    readiness = catalog.readiness(refs=[time_ref])
    assert "time_parse_risk" not in {
        issue.kind for issue in (*readiness.blockers, *readiness.warnings)
    }


def test_multi_entity_preview_requires_exact_complete_scope_pairs(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    net_revenue = ms.ref.relationship("sales.order_refund")
    orders = ms.ref.entity("sales.orders")
    refunds = ms.ref.entity("sales.refunds")

    with pytest.raises(SemanticRuntimeError, match="requires an Entity symbol/scope pair sequence"):
        catalog.preview(net_revenue, scope=default_scope())
    with pytest.raises(SemanticRuntimeError, match=r"native Entity lookup symbols"):
        catalog.preview(net_revenue, scope=(("sales.orders", default_scope()),))  # type: ignore[dict-item]
    with pytest.raises(SemanticRuntimeError, match="cover exactly"):
        catalog.preview(net_revenue, scope=((orders, default_scope()),))
    with pytest.raises(SemanticRuntimeError, match="cover exactly"):
        catalog.preview(
            net_revenue,
            scope=(
                (orders, default_scope()),
                (refunds, default_scope()),
                (ms.ref.entity("sales.other"), default_scope()),
            ),
        )

    orders_scope = md.partition({"region": "east"}, max_rows=10, timeout_seconds=30)
    refunds_scope = md.unpruned(max_rows=1, timeout_seconds=15)
    result = catalog.preview(
        net_revenue,
        scope=((orders, orders_scope), (refunds, refunds_scope)),
    )
    assert result.status == "passed"
    assert result.coverage.scopes == (
        ("sales.orders", orders_scope),
        ("sales.refunds", refunds_scope),
    )


def test_batch_uses_complete_union_mapping_and_shared_entity_scope(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    orders = ms.ref.entity("sales.orders")
    refunds = ms.ref.entity("sales.refunds")

    shared = catalog.preview_many(
        [ms.ref.dimension("sales.orders.region"), ms.ref.measure("sales.orders.amount")],
        scope=default_scope(),
    )
    mixed = catalog.preview_many(
        [ms.ref.measure("sales.orders.amount"), ms.ref.relationship("sales.order_refund")],
        scope=((orders, default_scope()), (refunds, default_scope())),
    )

    assert shared.refs == ("sales.orders.region", "sales.orders.amount")
    assert mixed.refs == ("sales.orders.amount", "sales.order_refund")


def test_complete_batch_is_validated_before_connection(
    scoped_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, _root = scoped_catalog
    amount = catalog_value(catalog, "sales.orders.amount", kind="field")
    sales = catalog.get("domain:sales")

    class UnregisteredDomainEntry(DomainEntry):
        pass

    forged = UnregisteredDomainEntry(
        _ref=sales._ref,
        _details=sales._details,
        _catalog=catalog,
    )
    monkeypatch.setattr(
        "marivo.datasource.runtime.open_backend",
        lambda *args, **kwargs: pytest.fail("connection opened"),
    )

    with pytest.raises(SemanticRuntimeError, match="not a registered concrete"):
        catalog.preview_many([amount, forged], scope=default_scope())
    with pytest.raises(SemanticRuntimeError, match="duplicate"):
        catalog.preview_many([amount, ms.ref.measure("sales.orders.amount")], scope=default_scope())


def test_cross_datasource_preview_fails_before_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(tmp_path / 'warehouse.duckdb')!r})\n"
            ),
            "datasources/finance.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='finance', path={str(tmp_path / 'finance.duckdb')!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Data', default=True)\n"
            ),
            "sales/models.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms
                orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["order_id"])
                refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("finance"), source=md.table("refunds"), primary_key=["refund_id"])
                order_id = ms.dimension_column(name="order_id", entity=orders, column="order_id")
                refund_id = ms.dimension_column(name="refund_id", entity=refunds, column="refund_id")
                ms.relationship(name="order_refund", from_entity=orders, to_entity=refunds, keys=[ms.join_on(order_id, refund_id)])
                """
            ),
        }
    )
    catalog = SemanticCatalog(project)
    monkeypatch.setattr(
        "marivo.datasource.runtime.open_backend",
        lambda *args, **kwargs: pytest.fail("connection opened"),
    )

    with pytest.raises(SemanticRuntimeError, match="share one datasource"):
        catalog.preview(
            ms.ref.relationship("sales.order_refund"),
            scope=(
                (ms.ref.entity("sales.orders"), default_scope()),
                (ms.ref.entity("sales.refunds"), default_scope()),
            ),
        )


def test_json_source_bindings_use_native_entity_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(tmp_path / 'json.duckdb')!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Data', default=True)\n"
            ),
            "sales/events.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms
                events = ms.entity(
                    name="events",
                    datasource=ms.ref.datasource("warehouse"),
                    source=md.json(
                        "https://example.invalid/events",
                        columns={"event_id": "event_id"},
                        query_params={"start": md.source_param("start")},
                    ),
                )
                """
            ),
        }
    )
    catalog = SemanticCatalog(project)
    original_open_backend = datasource_runtime.open_backend
    monkeypatch.setattr(
        "marivo.datasource.runtime.open_backend",
        lambda *args, **kwargs: pytest.fail("connection opened"),
    )
    entity = ms.ref.entity("sales.events")

    with pytest.raises(SemanticRuntimeError, match="invalid"):
        catalog.preview(entity, scope=default_scope())
    with pytest.raises(SemanticRuntimeError, match=r"native Entity lookup symbols"):
        catalog.preview(
            entity,
            scope=default_scope(),
            source_bindings=(("sales.events", {"start": 1}),),  # type: ignore[dict-item]
        )

    invalid_bindings = (
        (((entity, {"start": 1}), (ms.ref.entity("sales.events"), {"start": 2})), "repeat entity"),
        (((entity, {"start": 1}), (ms.ref.entity("sales.extra"), {"start": 2})), "outside"),
        (((ms.ref.datasource("warehouse"), {"start": 1}),), "native Entity"),
        ({ref_factory.entity("sales.events"): {"start": 1}}, "pair sequence"),
        (((entity, {}),), "invalid"),
    )
    for bindings, message in invalid_bindings:
        with pytest.raises(SemanticRuntimeError, match=message):
            catalog.preview(entity, scope=default_scope(), source_bindings=bindings)

    captured: dict[str, object] = {}

    def fake_read_json_source(backend, source, *, source_params=None):
        captured["params"] = source_params
        start = source_params["start"]
        return backend.create_table(
            f"scoped_preview_fake_json_{start}",
            obj=pa.table({"event_id": [start, start + 1]}),
            temp=True,
        )

    monkeypatch.setattr("marivo.datasource.runtime.open_backend", original_open_backend)
    monkeypatch.setattr("marivo.datasource.json_source.read_json_source", fake_read_json_source)
    result = catalog.preview(
        entity,
        scope=default_scope(),
        source_bindings=((entity, {"start": 1}),),
    )
    assert result.returned_row_count == 2
    assert captured == {"params": {"start": 1}}
    second = catalog.preview(
        entity,
        scope=default_scope(),
        source_bindings=((entity, {"start": 2}),),
    )
    assert [row["event_id"] for row in second.rows] == [2, 3]
    assert captured == {"params": {"start": 2}}


def test_temporal_artifact_certification_and_failed_previews(
    tmp_path: Path,
    semantic_project_factory,
) -> None:
    catalog = certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    calendar_ref = ms.ref.period_calendar("sales.fiscal")
    with pytest.raises(SemanticRuntimeError, match="exhaustive explicit scope"):
        catalog.preview(calendar_ref, scope=default_scope(max_rows=3), limit=1)
    assert (
        catalog_entry(
            catalog, as_ref(calendar_ref).path, kind="period_calendar"
        ).definition.snapshot_status
        == "missing"
    )

    for artifact_ref, missing_kind, max_rows in (
        (calendar_ref, "period_calendar_artifact_missing", 4),
        (
            ms.ref.temporal_set("sales.named_campaigns"),
            "temporal_set_artifact_missing",
            2,
        ),
    ):
        before = catalog.readiness(refs=[artifact_ref])
        assert missing_kind in {issue.kind for issue in before.blockers}
        preview = catalog.preview(artifact_ref, scope=default_scope(max_rows=max_rows), limit=1)
        assert preview.returned_row_count == 1
        assert preview.is_truncated is True
        assert preview.coverage.scope_exhaustion == "exhaustive"
        assert preview.coverage.scope_exactness == "scope_exact"
        card = preview.render()
        assert "preview_limit: 1" in card
        assert "preview_rows_truncated: true" in card
        assert "scope_coverage: exhaustive/scope_exact" in card
        assert "query_truncated" not in card
        assert catalog.get(as_ref(artifact_ref).key).definition.snapshot_status == "current"
        assert missing_kind not in {
            issue.kind for issue in catalog.readiness(refs=[artifact_ref]).blockers
        }

    assert catalog.get("period_calendar:sales.fiscal").period("week", "W2").start == date(
        2026, 1, 3
    )
    assert catalog.get("temporal_set:sales.named_campaigns").occurrence("incident").start == date(
        2026, 3, 3
    )

    with pytest.raises(SemanticRuntimeError) as invalid_limit:
        catalog.preview(calendar_ref, scope=default_scope(max_rows=4), limit=0)
    assert invalid_limit.value.expected == "an integer preview limit from 1 to 100 inclusive"
    assert invalid_limit.value.details["query_executed"] is False
    assert (
        catalog_entry(
            catalog, as_ref(calendar_ref).path, kind="period_calendar"
        ).definition.snapshot_status
        == "current"
    )


def test_calendar_lookup_repairs_resolve_collection_paths_and_help(
    tmp_path: Path,
    semantic_project_factory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog = certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    calendar = catalog.get("period_calendar:sales.fiscal")

    def repaired_calendar(error: SemanticRuntimeError, operation: str) -> PeriodCalendarEntry:
        assert error.kind == "not_found"
        assert error.details["operation"] == operation
        assert error.repair is not None
        match = re.search(r"catalog\.get\('([^']+)'\)", error.repair.action)
        assert match is not None
        assert match.group(1) == "period_calendar:sales.fiscal"
        entry = catalog.get(match.group(1))
        assert entry is calendar
        marivo.help(error.repair.help_target.display)
        assert "Kind: callable" in capsys.readouterr().out
        return entry

    with pytest.raises(SemanticRuntimeError) as missing:
        calendar.period_on("week", date(2026, 1, 3))
    repair_entry = repaired_calendar(missing.value, "snapshot")
    assert missing.value.details["snapshot_status"] == "missing"
    catalog.preview(repair_entry, scope=default_scope(max_rows=4))
    period = calendar.period_on("week", date(2026, 1, 3))
    assert (period.start, period.end) == (date(2026, 1, 3), date(2026, 1, 5))

    for operation, lookup in (
        ("grain", lambda: calendar.grain("unknown")),
        ("period", lambda: calendar.period("week", "unknown")),
        ("period_on", lambda: calendar.period_on("week", date(2026, 1, 5))),
        ("periods", lambda: calendar.periods("unknown")),
    ):
        with pytest.raises(SemanticRuntimeError) as invalid:
            lookup()
        assert repaired_calendar(invalid.value, operation).definition.snapshot_status == "current"


def test_scoped_temporal_navigation_does_not_inspect_certified_snapshots(
    tmp_path: Path, semantic_project_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )

    def reject_snapshot(_entry: object) -> None:
        pytest.fail("static navigation inspected a certified snapshot")

    for entry_type in (PeriodCalendarEntry, TemporalSetEntry):
        monkeypatch.setattr(entry_type, "_snapshot_with_status", reject_snapshot)
    for kind, identity in (
        ("period_calendar", "period_calendar:sales.fiscal"),
        ("temporal_set", "temporal_set:sales.named_campaigns"),
    ):
        page = catalog.search(kind=kind, domain="sales")
        assert len(page.items) == 1
        assert page.items[0].id == identity
        assert catalog.get(identity) is page.items[0]


@pytest.mark.parametrize(
    ("kind", "path", "max_rows"),
    (
        (ms.SemanticKind.PERIOD_CALENDAR, "sales.fiscal", 4),
        (ms.SemanticKind.TEMPORAL_SET, "sales.named_campaigns", 2),
    ),
)
def test_temporal_details_observe_state_changes_after_navigation(
    tmp_path: Path, semantic_project_factory, kind: ms.SemanticKind, path: str, max_rows: int
) -> None:
    catalog = certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    entry = catalog.get(f"{kind.value}:{path}")
    if isinstance(entry, PeriodCalendarEntry):
        assert entry.definition.source_date == "time_dimension:sales.calendar.calendar_date"
        assert not hasattr(entry.definition, "date")
    assert entry.definition.snapshot_status == "missing"
    catalog.preview(entry.ref, scope=default_scope(max_rows=max_rows), limit=1)
    assert catalog.get(entry.id) is entry
    assert entry.definition.snapshot_status == "current"

    manifests = tuple((catalog.workspace_dir / ".marivo" / "temporal").rglob("current.json"))
    assert len(manifests) == 1
    manifest_path = manifests[0]
    current = manifest_path.read_text()
    manifest = json.loads(current)
    manifest["definition_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    assert entry.definition.snapshot_status == "stale"
    manifest_path.write_text("invalid json")
    assert entry.definition.snapshot_status == "invalid"
    manifest_path.unlink()
    assert entry.definition.snapshot_status == "missing"
    manifest_path.write_text(current)
    assert entry.definition.snapshot_status == "current"


@pytest.mark.parametrize("invalid_source", ("timestamp", "empty"))
def test_native_calendar_certification_requires_date_schema_and_nonempty_coverage(
    tmp_path: Path,
    semantic_project_factory,
    invalid_source: str,
) -> None:
    catalog = certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    backend = ibis.duckdb.connect(str(tmp_path / "calendar.duckdb"))
    try:
        if invalid_source == "timestamp":
            backend.raw_sql("ALTER TABLE calendar ALTER COLUMN calendar_date TYPE TIMESTAMP")
        else:
            backend.raw_sql("DELETE FROM calendar")
    finally:
        backend.disconnect()
    calendar = catalog.get("period_calendar:sales.fiscal")
    with pytest.raises(SemanticRuntimeError) as caught:
        catalog.preview(calendar.ref, scope=default_scope(max_rows=4))
    assert caught.value.kind == "materialize_failed"
    assert calendar.definition.snapshot_status == "missing"
    if invalid_source == "timestamp":
        assert (
            caught.value.expected == "a native DATE field or an explicitly encoded civil-date field"
        )
        assert caught.value.details["query_executed"] is False
    else:
        assert "completely cover" in caught.value.message
