"""Snapshot-independent semantic preview and artifact certification."""

from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.preview import PreviewLimitError
from marivo.semantic.catalog import MetricEntry, SemanticCatalog
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
                """\
                import marivo.datasource as md
                import marivo.semantic as ms

                orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
                refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("warehouse"), source=md.table("refunds"))
                region = ms.dimension_column(name="region", entity=orders, column="region")
                order_id = ms.dimension_column(name="order_id", entity=orders, column="order_id")
                refund_id = ms.dimension_column(name="refund_id", entity=refunds, column="refund_id")
                occurred_at = ms.time_dimension_column(name="occurred_at", entity=orders, column="ts", granularity="hour")
                @ms.measure(entity=orders, additivity="additive", unit="USD")
                def amount(orders):
                    return orders.amount
                @ms.metric(entities=[orders], additivity="additive")
                def revenue(orders):
                    return orders.amount.sum()
                @ms.metric(entities=[orders, refunds], root_entity=orders, additivity="additive")
                def net_revenue(orders, refunds):
                    return orders.amount.sum()
                """
            ),
        }
    )
    monkeypatch.chdir(tmp_path)
    query_spy.user_data_queries = 0
    query_spy.sql.clear()
    return SemanticCatalog(project), tmp_path


def _scope(*, max_rows: int = 100) -> md.AuthoringScope:
    return md.unpruned(max_rows=max_rows, timeout_seconds=30)


