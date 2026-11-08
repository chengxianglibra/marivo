"""Snapshot-independent semantic preview and artifact certification."""

from __future__ import annotations

import textwrap
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ibis_ir
import pyarrow as pa
import pytest

import marivo
import marivo.datasource as md
from marivo.datasource import runtime as datasource_runtime
from marivo.datasource.errors import DatasourceAuthoringError
from marivo.datasource.ir import (
    DatasourceIR,
    JsonSourceIR,
    QueryParamScalar,
    QueryParamScalarList,
)
from marivo.datasource.json_source import JsonResponseCache
from marivo.preview import PreviewResult
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import (
    Domain,
    SemanticCatalog,
)
from marivo.semantic.errors import SemanticRuntimeError
from tests.semantic.preview_helpers import default_scope
from tests.shared_fixtures import catalog_entry, catalog_value

if TYPE_CHECKING:
    from tests.conftest import _SemanticProjectFactory


@pytest.fixture
def scoped_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: _SemanticProjectFactory,
) -> tuple[SemanticCatalog, Path]:
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
                f"md.duckdb(name='warehouse', wall_clock_timezone='UTC', path={str(database_path)!r})\n"
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
                other = ms.entity(name="other", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
                region = ms.dimension_column(name="region", entity=orders, column="region")
                order_id = ms.dimension_column(name="order_id", entity=orders, column="order_id")
                refund_id = ms.dimension_column(name="refund_id", entity=refunds, column="refund_id")
                occurred_at = ms.time_dimension_column(name="occurred_at", entity=orders, column="ts")
                amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="USD")
                ms.relationship(name="order_refund", from_entity=orders, to_entity=refunds, keys=[ms.join_on(order_id, refund_id)])

                refund_amount = ms.measure_column(name="amount", entity=refunds, column="amount", additivity=ms.additive_all(), unit="USD")
                amount = amount_ref
                @ms.metric()
                def net_revenue():
                    return amount.sum() - refund_amount.sum()

                @ms.metric()
                def revenue():
                    return amount.sum()
                """
            ),
        }
    )
    monkeypatch.chdir(tmp_path)
    return SemanticCatalog(project), tmp_path


@pytest.mark.parametrize("operation", ("preview", "preview_many"))
@pytest.mark.parametrize("limit", (0, 101, "20", True))
def test_invalid_preview_limits_are_structured_before_connection(
    scoped_catalog: tuple[SemanticCatalog, Path],
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
    assert error.repair is not None
    assert "limit=20" in error.repair.action
    assert error.repair.help_target.display == f"semantic.{operation}"
    rendered = str(error)
    marivo.help(error.repair.help_target.display)
    help_text = capsys.readouterr().out


def test_preview_get_and_search_have_equivalent_current_results(
    scoped_catalog: tuple[SemanticCatalog, Path],
) -> None:
    catalog, _root = scoped_catalog
    amount = catalog_value(catalog, "sales.orders.amount", kind="field")

    by_value = catalog.preview(amount, scope=default_scope())
    by_ref = catalog.preview(catalog.search(kind="measure").items[0], scope=default_scope())

    assert by_value.rows == by_ref.rows
    amounts = [row["amount"] for row in by_value.rows]
    assert all(isinstance(amount, float) for amount in amounts)
    assert sorted(amount for amount in amounts if isinstance(amount, float)) == [10.0, 20.0, 30.0]
    assert by_value.coverage.scopes == (("sales.orders", default_scope()),)


def test_published_metric_preview_uses_bounded_scope(
    scoped_catalog: tuple[SemanticCatalog, Path],
) -> None:
    catalog, _root = scoped_catalog
    for value in (
        catalog.get("metric:sales.revenue"),
        next(
            item
            for item in catalog.search(kind="metric").items
            if item.id == "metric:sales.revenue"
        ),
    ):
        preview = catalog.preview(value, scope=default_scope())
        assert preview.kind == "semantic_metric"
        assert preview.coverage.scopes == (("sales.orders", default_scope()),)
        assert preview.sample_policy.method == "pre_aggregate_limit"


def test_ordinary_preview_ignores_history_and_writes_no_check(
    scoped_catalog: tuple[SemanticCatalog, Path],
) -> None:
    catalog, _root = scoped_catalog
    amount = catalog.get("measure:sales.orders.amount")
    result = catalog.preview(amount, scope=default_scope(max_rows=3))

    assert result.status == "passed"


def test_nonpartition_column_rejects_and_half_open_time_range_is_applied(
    scoped_catalog: tuple[SemanticCatalog, Path],
) -> None:
    catalog, _root = scoped_catalog

    with pytest.raises(DatasourceAuthoringError):
        catalog.preview(
            catalog_value(catalog, "sales.orders", kind="entity"),
            scope=md.partition({"region": "east"}, max_rows=10, timeout_seconds=30),
        )
    ranged = catalog.preview(
        catalog_value(catalog, "sales.orders", kind="entity"),
        scope=md.time_range(
            "ts",
            start=datetime(2026, 7, 10),
            end=datetime(2026, 7, 11),
            max_rows=10,
            timeout_seconds=30,
        ),
    )

    assert [row["order_id"] for row in ranged.rows] == [1, 2]


def test_scope_row_guard_reports_real_truncation(
    scoped_catalog: tuple[SemanticCatalog, Path],
) -> None:
    catalog, _root = scoped_catalog
    result = catalog.preview(
        catalog_value(catalog, "sales.orders", kind="entity"),
        scope=default_scope(max_rows=2),
        limit=20,
    )

    assert result.returned_row_count == 2
    assert result.is_truncated is True
    assert result.coverage.rows_observed == 3
    assert result.coverage.scope_exhaustion == "truncated"
    assert result.coverage.scope_exactness == "sample_only"


def test_time_dimension_reports_declared_native_naive_authority(
    scoped_catalog: tuple[SemanticCatalog, Path],
) -> None:
    catalog, _root = scoped_catalog
    time_ref = catalog.get("time_dimension:sales.orders.occurred_at")
    before = catalog.readiness(refs=[time_ref]).to_dict()
    result = catalog.preview(time_ref, scope=default_scope())
    after = catalog.readiness(refs=[time_ref]).to_dict()

    assert result.warnings == ()
    assert result.timezones["occurred_at"] == {
        "kind": "wall_clock",
        "timezone": "UTC",
        "origin": "datasource",
        "inherited_from": None,
    }
    before.pop("checked_at")
    after.pop("checked_at")
    assert after == before
    readiness = catalog.readiness(refs=[time_ref])


def test_multi_entity_preview_requires_exact_complete_scope_pairs(
    scoped_catalog: tuple[SemanticCatalog, Path],
) -> None:
    catalog, _root = scoped_catalog
    net_revenue = catalog.get("metric:sales.net_revenue")
    orders = catalog_value(catalog, "sales.orders", kind="entity")
    refunds = catalog_value(catalog, "sales.refunds", kind="entity")

    with pytest.raises(SemanticRuntimeError):
        catalog.preview(net_revenue, scope=default_scope())
    with pytest.raises(SemanticRuntimeError):
        catalog.preview(net_revenue, scope=(("sales.orders", default_scope()),))  # type: ignore[arg-type]  # Exercise an invalid pair.
    with pytest.raises(SemanticRuntimeError):
        catalog.preview(net_revenue, scope=((orders, default_scope()),))
    with pytest.raises(SemanticRuntimeError):
        catalog.preview(
            net_revenue,
            scope=(
                (orders, default_scope()),
                (refunds, default_scope()),
                (catalog_value(catalog, "sales.other", kind="entity"), default_scope()),
            ),
        )

    orders_scope = md.time_range(
        "ts", start="2026-07-10", end="2026-07-11", max_rows=10, timeout_seconds=30
    )
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


def test_batch_uses_complete_union_mapping_and_shared_entity_scope(
    scoped_catalog: tuple[SemanticCatalog, Path],
) -> None:
    catalog, _root = scoped_catalog
    orders = catalog_value(catalog, "sales.orders", kind="entity")
    refunds = catalog_value(catalog, "sales.refunds", kind="entity")

    shared = catalog.preview_many(
        [catalog.get("dimension:sales.orders.region"), catalog.get("measure:sales.orders.amount")],
        scope=default_scope(),
    )
    mixed = catalog.preview_many(
        [catalog.get("measure:sales.orders.amount"), catalog.get("metric:sales.net_revenue")],
        scope=((orders, default_scope()), (refunds, default_scope())),
    )

    assert shared.refs == ("sales.orders.region", "sales.orders.amount")
    assert mixed.refs == ("sales.orders.amount", "sales.net_revenue")


def test_complete_batch_is_validated_before_connection(
    scoped_catalog: tuple[SemanticCatalog, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, _root = scoped_catalog
    amount = catalog_value(catalog, "sales.orders.amount", kind="field")
    sales = catalog_entry(catalog, "sales", kind="domain")

    class UnregisteredDomain(Domain):
        pass

    forged = UnregisteredDomain(_ref=sales._ref, _details=sales._details, _catalog=catalog)
    monkeypatch.setattr(
        "marivo.datasource.runtime.open_backend",
        lambda *args, **kwargs: pytest.fail("connection opened"),
    )
    with pytest.raises(SemanticRuntimeError):
        catalog.preview_many([amount, forged], scope=default_scope())
    with pytest.raises(SemanticRuntimeError):
        catalog.preview_many(
            [amount, catalog.get("measure:sales.orders.amount")], scope=default_scope()
        )


def test_cross_datasource_preview_fails_before_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', wall_clock_timezone='UTC', path={str(tmp_path / 'warehouse.duckdb')!r})\n",
            "datasources/finance.py": f"import marivo.datasource as md\nmd.duckdb(name='finance', path={str(tmp_path / 'finance.duckdb')!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/models.py": textwrap.dedent(
                '                import marivo.datasource as md\n                import marivo.semantic as ms\n                orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["order_id"])\n                refunds = ms.entity(name="refunds", datasource=ms.ref.datasource("finance"), source=md.table("refunds"), primary_key=["refund_id"])\n                order_id = ms.dimension_column(name="order_id", entity=orders, column="order_id")\n                refund_id = ms.dimension_column(name="refund_id", entity=refunds, column="refund_id")\n                ms.relationship(name="order_refund", from_entity=orders, to_entity=refunds, keys=[ms.join_on(order_id, refund_id)])\n                @ms.metric()\n                def both_counts():\n                    return orders.count() + refunds.count()\n                '
            ),
        }
    )
    catalog = SemanticCatalog(project)
    monkeypatch.setattr(
        "marivo.datasource.runtime.open_backend",
        lambda *args, **kwargs: pytest.fail("connection opened"),
    )
    with pytest.raises(SemanticRuntimeError):
        catalog.preview(
            catalog.get("metric:sales.both_counts"),
            scope=(
                (catalog_value(catalog, "sales.orders", kind="entity"), default_scope()),
                (catalog_value(catalog, "sales.refunds", kind="entity"), default_scope()),
            ),
        )


def test_json_source_bindings_use_native_entity_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', wall_clock_timezone='UTC', path={str(tmp_path / 'json.duckdb')!r})\n"
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
                extra = ms.entity(name="extra", datasource=ms.ref.datasource("warehouse"), source=md.table("extra"))
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
    entity = catalog_value(catalog, "sales.events", kind="entity")

    with pytest.raises(SemanticRuntimeError):
        catalog.preview(entity, scope=default_scope())
    with pytest.raises(SemanticRuntimeError):
        catalog.preview(
            entity,
            scope=default_scope(),
            source_bindings=(("sales.events", {"start": 1}),),  # type: ignore[arg-type]  # Exercise an invalid pair.
        )

    invalid_bindings: tuple[tuple[object, str], ...] = (
        (
            (
                (entity, {"start": 1}),
                (catalog_value(catalog, "sales.events", kind="entity"), {"start": 2}),
            ),
            "repeat entity",
        ),
        (
            (
                (entity, {"start": 1}),
                (catalog_value(catalog, "sales.extra", kind="entity"), {"start": 2}),
            ),
            "outside",
        ),
        (((catalog.get("datasource:warehouse"), {"start": 1}),), "semantic kind datasource"),
        ({ref_factory.entity("sales.events"): {"start": 1}}, "pair sequence"),
        (((entity, {}),), "invalid"),
    )
    for bindings, message in invalid_bindings:
        with pytest.raises(SemanticRuntimeError):
            catalog.preview(entity, scope=default_scope(), source_bindings=bindings)  # type: ignore[arg-type]  # Invalid binding forms.

    captured: dict[str, object] = {}

    def fake_read_json_source(
        backend: ibis.duckdb.DuckDBBackend,
        source: JsonSourceIR,
        *,
        source_params: Mapping[str, QueryParamScalar | QueryParamScalarList] | None = None,
        responses: JsonResponseCache | None = None,
    ) -> ibis_ir.Table:
        assert responses is None
        captured["params"] = source_params
        assert source_params is not None
        start = source_params["start"]
        assert isinstance(start, int)
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


def test_metric_input_cap_is_exact_and_disclosed(
    scoped_catalog: tuple[SemanticCatalog, Path],
) -> None:
    catalog, _root = scoped_catalog
    result = catalog.preview(catalog.get("metric:sales.revenue"), scope=default_scope(max_rows=1))
    assert result.coverage.entity_rows == (("sales.orders", 1),)
    assert result.coverage.entity_row_limits == (("sales.orders", 1),)
    assert result.coverage.scope_exhaustion == "unknown"
    assert result.coverage.scope_exactness == "sample_only"
    assert result.rows[0]["value"] in (10, 20, 30)
    assert result.execution_limits is not None
    assert not result.execution_limits.timeout_enforced


def test_metric_input_hard_cap_does_not_count_full_source(
    scoped_catalog: tuple[SemanticCatalog, Path],
) -> None:
    catalog, root = scoped_catalog
    backend = ibis.duckdb.connect(str(root / "warehouse.duckdb"))
    try:
        backend.raw_sql("DELETE FROM orders")
        backend.raw_sql(
            "INSERT INTO orders SELECT i, 1.0, 'east', TIMESTAMP '2026-07-10' FROM range(10001) t(i)"
        )
    finally:
        backend.disconnect()
    result = catalog.preview(
        catalog.get("metric:sales.revenue"), scope=default_scope(max_rows=20000)
    )
    assert result.rows == ({"value": 10000},)
    assert result.coverage.entity_rows == (("sales.orders", 10000),)
    assert result.coverage.entity_row_limits == (("sales.orders", 10000),)
    assert result.coverage.scope_exhaustion == "unknown"


@pytest.mark.parametrize("operation", ("preview", "preview_many"))
@pytest.mark.parametrize("pruned", (False, True))
def test_metric_preview_reuses_physical_partition_preflight(
    scoped_catalog: tuple[SemanticCatalog, Path],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    pruned: bool,
) -> None:
    from dataclasses import replace

    from marivo.datasource.inspection import SourceInspection, _inspect_resolved_source
    from marivo.datasource.metadata import PartitionMetadata

    catalog, _root = scoped_catalog

    def inspect_source(
        datasource_ir: DatasourceIR,
        source: md.TableSource,
        *,
        project_root: Path,
        connections: datasource_runtime.DatasourceConnectionService,
    ) -> SourceInspection:
        inspection = _inspect_resolved_source(
            datasource_ir, source, project_root=project_root, connections=connections
        )
        return replace(
            inspection,
            partitioning=replace(
                inspection.partitioning,
                state="known",
                fields=(PartitionMetadata(name="region", type="string"),),
                value_source="metadata",
            ),
        )

    monkeypatch.setattr("marivo.datasource.inspection._inspect_resolved_source", inspect_source)
    metric = catalog.get("metric:sales.revenue")
    scope = (
        md.partition({"region": "east"}, max_rows=10, timeout_seconds=30)
        if pruned
        else default_scope()
    )

    def preview() -> PreviewResult:
        return (
            catalog.preview(metric, scope=scope)
            if operation == "preview"
            else catalog.preview_many([metric], scope=scope).results[0]
        )

    if pruned:
        result = preview()
        assert result.rows == ({"value": 40},)
        assert result.coverage.entity_rows == (("sales.orders", 2),)
        assert result.coverage.scopes == (("sales.orders", scope),)
    else:
        monkeypatch.setattr(
            datasource_runtime.DatasourceConnectionService,
            "collect_source",
            lambda *args, **kwargs: pytest.fail("preflight read business data"),
        )
        with pytest.raises(DatasourceAuthoringError) as caught:
            preview()
        assert caught.value.code == "incomplete_partition_fields"
        assert caught.value.effect_observed is not None
        assert caught.value.effect_observed.query_executed is False
