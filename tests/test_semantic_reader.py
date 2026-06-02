"""Tests for marivo.semantic reader API — list/search/lineage/describe/compile_sql.

Tests cover:
- list_models, list_datasources, list_datasets, list_fields, list_time_fields,
  list_metrics, list_relationships
- get_dataset, get_datasource, get_field, get_metric
- search (substring match, kind filter, field priority)
- dependencies (metric, dataset, derived metric)
- dependents (dataset, field, metric)
- describe (returns Description, to_text)
- compile_sql (base metric, derived metric, not found)
- reader on unloaded/errored project
"""

from __future__ import annotations

import json
import textwrap

import ibis
import pytest

from marivo.preview import PreviewLimitError, PreviewResult
from marivo.semantic.errors import ErrorKind, SemanticLoadFailed, SemanticRuntimeError
from marivo.semantic.ir import (
    DatasetIR,
    DatasourceIR,
    FieldIR,
    MetricIR,
    RelationshipIR,
    SymbolKind,
)
from marivo.semantic.reader import (
    DatasetSummary,
    DatasourceSummary,
    DependencyNode,
    Description,
    MetricSummary,
    ModelSummary,
    SearchHit,
)

# ---------------------------------------------------------------------------
# Model file templates
# ---------------------------------------------------------------------------

_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.model(name="sales", default=True)
""")

_FULL_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.field(dataset=orders)
    def amount(table):
        return table.amount

    @ms.field(dataset=orders)
    def region(table):
        return table.region

    @ms.time_field(dataset=orders, data_type="timestamp", granularity="day")
    def created_at(table):
        return table.created_at

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum())
    def total_revenue(table):
        return table.amount.sum()

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum())
    def order_count(table):
        return table.count()

    @ms.metric(
        datasets=[orders],
        additivity='additive',
        decomposition=ms.sum(),
        description="Average order value",
    )
    def aov(table):
        return table.amount.mean()

    ms.relationship(
        name="orders_to_items",
        from_dataset=orders,
        to_dataset=orders,
        from_fields=[amount],
        to_fields=[amount],
    )
""")


_DERIVED_METRIC_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    orders = ms.dataset(name="orders", datasource="warehouse", source=ms.table("orders"))

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum())
    def revenue(table):
        return table.amount.sum()

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum())
    def count_metric(table):
        return table.count()

    @ms.metric(
        datasets=[],
        decomposition=ms.weighted_average(value="sales.revenue", weight="sales.count_metric"),
    )
    def aov():
        return ms.component("numerator") / ms.component("weight")
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


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


