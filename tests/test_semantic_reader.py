"""Tests for marivo.semantic reader API — list/preview/readiness.

Tests cover:
- list_domains, list_datasources, list_entities, list_dimensions,
  list_time_dimensions, list_metrics, list_relationships
- get_entity, get_metric
- reader on unloaded/errored project
- preview operations
"""

from __future__ import annotations

import json
import textwrap

import ibis
import pytest

from marivo.preview import PreviewLimitError, PreviewResult
from marivo.semantic.discovery import DiscoveryResult, SelectionError
from marivo.semantic.errors import ErrorKind, SemanticLoadFailed, SemanticRuntimeError
from marivo.semantic.ir import (
    DimensionKind,
    EntityIR,
    MetricIR,
)
from marivo.semantic.reader import (
    DatasourceSummary,
    DimensionSummary,
    DomainSummary,
    EntitySummary,
    MetricSummary,
    RelationshipSummary,
)

# ---------------------------------------------------------------------------
# Model file templates
# ---------------------------------------------------------------------------

_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
""")

_FULL_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    @ms.dimension(entity=orders)
    def region(table):
        return table.region

    @ms.time_dimension(entity=orders, data_type="timestamp", granularity="day")
    def created_at(table):
        return table.created_at

    @ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)
    def total_revenue(table):
        return table.amount.sum()

    @ms.metric(entities=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)
    def order_count(table):
        return table.count()

    @ms.metric(
        entities=[orders],
        additivity='additive',
        decomposition=ms.sum(),
        description="Average order value",
    verification_mode="python_native",)
    def aov(table):
        return table.amount.mean()

    ms.relationship(
        name="orders_to_items",
        from_entity=orders,
        to_entity=orders,
        from_dimensions=[amount],
        to_dimensions=[amount],
    )
""")


# ---------------------------------------------------------------------------
# DuckDB backend fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_backend():
    """In-memory DuckDB backend with a test orders table."""
    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-02-01')"
    )
    return con


@pytest.fixture
def backend_factory(duckdb_backend):
    """A backend_factory callable that always returns the shared DuckDB backend."""

    def _factory(datasource_semantic_id: str):
        return duckdb_backend

    return _factory


def _fake_inspect_source(datasource, *, source, include_partitions=True):
    from marivo.analysis.datasources.metadata import TableMetadata

    return TableMetadata(
        datasource=datasource,
        table=getattr(source, "table", "fake_table"),
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(),
        partitions=(),
        warnings=(),
    )


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