def test_preview_requires_explicit_scope(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    with pytest.raises(TypeError):
        catalog.preview(ms.ref.metric("sales.revenue"))  # type: ignore[call-arg]


def test_preview_entry_and_ref_have_equivalent_current_results(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    revenue = catalog.metrics.get("sales.revenue")

    by_entry = catalog.preview(revenue, scope=_scope())
    by_ref = catalog.preview(revenue.ref, scope=_scope())

    assert by_entry.rows == by_ref.rows == ({"value": 60.0},)
    assert by_entry.coverage.scopes == (("sales.orders", _scope()),)
    assert by_entry.coverage.scope_exactness == "sample_only"
    assert not hasattr(by_entry.coverage, "snapshot_ids")
    assert not hasattr(by_entry.coverage, "cache_status")


def test_ordinary_preview_ignores_history_and_writes_no_check(scoped_catalog) -> None:
    catalog, root = scoped_catalog
    check_dir = root / ".marivo" / "authoring" / "checks"
    check_dir.mkdir(parents=True)
    historical = check_dir / "historical.json"
    historical.write_text('{"old": true}', encoding="utf-8")

    before = catalog.readiness(refs=[ms.ref.metric("sales.revenue")]).to_dict()
    result = catalog.preview(ms.ref.metric("sales.revenue"), scope=_scope(max_rows=2))
    after = catalog.readiness(refs=[ms.ref.metric("sales.revenue")]).to_dict()

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
    result = catalog.preview(ms.ref.entity("sales.orders"), scope=_scope(max_rows=2), limit=20)

    assert result.returned_row_count == 2
    assert result.is_truncated is True
    assert result.coverage.rows_observed == 3
    assert result.coverage.scope_exhaustion == "truncated"
    assert result.coverage.scope_exactness == "sample_only"


def test_time_dimension_reports_native_naive_parse_risk(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    time_ref = ms.ref.time_dimension("sales.orders.occurred_at")
    before = catalog.readiness(refs=[time_ref]).to_dict()
    result = catalog.preview(time_ref, scope=_scope())
    after = catalog.readiness(refs=[time_ref]).to_dict()

    assert [warning.kind for warning in result.warnings] == ["time_parse_risk"]
    before.pop("checked_at")
    after.pop("checked_at")
    assert after == before
    readiness = catalog.readiness(refs=[time_ref])
    assert "time_parse_risk" not in {
        issue.kind for issue in (*readiness.blockers, *readiness.warnings)
    }


def test_multi_entity_preview_requires_exact_complete_scope_mapping(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    net_revenue = ms.ref.metric("sales.net_revenue")
    orders = ms.ref.entity("sales.orders")
    refunds = ms.ref.entity("sales.refunds")

    with pytest.raises(SemanticRuntimeError, match="requires a Mapping"):
        catalog.preview(net_revenue, scope=_scope())
    with pytest.raises(SemanticRuntimeError, match=r"exact Ref\[entity\] keys"):
        catalog.preview(net_revenue, scope={"sales.orders": _scope()})  # type: ignore[dict-item]
    with pytest.raises(SemanticRuntimeError, match="cover exactly"):
        catalog.preview(net_revenue, scope={orders: _scope()})
    with pytest.raises(SemanticRuntimeError, match="cover exactly"):
        catalog.preview(
            net_revenue,
            scope={orders: _scope(), refunds: _scope(), ms.ref.entity("sales.other"): _scope()},
        )

    orders_scope = md.partition({"region": "east"}, max_rows=10, timeout_seconds=30)
    refunds_scope = md.unpruned(max_rows=1, timeout_seconds=15)
    result = catalog.preview(
        net_revenue,
        scope={orders: orders_scope, refunds: refunds_scope},
    )
    assert result.rows == ({"value": 40.0},)
    assert result.coverage.scopes == (
        ("sales.orders", orders_scope),
        ("sales.refunds", refunds_scope),
    )


def test_batch_uses_complete_union_mapping_and_shared_entity_scope(scoped_catalog) -> None:
    catalog, _root = scoped_catalog
    orders = ms.ref.entity("sales.orders")
    refunds = ms.ref.entity("sales.refunds")

    shared = catalog.preview_many(
        [ms.ref.dimension("sales.orders.region"), ms.ref.metric("sales.revenue")],
        scope=_scope(),
    )
    mixed = catalog.preview_many(
        [ms.ref.metric("sales.revenue"), ms.ref.metric("sales.net_revenue")],
        scope={orders: _scope(), refunds: _scope()},
    )

    assert shared.refs == ("sales.orders.region", "sales.revenue")
    assert mixed.refs == ("sales.revenue", "sales.net_revenue")


def test_complete_batch_is_validated_before_connection(
    scoped_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, _root = scoped_catalog
    revenue = catalog.metrics.get("sales.revenue")

    class UnregisteredMetricEntry(MetricEntry):
        pass

    forged = UnregisteredMetricEntry(
        ref=revenue.ref,
        _details=revenue.details(),
        _catalog=catalog,
    )
    monkeypatch.setattr(
        catalog._project,
        "_connection_service",
        lambda: pytest.fail("connection opened"),
    )

    with pytest.raises(SemanticRuntimeError, match="not a registered concrete"):
        catalog.preview_many([revenue, forged], scope=_scope())
    with pytest.raises(SemanticRuntimeError, match="duplicate"):
        catalog.preview_many([revenue, revenue.ref], scope=_scope())


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
                orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
                refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("finance"), source=md.table("refunds"))
                @ms.metric(entities=[orders, refunds], root_entity=orders, additivity="additive")
                def net_revenue(orders, refunds):
                    return orders.amount.sum()
                """
            ),
        }
    )
    catalog = SemanticCatalog(project)
    monkeypatch.setattr(
        catalog._project,
        "_connection_service",
        lambda: pytest.fail("connection opened"),
    )

    with pytest.raises(SemanticRuntimeError, match="share one datasource"):
        catalog.preview(
            ms.ref.metric("sales.net_revenue"),
            scope={
                ms.ref.entity("sales.orders"): _scope(),
                ms.ref.entity("sales.refunds"): _scope(),
            },
        )


def test_json_source_bindings_are_exact_entity_ref_mappings(
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
                        schema={"event_id": "int64"},
                        query_params={"start": md.source_param("start")},
                    ),
                )
                """
            ),
        }
    )
    catalog = SemanticCatalog(project)
    original_connection_service = catalog._project._connection_service
    monkeypatch.setattr(
        catalog._project,
        "_connection_service",
        lambda: pytest.fail("connection opened"),
    )
    entity = ms.ref.entity("sales.events")

    with pytest.raises(SemanticRuntimeError, match="invalid"):
        catalog.preview(entity, scope=_scope())
    with pytest.raises(SemanticRuntimeError, match=r"exact Ref\[entity\] keys"):
        catalog.preview(
            entity,
            scope=_scope(),
            source_bindings={"sales.events": {"start": 1}},  # type: ignore[dict-item]
        )

    captured: dict[str, object] = {}

    def fake_read_json_source(backend, source, *, source_params=None):
        captured["params"] = source_params
        return ibis.memtable({"event_id": [1, 2]})

    monkeypatch.setattr(catalog._project, "_connection_service", original_connection_service)
    monkeypatch.setattr("marivo.semantic.materializer.read_json_source", fake_read_json_source)
    result = catalog.preview(
        entity,
        scope=_scope(),
        source_bindings={entity: {"start": 1}},
    )
    assert result.returned_row_count == 2
    assert captured == {"params": {"start": 1}}


def _certified_project(
    *,
    tmp_path: Path,
    semantic_project_factory,
) -> SemanticCatalog:
    database_path = tmp_path / "calendar.duckdb"
    backend = ibis.duckdb.connect(str(database_path))
    backend.raw_sql("CREATE TABLE calendar (calendar_date DATE, week TEXT, is_working BOOLEAN)")
    backend.raw_sql(
        "INSERT INTO calendar VALUES "
        "(DATE '2026-01-01', 'W1', false), "
        "(DATE '2026-01-02', 'W1', true), "
        "(DATE '2026-01-03', 'W2', true), "
        "(DATE '2026-01-04', 'W2', false)"
    )
    backend.raw_sql(
        "CREATE TABLE campaigns (campaign_id TEXT, starts DATE, ends DATE, category TEXT)"
    )
    backend.raw_sql(
        "INSERT INTO campaigns VALUES "
        "('spring', DATE '2026-03-01', DATE '2026-03-04', 'promotion'), "
        "('incident', DATE '2026-03-03', DATE '2026-03-05', 'incident')"
    )
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
            "sales/time.py": textwrap.dedent(
                """\
                import marivo.datasource as md
                import marivo.semantic as ms
                calendar = ms.entity(name="calendar", datasource=ms.ref.datasource("warehouse"), source=md.table("calendar"))
                calendar_date = ms.time_dimension_column(name="calendar_date", entity=calendar, column="calendar_date", granularity="day")
                week = ms.dimension_column(name="week", entity=calendar, column="week")
                is_working = ms.dimension_column(name="is_working", entity=calendar, column="is_working")
                fiscal = ms.period_calendar(name="fiscal", date=calendar_date, boundary_timezone="UTC", coverage=(__import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 5)), levels={"week": week})
                schedule = ms.work_schedule(name="schedule", date=calendar_date, is_working=is_working, boundary_timezone="UTC", coverage=(__import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 5)))
                campaigns = ms.entity(name="campaigns", datasource=ms.ref.datasource("warehouse"), source=md.table("campaigns"))
                campaign_id = ms.dimension_column(name="campaign_id", entity=campaigns, column="campaign_id")
                category = ms.dimension_column(name="category", entity=campaigns, column="category")
                starts = ms.time_dimension_column(name="starts", entity=campaigns, column="starts", granularity="day")
                ends = ms.time_dimension_column(name="ends", entity=campaigns, column="ends", granularity="day")
                named_campaigns = ms.temporal_set(name="named_campaigns", occurrence_id=campaign_id, start=starts, end=ends, category=category, boundary_timezone="UTC", coverage=(__import__("datetime").date(2026, 1, 1), __import__("datetime").date(2027, 1, 1)))
                """
            ),
        }
    )
    return SemanticCatalog(project)