def test_list_models(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    models = project.list_models()
    assert len(models) >= 1
    assert any(m.name == "sales" for m in models)
    assert all(isinstance(m, ModelSummary) for m in models)
    # Verify object_counts is present
    for m in models:
        assert isinstance(m.object_counts, dict)


# ---------------------------------------------------------------------------
# list_datasources
# ---------------------------------------------------------------------------


def test_list_datasources(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    datasources = project.list_datasources()
    assert len(datasources) >= 1
    assert any(d.name == "warehouse" for d in datasources)
    assert all(isinstance(d, DatasourceSummary) for d in datasources)


# ---------------------------------------------------------------------------
# list_datasets
# ---------------------------------------------------------------------------


def test_list_datasets(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    datasets = project.list_datasets()
    assert len(datasets) >= 1
    assert any(d.name == "orders" for d in datasets)
    assert all(isinstance(d, DatasetSummary) for d in datasets)
    # dataset_provenance should be None when not materialized
    for d in datasets:
        assert d.dataset_provenance is None


def test_list_datasets_filter_by_model(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    datasets = project.list_datasets(model="sales")
    assert len(datasets) >= 1
    assert all(d.model == "sales" for d in datasets)

    # Non-existent model should return empty
    datasets_other = project.list_datasets(model="nonexistent")
    assert len(datasets_other) == 0


# ---------------------------------------------------------------------------
# list_fields / list_time_fields
# ---------------------------------------------------------------------------


def test_list_fields(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    fields = project.list_fields()
    # amount and region are non-time fields
    field_names = [f.name for f in fields]
    assert "amount" in field_names
    assert "region" in field_names
    assert all(isinstance(f, FieldIR) for f in fields)
    assert all(not f.is_time_field for f in fields)


def test_list_fields_filter_by_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    fields = project.list_fields(dataset="sales.orders")
    assert len(fields) >= 2
    assert all(f.dataset == "sales.orders" for f in fields)


def test_list_time_fields(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    time_fields = project.list_time_fields()
    assert len(time_fields) >= 1
    assert any(f.name == "created_at" for f in time_fields)
    assert all(f.is_time_field for f in time_fields)


# ---------------------------------------------------------------------------
# list_metrics
# ---------------------------------------------------------------------------


def test_list_metrics(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
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
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    metrics = project.list_metrics(dataset="sales.orders")
    assert len(metrics) >= 1
    # MetricSummary doesn't have .datasets; just check we got results
    assert all(isinstance(m, MetricSummary) for m in metrics)


def test_list_metrics_filter_by_decomposition(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
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
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    rels = project.list_relationships()
    assert len(rels) >= 1
    assert any(r.name == "orders_to_items" for r in rels)
    assert all(isinstance(r, RelationshipIR) for r in rels)


def test_list_relationships_filter_by_model(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    rels = project.list_relationships(model="sales")
    assert len(rels) >= 1
    assert all(r.model == "sales" for r in rels)


# ---------------------------------------------------------------------------
# get_dataset / get_datasource / get_field / get_metric
# ---------------------------------------------------------------------------


def test_get_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    ds = project.get_dataset("sales.orders")
    assert ds is not None
    assert ds.name == "orders"
    assert isinstance(ds, DatasetIR)


def test_get_dataset_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    assert project.get_dataset("nonexistent") is None


def test_get_datasource(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    ds = project.get_datasource("warehouse")
    assert ds is not None
    assert ds.name == "warehouse"
    assert isinstance(ds, DatasourceIR)


def test_get_datasource_uses_global_name_not_model_qualified_id(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )

    assert project.get_datasource("sales.warehouse") is None


def test_get_field(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    f = project.get_field("sales.amount")
    assert f is not None
    assert f.name == "amount"
    assert isinstance(f, FieldIR)


def test_get_metric(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    m = project.get_metric("sales.total_revenue")
    assert m is not None
    assert m.name == "total_revenue"
    assert isinstance(m, MetricIR)


def test_get_metric_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    assert project.get_metric("nonexistent") is None


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_exact_semantic_id_match(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("sales.total_revenue")
    assert len(results) >= 1
    # Exact semantic_id match should be first
    top = results[0]
    assert top.semantic_id == "sales.total_revenue"
    assert top.kind == SymbolKind.METRIC
    assert top.matched_field == "semantic_id"
    assert isinstance(top, SearchHit)


def test_search_name_match(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("total_revenue")
    assert len(results) >= 1
    top = results[0]
    assert "total_revenue" in top.semantic_id
    # Matched on either semantic_id or name
    assert top.matched_field in ("semantic_id", "name")


def test_search_description_match(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("average order")
    assert len(results) >= 1
    # The aov metric has description "Average order value"
    aov_results = [r for r in results if r.semantic_id == "sales.aov"]
    assert len(aov_results) >= 1
    assert aov_results[0].matched_field == "description"


def test_search_case_insensitive(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("TOTAL_REVENUE")
    assert len(results) >= 1
    assert any(r.semantic_id == "sales.total_revenue" for r in results)


def test_search_kind_filter(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("sales", kind=SymbolKind.METRIC)
    assert all(r.kind == SymbolKind.METRIC for r in results)


def test_search_no_results(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("zzzznonexistent")
    assert len(results) == 0


def test_search_results_sorted_by_field_priority(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("revenue")
    # Results should be sorted by field priority then semantic_id
    if len(results) > 1:
        _field_priority = {
            "semantic_id": 0,
            "name": 1,
            "description": 2,
            "business_definition": 3,
            "synonyms": 4,
            "examples": 5,
        }
        for i in range(len(results) - 1):
            pri_a = _field_priority.get(results[i].matched_field, 99)
            pri_b = _field_priority.get(results[i + 1].matched_field, 99)
            assert pri_a <= pri_b


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------


def test_dependencies_metric(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    root = project.dependencies("sales.total_revenue")
    assert isinstance(root, DependencyNode)
    assert root.semantic_id == "sales.total_revenue"
    assert root.kind == SymbolKind.METRIC
    # Should include dataset dependency
    child_ids = [c.semantic_id for c in root.children]
    assert "sales.orders" in child_ids
    # Dataset should have field children
    orders_node = next(c for c in root.children if c.semantic_id == "sales.orders")
    field_ids = [fc.semantic_id for fc in orders_node.children]
    assert "sales.amount" in field_ids


def test_dependencies_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    root = project.dependencies("sales.orders")
    assert root.semantic_id == "sales.orders"
    assert root.kind == SymbolKind.DATASET
    # Should include fields belonging to this dataset
    child_ids = [c.semantic_id for c in root.children]
    assert "sales.amount" in child_ids
    assert "sales.region" in child_ids
    assert "sales.created_at" in child_ids


def test_dependencies_field(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    root = project.dependencies("sales.amount")
    assert root.semantic_id == "sales.amount"
    assert root.kind == SymbolKind.FIELD
    assert root.children == ()


def test_dependencies_time_field(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    root = project.dependencies("sales.created_at")
    assert root.semantic_id == "sales.created_at"
    assert root.kind == SymbolKind.TIME_FIELD


def test_dependencies_derived_metric(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/derived.py": _DERIVED_METRIC_MODEL_PY,
        }
    )
    root = project.dependencies("sales.aov")
    assert root.semantic_id == "sales.aov"
    assert root.kind == SymbolKind.METRIC
    # Derived metric depends on its component metrics
    child_ids = [c.semantic_id for c in root.children]
    assert "sales.revenue" in child_ids
    assert "sales.count_metric" in child_ids
    # Component metrics depend on their datasets
    revenue_node = next(c for c in root.children if c.semantic_id == "sales.revenue")
    revenue_child_ids = [rc.semantic_id for rc in revenue_node.children]
    assert "sales.orders" in revenue_child_ids


def test_dependencies_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.dependencies("nonexistent")
    assert exc_info.value.kind == ErrorKind.METRIC_NOT_FOUND


# ---------------------------------------------------------------------------
# dependents
# ---------------------------------------------------------------------------


def test_dependents_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    root = project.dependents("sales.orders")
    assert root.semantic_id == "sales.orders"
    assert root.kind == SymbolKind.DATASET
    child_ids = [c.semantic_id for c in root.children]
    # Metrics that depend on this dataset
    assert "sales.total_revenue" in child_ids
    assert "sales.order_count" in child_ids
    # Fields belonging to this dataset
    assert "sales.amount" in child_ids


def test_dependents_metric(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/derived.py": _DERIVED_METRIC_MODEL_PY,
        }
    )
    # revenue is a component of aov
    root = project.dependents("sales.revenue")
    assert root.semantic_id == "sales.revenue"
    assert root.kind == SymbolKind.METRIC
    child_ids = [c.semantic_id for c in root.children]
    assert "sales.aov" in child_ids


def test_dependents_field(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    root = project.dependents("sales.amount")
    assert root.semantic_id == "sales.amount"
    assert root.kind == SymbolKind.FIELD
    child_ids = [c.semantic_id for c in root.children]
    # Field's parent dataset
    assert "sales.orders" in child_ids


def test_dependents_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.dependents("nonexistent")
    assert exc_info.value.kind == ErrorKind.METRIC_NOT_FOUND


# ---------------------------------------------------------------------------
# compile_sql
# ---------------------------------------------------------------------------


def test_compile_sql_base_metric(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    sql = project.compile_sql("sales.total_revenue", backend_factory=backend_factory)
    assert isinstance(sql, str)
    assert len(sql) > 0
    # Should contain a SUM or sum
    assert "sum" in sql.lower() or "SUM" in sql


def test_compile_sql_derived_metric(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/derived.py": _DERIVED_METRIC_MODEL_PY,
        }
    )
    sql = project.compile_sql("sales.aov", backend_factory=backend_factory)
    assert isinstance(sql, str)
    assert len(sql) > 0


def test_compile_sql_metric_not_found(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.compile_sql("nonexistent", backend_factory=backend_factory)
    assert exc_info.value.kind == ErrorKind.COMPILE_ERROR


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------


def test_describe_returns_description(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe("sales.total_revenue")
    assert isinstance(desc, Description)
    assert desc.name == "total_revenue"
    assert desc.kind == SymbolKind.METRIC
    assert desc.parity_status is not None


def test_describe_to_text(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe("sales.total_revenue", format="text")
    assert isinstance(desc, Description)
    text = desc.to_text()
    assert isinstance(text, str)
    assert "total_revenue" in text
    assert "[metric]" in text


def test_describe_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.describe("nonexistent")
    assert exc_info.value.kind == ErrorKind.METRIC_NOT_FOUND


def test_describe_with_compile_sql(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe(
        "sales.total_revenue",
        format="text",
        compile_sql=True,
        backend_factory=backend_factory,
    )
    assert isinstance(desc, Description)
    assert desc.compiled_sql is not None
    text = desc.to_text()
    assert "compiled_sql" in text


def test_describe_dataset_has_provenance_none(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe("sales.orders")
    assert isinstance(desc, Description)
    assert desc.kind == SymbolKind.DATASET
    assert desc.dataset_provenance is None  # Not materialized yet
    assert desc.primary_key is not None  # Should be a tuple (possibly empty)


def test_describe_field_has_granularity_none(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe("sales.amount")
    assert isinstance(desc, Description)
    assert desc.kind == SymbolKind.FIELD
    assert desc.granularity is None  # Not a time field


def test_describe_time_field_has_granularity(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe("sales.created_at")
    assert isinstance(desc, Description)
    assert desc.kind == SymbolKind.TIME_FIELD
    assert desc.granularity == "day"


def test_describe_time_field_has_format(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": textwrap.dedent("""\
                import marivo.semantic as ms
                orders = ms.dataset(name="orders", datasource="wh", source=ms.table("orders"))

                @ms.time_field(
                    dataset=orders,
                    data_type="string",
                    granularity="day",
                    date_format="yyyymmdd",
                )
                def log_date(table):
                    return table.log_date
            """),
        }
    )
    desc = project.describe("sales.log_date")
    assert isinstance(desc, Description)
    assert desc.kind == SymbolKind.TIME_FIELD
    assert desc.format == "yyyymmdd"


# ---------------------------------------------------------------------------
# reader on unloaded / errored project
# ---------------------------------------------------------------------------


def test_reader_on_unloaded_project_raises(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        },
        load=False,
    )
    with pytest.raises(SemanticLoadFailed):
        project.list_models()


def test_reader_on_errored_project_raises(semantic_project_factory) -> None:
    bad_model = textwrap.dedent("""\
        raise ValueError("boom")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": bad_model,
        }
    )
    with pytest.raises(SemanticLoadFailed):
        project.list_models()


# ---------------------------------------------------------------------------
# returned objects are read-only (frozen dataclasses)
# ---------------------------------------------------------------------------


def test_returned_summary_objects_are_frozen(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    models = project.list_models()
    if models:
        with pytest.raises(AttributeError):
            models[0].name = "mutated"  # type: ignore[misc]


def test_description_is_frozen(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe("sales.total_revenue")
    with pytest.raises(AttributeError):
        desc.name = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# empty project edge cases
# ---------------------------------------------------------------------------


def test_empty_project_list_methods(semantic_project_factory) -> None:
    project = semantic_project_factory({})
    assert project.list_models() == []
    assert project.list_datasources() == []
    assert project.list_datasets() == []
    assert project.list_fields() == []
    assert project.list_time_fields() == []
    assert project.list_metrics() == []
    assert project.list_relationships() == []


def test_empty_project_search(semantic_project_factory) -> None:
    project = semantic_project_factory({})
    results = project.search("anything")
    assert results == []


# ---------------------------------------------------------------------------
# preview_dataset / preview_field / preview_metric
# ---------------------------------------------------------------------------


def test_preview_dataset_returns_bounded_rows(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
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
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )

    preview = project.preview_field("sales.amount", backend_factory=backend_factory, limit=2)

    assert preview.kind == "semantic_field"
    assert preview.ref == "sales.amount"
    assert preview.columns[-1] == "amount"
    assert preview.rows[0]["amount"] == 100.0
    assert len(preview.columns) >= 2


def test_preview_metric_returns_scalar_value(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
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


def test_preview_dataset_rejects_invalid_limit(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
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
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
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
    assert project.raw_preview_evidence() == ("warehouse.orders",)


def test_collect_source_preview_persists_metadata_without_rows(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )

    project.collect_source_preview(
        datasource="warehouse",
        table="orders",
        backend_factory=backend_factory,
        columns=("order_id", "amount"),
        limit=2,
    )

    path = project.root_path / ".evidence" / "raw_previews.json"
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
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
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

    path = project.root_path / ".evidence" / "raw_previews.json"
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
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )

    with pytest.raises(PreviewLimitError):
        project.collect_source_preview(
            datasource="warehouse",
            table="orders",
            backend_factory=backend_factory,
            limit=0,
        )