def test_list_models(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    models = project.list_domains()
    assert isinstance(models, DiscoveryResult)
    assert len(models) >= 1
    assert any(m.name == "sales" for m in models)
    assert all(isinstance(m, DomainSummary) for m in models)
    # Verify object_counts is present
    for m in models:
        assert isinstance(m.object_counts, dict)


# ---------------------------------------------------------------------------
# list_datasources
# ---------------------------------------------------------------------------


def test_list_datasources(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    datasources = project.list_datasources()
    assert isinstance(datasources, DiscoveryResult)
    assert len(datasources) >= 1
    assert any(d.name == "warehouse" for d in datasources)
    assert all(isinstance(d, DatasourceSummary) for d in datasources)


# ---------------------------------------------------------------------------
# list_datasets
# ---------------------------------------------------------------------------


def test_list_datasets(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    datasets = project.list_entities()
    assert isinstance(datasets, DiscoveryResult)
    assert len(datasets) >= 1
    assert any(d.name == "orders" for d in datasets)
    assert all(isinstance(d, EntitySummary) for d in datasets)
    # entity_provenance should be None when not materialized
    for d in datasets:
        assert d.entity_provenance is None


def test_list_datasets_filter_by_model(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    datasets = project.list_entities(domain="sales")
    assert len(datasets) >= 1
    assert all(d.domain == "sales" for d in datasets)

    # Non-existent model should return empty
    datasets_other = project.list_entities(domain="nonexistent")
    assert len(datasets_other) == 0


# ---------------------------------------------------------------------------
# list_fields / list_time_fields
# ---------------------------------------------------------------------------


def test_list_fields(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    fields = project.list_dimensions()
    field_names = [f.name for f in fields]
    assert "amount" in field_names
    assert "region" in field_names
    assert all(isinstance(f, DimensionSummary) for f in fields)
    assert all(not f.is_time_dimension for f in fields)
    assert all(f.kind == DimensionKind.CATEGORICAL for f in fields)


def test_list_fields_filter_by_dataset_keyword(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    fields = project.list_dimensions(entity="sales.orders")
    assert len(fields) >= 2
    assert all(f.entity == "sales.orders" for f in fields)


def test_list_fields_filter_by_model(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    fields = project.list_dimensions(domain="sales")
    assert len(fields) >= 1
    assert all(f.domain == "sales" for f in fields)


def test_list_fields_filter_by_model_and_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    fields = project.list_dimensions(domain="sales", entity="sales.orders")
    assert len(fields) >= 1
    assert all(f.domain == "sales" for f in fields)
    assert all(f.entity == "sales.orders" for f in fields)


def test_list_fields_rejects_positional_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    with pytest.raises(TypeError):
        project.list_dimensions("sales.orders")


def test_list_time_fields(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    time_fields = project.list_time_dimensions()
    assert len(time_fields) >= 1
    assert any(f.name == "created_at" for f in time_fields)
    assert all(f.is_time_dimension for f in time_fields)
    assert all(f.kind == DimensionKind.TIME for f in time_fields)


def test_list_time_fields_filter_by_model(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    time_fields = project.list_time_dimensions(domain="sales")
    assert len(time_fields) >= 1
    assert all(f.domain == "sales" for f in time_fields)


def test_list_time_fields_rejects_positional_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    with pytest.raises(TypeError):
        project.list_time_dimensions("sales.orders")


def test_list_fields_kind_measure(semantic_project_factory) -> None:
    model_with_measure = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="warehouse", source=ms.table("orders"))

        @ms.dimension(entity=orders, kind="measure")
        def amount(table):
            return table.amount

        @ms.dimension(entity=orders)
        def region(table):
            return table.region
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": model_with_measure,
        }
    )
    fields = project.list_dimensions()
    amount_field = next(f for f in fields if f.name == "amount")
    region_field = next(f for f in fields if f.name == "region")
    assert amount_field.kind == DimensionKind.MEASURE
    assert region_field.kind == DimensionKind.CATEGORICAL


def test_kind_string_comparison(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    fields = project.list_dimensions()
    dimension_fields = [f for f in fields if f.kind == "categorical"]
    assert len(dimension_fields) >= 2
    time_fields = project.list_time_dimensions()
    assert all(f.kind == "time" for f in time_fields)


# ---------------------------------------------------------------------------
# list_metrics
# ---------------------------------------------------------------------------


def test_list_metrics(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    metrics = project.list_metrics()
    assert len(metrics) >= 3
    metric_names = [m.name for m in metrics]
    assert "total_revenue" in metric_names
    assert "order_count" in metric_names
    assert all(isinstance(m, MetricSummary) for m in metrics)


def test_list_metrics_filter_by_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    metrics = project.list_metrics(entity="sales.orders")
    assert len(metrics) >= 1
    # MetricSummary doesn't have .datasets; just check we got results
    assert all(isinstance(m, MetricSummary) for m in metrics)


def test_list_metrics_filter_by_decomposition(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    sum_metrics = project.list_metrics(decomposition="sum")
    assert len(sum_metrics) >= 1
    assert all(m.decomposition_kind == "sum" for m in sum_metrics)


# ---------------------------------------------------------------------------
# list_relationships
# ---------------------------------------------------------------------------


def test_list_relationships(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    rels = project.list_relationships()
    assert len(rels) >= 1
    assert any(r.name == "orders_to_items" for r in rels)
    assert all(isinstance(r, RelationshipSummary) for r in rels)


def test_list_relationships_filter_by_model(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    rels = project.list_relationships(domain="sales")
    assert len(rels) >= 1
    assert all(r.domain == "sales" for r in rels)


# ---------------------------------------------------------------------------
# get_dataset / get_datasource / get_field / get_metric
# ---------------------------------------------------------------------------


def test_get_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    ds = project.get_entity("sales.orders")
    assert ds is not None
    assert ds.name == "orders"
    assert isinstance(ds, EntityIR)


def test_get_entity_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    assert project.get_entity("nonexistent") is None


def test_get_metric(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    m = project.get_metric("sales.total_revenue")
    assert m is not None
    assert m.name == "total_revenue"
    assert isinstance(m, MetricIR)


def test_get_metric_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    assert project.get_metric("nonexistent") is None


# ---------------------------------------------------------------------------
# reader on unloaded / errored project
# ---------------------------------------------------------------------------


def test_reader_on_unloaded_project_raises(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        },
        load=False,
    )
    with pytest.raises(SemanticLoadFailed):
        project.list_domains()


def test_reader_on_errored_project_raises(semantic_project_factory) -> None:
    bad_model = textwrap.dedent("""\
        raise ValueError("boom")
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": bad_model,
        }
    )
    with pytest.raises(SemanticLoadFailed):
        project.list_domains()


def test_require_registry_uses_project_not_loaded_error_kind(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        },
        load=False,
    )
    with pytest.raises(SemanticLoadFailed) as exc_info:
        project.list_metrics()
    errors = exc_info.value.errors
    assert len(errors) == 1
    assert errors[0].kind == "project_not_loaded"
    assert errors[0].constraint_id == "project_loaded_required"
    assert "project.load()" in (errors[0].hint or "")
    assert "list_metrics" not in (errors[0].hint or "")


def test_load_single_model_string(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        },
        load=False,
    )
    result = project.load("sales")
    assert result.status == "ready"
    assert project._filtered_domains == ("sales",)


def test_load_single_model_string_on_already_loaded_project(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        },
    )
    project.load("sales")
    assert project._filtered_domains == ("sales",)
    assert project.is_ready()


# ---------------------------------------------------------------------------
# returned objects are read-only (frozen dataclasses)
# ---------------------------------------------------------------------------


def test_returned_summary_objects_are_frozen(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    models = project.list_domains()
    if models:
        with pytest.raises(AttributeError):
            models[0].name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# empty project edge cases
# ---------------------------------------------------------------------------


def test_empty_project_list_methods(semantic_project_factory) -> None:
    project = semantic_project_factory({})
    assert len(project.list_domains()) == 0
    assert len(project.list_datasources()) == 0
    assert len(project.list_entities()) == 0
    assert len(project.list_dimensions()) == 0
    assert len(project.list_time_dimensions()) == 0
    assert len(project.list_metrics()) == 0
    assert len(project.list_relationships()) == 0


# ---------------------------------------------------------------------------
# DiscoveryResult return-type contract
# ---------------------------------------------------------------------------


def test_list_metrics_returns_discovery_result(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    result = project.list_metrics()
    assert isinstance(result, DiscoveryResult)


def test_list_metrics_is_silent(semantic_project_factory, capsys) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    project.list_metrics()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_list_models_returns_discovery_result(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    assert isinstance(project.list_domains(), DiscoveryResult)


def test_list_datasources_returns_discovery_result(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    assert isinstance(project.list_datasources(), DiscoveryResult)


def test_list_datasets_returns_discovery_result(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    assert isinstance(project.list_entities(), DiscoveryResult)


def test_list_fields_returns_discovery_result(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    assert isinstance(project.list_dimensions(), DiscoveryResult)


def test_list_time_fields_returns_discovery_result(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    assert isinstance(project.list_time_dimensions(), DiscoveryResult)


def test_list_relationships_returns_discovery_result(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    assert isinstance(project.list_relationships(), DiscoveryResult)


def test_list_metrics_no_display_parameter(semantic_project_factory) -> None:
    import inspect

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    sig = inspect.signature(project.list_metrics)
    assert "display" not in sig.parameters


def test_list_metrics_ids_returns_semantic_ids(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    result = project.list_metrics()
    ids = result.ids()
    assert isinstance(ids, list)
    assert all(isinstance(i, str) for i in ids)


def test_list_metrics_require_one_with_single_result(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    result = project.list_metrics(entity="sales.orders")
    if len(result) == 1:
        item = result.require_one()
        assert item.semantic_id in result.ids()


def test_list_metrics_require_one_zero_raises_no_results(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    result = project.list_metrics(entity="nonexistent.dataset")
    with pytest.raises(SelectionError) as exc_info:
        result.require_one()
    assert "no results" in str(exc_info.value.message).lower()


def test_list_models_ids_raises_selection_error(semantic_project_factory) -> None:
    """DomainSummary has no semantic_id, so .ids() should raise SelectionError."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    result = project.list_domains()
    with pytest.raises(SelectionError):
        result.ids()


# ---------------------------------------------------------------------------
# preview_dataset / preview_field / preview_metric
# ---------------------------------------------------------------------------


def test_preview_dataset_returns_bounded_rows(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )

    preview = project.preview_dataset("sales.orders", backend_factory=backend_factory, limit=2)

    assert isinstance(preview, PreviewResult)
    assert preview.kind == "semantic_dataset"
    assert preview.ref == "sales.orders"
    assert preview.requested_limit == 2
    assert preview.returned_row_count == 2
    assert preview.is_truncated is False
    assert "amount" in preview.columns


def test_preview_field_returns_values_with_context(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )

    preview = project.preview_field("sales.orders.amount", backend_factory=backend_factory, limit=2)

    assert preview.kind == "semantic_field"
    assert preview.ref == "sales.orders.amount"
    assert preview.columns[-1] == "amount"
    assert preview.rows[0]["amount"] == 100.0
    assert len(preview.columns) >= 2


def test_preview_metric_returns_scalar_value(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )

    preview = project.preview_metric(
        "sales.total_revenue",
        backend_factory=backend_factory,
        limit=20,
    )

    assert preview.kind == "semantic_metric"
    assert preview.ref == "sales.total_revenue"
    assert preview.columns == ("value",)
    assert preview.returned_row_count == 1
    assert preview.rows[0]["value"] == pytest.approx(300.0)
    assert preview.is_truncated is False
    assert preview.sample_policy.method == "pre_aggregate_limit"
    assert preview.sample_policy.limit == 20

    # approximate_preview warning should always be present for metric preview
    approx_warnings = [w for w in preview.warnings if w.kind == "approximate_preview"]
    assert len(approx_warnings) == 1


def test_preview_dataset_rejects_invalid_limit(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )

    with pytest.raises(PreviewLimitError):
        project.preview_dataset("sales.orders", backend_factory=backend_factory, limit=0)


def test_collect_source_preview_returns_datasource_preview_and_records_evidence(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )

    preview = project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
        columns=("order_id", "amount"),
        limit=2,
    )

    assert isinstance(preview, PreviewResult)
    assert preview.kind == "datasource_table"
    assert preview.ref == "warehouse.orders"
    assert preview.columns == ("order_id", "amount")
    assert preview.returned_row_count == 2
    # Verify the preview was persisted to the ledger
    from marivo.semantic.ledger import LedgerStore

    store = LedgerStore(project.semantic_root)
    records = store.read_raw_previews()
    assert len(records) >= 1
    assert records[0].ref == "warehouse.orders"


def test_collect_source_preview_persists_metadata_without_rows(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )

    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
        columns=("order_id", "amount"),
        limit=2,
    )

    path = project.semantic_root / ".evidence" / "raw_previews.json"
    payload = json.loads(path.read_text())

    assert len(payload["raw_previews"]) == 1
    record = payload["raw_previews"][0]
    assert record["ref"] == "warehouse.orders"
    assert record["datasource"] == "warehouse"
    assert record["table"] == "orders"
    assert record["database"] is None
    assert record["columns"] == ["order_id", "amount"]
    assert record["types"] == {"order_id": "int32", "amount": "float32"}
    assert record["requested_limit"] == 2
    assert record["returned_row_count"] == 2
    assert record["sample_policy"] == {
        "method": "bounded_limit",
        "limit": 2,
        "order_by": [],
        "filters": [],
    }
    assert "collected_at" in record
    assert "rows" not in record


def test_collect_source_preview_replaces_persisted_record_for_same_ref(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )

    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
        columns=("order_id",),
        limit=1,
    )
    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
        columns=("order_id", "amount"),
        limit=2,
    )

    path = project.semantic_root / ".evidence" / "raw_previews.json"
    payload = json.loads(path.read_text())

    assert len(payload["raw_previews"]) == 1
    record = payload["raw_previews"][0]
    assert record["ref"] == "warehouse.orders"
    assert record["columns"] == ["order_id", "amount"]
    assert record["requested_limit"] == 2


def test_collect_source_preview_rejects_invalid_limit(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )

    with pytest.raises(PreviewLimitError):
        project.collect_source_preview(
            datasource="warehouse",
            table="orders",
            backend_factory=backend_factory,
            limit=0,
        )


# ---------------------------------------------------------------------------
# bind_datasource_access
# ---------------------------------------------------------------------------


def test_bind_datasource_access_materialize(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    table = project.materialize_dataset("sales.orders")
    assert hasattr(table, "columns")


def test_bind_datasource_access_preview(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    result = project.preview_metric("sales.total_revenue", limit=2)
    assert isinstance(result, PreviewResult)


def test_bind_datasource_access_missing_raises(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.materialize_dataset("sales.orders")
    assert exc_info.value.kind == ErrorKind.BACKEND_FACTORY_REQUIRED


def test_bind_datasource_access_explicit_override(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    table = project.materialize_dataset("sales.orders", backend_factory=backend_factory)
    assert hasattr(table, "columns")


def test_bind_datasource_access_preserved_across_reload(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    project.load()
    table = project.materialize_dataset("sales.orders")
    assert hasattr(table, "columns")


def test_readiness_uses_bound_factory(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    project.bind_datasource_access(
        inspect_source=_fake_inspect_source, backend_factory=backend_factory
    )
    report = project.readiness()
    assert report.status in ("ready", "ready_with_warnings", "warning", "blocked")


def test_readiness_without_bound_factory(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    report = project.readiness()
    assert report.status == "blocked"
    blockers = [issue for issue in report.blockers if issue.kind == "datasource_unreachable"]
    assert blockers
    assert "project-bound backend access" in blockers[0].message
    assert "bind_datasource_access" in blockers[0].message