@pytest.mark.parametrize(
    ("artifact_ref", "missing_kind", "max_rows"),
    (
        (ms.ref.period_calendar("sales.fiscal"), "period_calendar_artifact_missing", 4),
        (ms.ref.temporal_set("sales.named_campaigns"), "temporal_set_artifact_missing", 2),
        (ms.ref.work_schedule("sales.schedule"), "work_schedule_artifact_missing", 4),
    ),
)
def test_explicit_scope_certifies_all_temporal_artifacts(
    tmp_path: Path,
    semantic_project_factory,
    artifact_ref,
    missing_kind: str,
    max_rows: int,
) -> None:
    catalog = _certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    before = catalog.readiness(refs=[artifact_ref])
    assert missing_kind in {issue.kind for issue in before.blockers}

    preview = catalog.preview(artifact_ref, scope=_scope(max_rows=max_rows), limit=1)

    assert preview.returned_row_count == 1
    assert catalog.require(artifact_ref).details().snapshot_status == "current"
    assert missing_kind not in {
        issue.kind for issue in catalog.readiness(refs=[artifact_ref]).blockers
    }


def test_truncated_certification_does_not_publish_artifact(
    tmp_path: Path, semantic_project_factory
) -> None:
    catalog = _certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    calendar_ref = ms.ref.period_calendar("sales.fiscal")

    with pytest.raises(SemanticRuntimeError, match="exhaustive explicit scope"):
        catalog.preview(calendar_ref, scope=_scope(max_rows=3), limit=1)

    assert catalog.period_calendars.get(calendar_ref).details().snapshot_status == "missing"


def test_invalid_display_limit_does_not_replace_current_artifact(
    tmp_path: Path, semantic_project_factory
) -> None:
    catalog = _certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    calendar_ref = ms.ref.period_calendar("sales.fiscal")
    catalog.preview(calendar_ref, scope=_scope(max_rows=4))

    with pytest.raises(PreviewLimitError):
        catalog.preview(calendar_ref, scope=_scope(max_rows=4), limit=0)

    assert catalog.period_calendars.get(calendar_ref).details().snapshot_status == "current"
