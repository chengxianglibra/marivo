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
    FieldSummary,
    MetricSummary,
    ModelSummary,
    RelationshipSummary,
    SearchHit,
    SemanticProject,
)
from marivo.semantic.validator import Registry

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

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)
    def total_revenue(table):
        return table.amount.sum()

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)
    def order_count(table):
        return table.count()

    @ms.metric(
        datasets=[orders],
        additivity='additive',
        decomposition=ms.sum(),
        description="Average order value",
    verification_mode="python_native",)
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

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)
    def revenue(table):
        return table.amount.sum()

    @ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum(), verification_mode='python_native',)
    def count_metric(table):
        return table.count()

    aov = ms.derived_metric(
        name="aov",
        decomposition=ms.weighted_average(
            value="sales.revenue",
            weight="sales.count_metric",
        ),
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
    models = project.list_models(display=False)
    assert len(models) >= 1
    assert any(m.name == "sales" for m in models)
    assert all(isinstance(m, ModelSummary) for m in models)
    # Verify object_counts is present
    for m in models:
        assert isinstance(m.object_counts, dict)


def test_list_methods_display_tables_by_default(semantic_project_factory, capsys) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )

    method_expectations = [
        (
            project.list_models,
            "name | default | datasets | fields | time_fields | metrics | relationships | description",
            "sales",
        ),
        (project.list_datasources, "semantic_id | name | backend_type | description", "warehouse"),
        (project.list_datasets, "semantic_id | model | datasource | description", "sales.orders"),
        (
            project.list_fields,
            "semantic_id | dataset | name | description",
            "sales.orders.amount",
        ),
        (
            project.list_time_fields,
            "semantic_id | dataset | name | data_type | granularity | description",
            "sales.orders.created_at",
        ),
        (
            project.list_metrics,
            "semantic_id | model | name | decomposition_kind | is_derived | parity_status | description",
            "sales.total_revenue",
        ),
        (
            project.list_relationships,
            "semantic_id | model | from_dataset | to_dataset | description",
            "sales.orders_to_items",
        ),
    ]

    for method, header, expected_value in method_expectations:
        result = method()
        output = capsys.readouterr().out
        assert result
        assert header in output
        assert expected_value in output


def test_list_methods_display_false_suppresses_output(semantic_project_factory, capsys) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )

    metrics = project.list_metrics(display=False)

    assert any(metric.semantic_id == "sales.total_revenue" for metric in metrics)
    assert capsys.readouterr().out == ""


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
    datasources = project.list_datasources(display=False)
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
    datasets = project.list_datasets(display=False)
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
    datasets = project.list_datasets(model="sales", display=False)
    assert len(datasets) >= 1
    assert all(d.model == "sales" for d in datasets)

    # Non-existent model should return empty
    datasets_other = project.list_datasets(model="nonexistent", display=False)
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
    fields = project.list_fields(display=False)
    field_names = [f.name for f in fields]
    assert "amount" in field_names
    assert "region" in field_names
    assert all(isinstance(f, FieldSummary) for f in fields)
    assert all(not f.is_time_field for f in fields)


def test_list_fields_filter_by_dataset_keyword(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    fields = project.list_fields(dataset="sales.orders", display=False)
    assert len(fields) >= 2
    assert all(f.dataset == "sales.orders" for f in fields)


def test_list_fields_rejects_positional_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    with pytest.raises(TypeError):
        project.list_fields("sales.orders", display=False)


def test_list_time_fields(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    time_fields = project.list_time_fields(display=False)
    assert len(time_fields) >= 1
    assert any(f.name == "created_at" for f in time_fields)
    assert all(f.is_time_field for f in time_fields)


def test_list_time_fields_rejects_positional_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    with pytest.raises(TypeError):
        project.list_time_fields("sales.orders", display=False)


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
    metrics = project.list_metrics(display=False)
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
    metrics = project.list_metrics(dataset="sales.orders", display=False)
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
    sum_metrics = project.list_metrics(decomposition="sum", display=False)
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
    rels = project.list_relationships(display=False)
    assert len(rels) >= 1
    assert any(r.name == "orders_to_items" for r in rels)
    assert all(isinstance(r, RelationshipSummary) for r in rels)


def test_list_relationships_filter_by_model(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    rels = project.list_relationships(model="sales", display=False)
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
    f = project.get_field("sales.orders.amount")
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


def test_get_relationship(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    rel = project.get_relationship("sales.orders_to_items")
    assert rel is not None
    assert rel.name == "orders_to_items"
    assert isinstance(rel, RelationshipIR)


def test_get_relationship_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    assert project.get_relationship("nonexistent") is None


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
    results = project.search("sales.total_revenue", display=False)
    assert len(results) >= 1
    # Exact semantic_id match should be first
    top = results[0]
    assert top.semantic_id == "sales.total_revenue"
    assert top.kind == SymbolKind.METRIC
    assert top.matched_field == "semantic_id"
    assert isinstance(top, SearchHit)


def test_search_displays_results_by_default(semantic_project_factory, capsys) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )

    results = project.search("sales.total_revenue")

    output = capsys.readouterr().out
    assert results
    assert "semantic_id | kind | matched_field | matched_snippet" in output
    assert "sales.total_revenue" in output
    assert "metric" in output


def test_search_display_false_suppresses_output(semantic_project_factory, capsys) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )

    results = project.search("sales.total_revenue", display=False)

    assert results
    assert capsys.readouterr().out == ""


def test_search_name_match(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("total_revenue", display=False)
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
    results = project.search("average order", display=False)
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
    results = project.search("TOTAL_REVENUE", display=False)
    assert len(results) >= 1
    assert any(r.semantic_id == "sales.total_revenue" for r in results)


def test_search_kind_filter(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("sales", kind=SymbolKind.METRIC, display=False)
    assert all(r.kind == SymbolKind.METRIC for r in results)


def test_search_no_results(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("zzzznonexistent", display=False)
    assert len(results) == 0


def test_search_results_sorted_by_field_priority(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    results = project.search("revenue", display=False)
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
    # Dataset depends on its datasource
    child_ids = [c.semantic_id for c in root.children]
    assert "warehouse" in child_ids


def test_dependencies_field(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    root = project.dependencies("sales.orders.amount")
    assert root.semantic_id == "sales.orders.amount"
    assert root.kind == SymbolKind.FIELD
    # Field depends on its parent dataset
    child_ids = [c.semantic_id for c in root.children]
    assert "sales.orders" in child_ids


def test_dependencies_time_field(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    root = project.dependencies("sales.orders.created_at")
    assert root.semantic_id == "sales.orders.created_at"
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
    assert exc_info.value.kind == ErrorKind.NOT_FOUND


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
    assert "sales.orders.amount" in child_ids


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
    root = project.dependents("sales.orders.amount")
    assert root.semantic_id == "sales.orders.amount"
    assert root.kind == SymbolKind.FIELD
    # Fields have no dependents
    assert root.children == ()


def test_dependents_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.dependents("nonexistent")
    assert exc_info.value.kind == ErrorKind.NOT_FOUND


def test_dependencies_relationship(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    root = project.dependencies("sales.orders_to_items")
    assert root.semantic_id == "sales.orders_to_items"
    assert root.kind == SymbolKind.RELATIONSHIP
    child_ids = [c.semantic_id for c in root.children]
    assert "sales.orders" in child_ids


def test_dependents_relationship(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    root = project.dependents("sales.orders_to_items")
    assert root.semantic_id == "sales.orders_to_items"
    assert root.kind == SymbolKind.RELATIONSHIP
    assert root.children == ()


def test_describe_dataset_deps_consistent(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe("sales.orders")
    tree_ids = project._flatten_ids(project.dependencies("sales.orders"))
    assert set(desc.dependencies) == tree_ids


def test_describe_field_deps_consistent(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe("sales.orders.amount")
    tree_dep_ids = project._flatten_ids(project.dependencies("sales.orders.amount"))
    tree_dep_of_ids = project._flatten_ids(project.dependents("sales.orders.amount"))
    assert set(desc.dependencies) == tree_dep_ids
    assert set(desc.dependents) == tree_dep_of_ids


def test_blast_radius_dataset(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    br = project.blast_radius_of(("sales.orders",))
    # Dependents of sales.orders: metrics + fields
    expected = len(project._flatten_ids(project.dependents("sales.orders")))
    assert br == expected


def test_blast_radius_field(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    br = project.blast_radius_of(("sales.orders.amount",))
    # Fields have no dependents
    assert br == 0


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
    desc = project.describe("sales.total_revenue")
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
    assert exc_info.value.kind == ErrorKind.NOT_FOUND


def test_describe_with_compile_sql(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe(
        "sales.total_revenue",
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
    desc = project.describe("sales.orders.amount")
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
    desc = project.describe("sales.orders.created_at")
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
    desc = project.describe("sales.orders.log_date")
    assert isinstance(desc, Description)
    assert desc.kind == SymbolKind.TIME_FIELD
    assert desc.format == "yyyymmdd"


def _make_ambiguous_registry() -> Registry:
    """Build a Registry where a dataset and metric share the same semantic_id.

    The validator normally prevents this, but _find_ir must still be safe.
    """
    from marivo.semantic.ir import (
        AiContextIR,
        DatasetIR,
        DecompositionIR,
        MetricIR,
        ProvenanceIR,
        SourceLocation,
        TableSourceIR,
    )

    shared_id = "sales.dau_7d_portrait"
    ds = DatasetIR(
        semantic_id=shared_id,
        model="sales",
        name="dau_7d_portrait",
        datasource="warehouse",
        source=TableSourceIR(table="dau_7d_portrait"),
        primary_key=(),
        description=None,
        ai_context=AiContextIR(),
        python_symbol="dau_portrait",
        location=SourceLocation(file="test.py", line=1),
    )
    metric = MetricIR(
        semantic_id=shared_id,
        model="sales",
        name="dau_7d_portrait",
        datasets=(shared_id,),
        is_derived=False,
        decomposition=DecompositionIR(kind="sum"),
        provenance=ProvenanceIR(),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="",
        python_symbol="dau_7d_portrait_metric",
        location=SourceLocation(file="test.py", line=2),
        additivity="additive",
    )
    reg = Registry()
    reg.datasets[shared_id] = ds
    reg.metrics[shared_id] = metric
    return reg


def test_find_ir_ambiguous_name_raises() -> None:
    """When a name matches multiple kinds in the registry, _find_ir raises AMBIGUOUS_REFERENCE."""
    from marivo.semantic.errors import SemanticRuntimeError

    reg = _make_ambiguous_registry()
    shared_id = "sales.dau_7d_portrait"

    with pytest.raises(SemanticRuntimeError) as exc_info:
        SemanticProject._find_ir(shared_id, reg)

    assert exc_info.value.kind == "ambiguous_reference"
    assert "candidates" in exc_info.value.details
    candidates = exc_info.value.details["candidates"]
    assert len(candidates) == 2
    kind_strs = {c[0] for c in candidates}
    assert "dataset" in kind_strs
    assert "metric" in kind_strs


def test_find_ir_with_kind_returns_single_match() -> None:
    """When kind is specified, _find_ir only searches the matching collection."""
    from marivo.semantic.ir import DatasetIR, MetricIR, SymbolKind

    reg = _make_ambiguous_registry()
    shared_id = "sales.dau_7d_portrait"

    ds_result = SemanticProject._find_ir(shared_id, reg, kind=SymbolKind.DATASET)
    assert isinstance(ds_result, DatasetIR)

    metric_result = SemanticProject._find_ir(shared_id, reg, kind=SymbolKind.METRIC)
    assert isinstance(metric_result, MetricIR)

    # kind that has no match returns None
    none_result = SemanticProject._find_ir(shared_id, reg, kind=SymbolKind.FIELD)
    assert none_result is None


def test_describe_with_kind_param(semantic_project_factory) -> None:
    """Passing kind= to describe narrows the search to the specified collection."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )

    # describe with kind=METRIC should find the metric
    desc = project.describe("sales.total_revenue", kind=SymbolKind.METRIC)
    assert desc.kind == SymbolKind.METRIC

    # describe with kind=DATASET should find the dataset
    desc = project.describe("sales.orders", kind=SymbolKind.DATASET)
    assert desc.kind == SymbolKind.DATASET

    # describe with kind that doesn't match should raise DATASET_NOT_FOUND
    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.describe("sales.total_revenue", kind=SymbolKind.DATASET)
    assert exc_info.value.kind == ErrorKind.DATASET_NOT_FOUND


def test_describe_relationship(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe("sales.orders_to_items")
    assert isinstance(desc, Description)
    assert desc.kind == SymbolKind.RELATIONSHIP
    assert desc.name == "orders_to_items"
    assert desc.from_dataset == "sales.orders"
    assert desc.to_dataset == "sales.orders"
    assert desc.from_fields == ("sales.orders.amount",)
    assert desc.to_fields == ("sales.orders.amount",)
    assert "sales.orders" in desc.dependencies


def test_describe_relationship_to_text(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    desc = project.describe("sales.orders_to_items")
    text = desc.to_text()
    assert "[relationship]" in text
    assert "from_dataset" in text
    assert "to_dataset" in text


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
        project.list_models(display=False)


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
        project.list_models(display=False)


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
    models = project.list_models(display=False)
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
    assert project.list_models(display=False) == []
    assert project.list_datasources(display=False) == []
    assert project.list_datasets(display=False) == []
    assert project.list_fields(display=False) == []
    assert project.list_time_fields(display=False) == []
    assert project.list_metrics(display=False) == []
    assert project.list_relationships(display=False) == []


def test_empty_project_list_methods_display_empty_messages(
    semantic_project_factory, capsys
) -> None:
    project = semantic_project_factory({})

    empty_expectations = [
        (project.list_models, "No models found."),
        (project.list_datasources, "No datasources found."),
        (project.list_datasets, "No datasets found."),
        (project.list_fields, "No fields found."),
        (project.list_time_fields, "No time fields found."),
        (project.list_metrics, "No metrics found."),
        (project.list_relationships, "No relationships found."),
    ]

    for method, message in empty_expectations:
        assert method() == []
        assert capsys.readouterr().out == f"{message}\n"


def test_empty_project_search(semantic_project_factory) -> None:
    project = semantic_project_factory({})
    results = project.search("anything", display=False)
    assert results == []


def test_empty_project_search_displays_empty_message(semantic_project_factory, capsys) -> None:
    project = semantic_project_factory({})

    results = project.search("anything")

    assert results == []
    assert capsys.readouterr().out == "No search results found.\n"


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

    preview = project.preview_field("sales.orders.amount", backend_factory=backend_factory, limit=2)

    assert preview.kind == "semantic_field"
    assert preview.ref == "sales.orders.amount"
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
    assert preview.sample_policy.method == "pre_aggregate_limit"
    assert preview.sample_policy.limit == 20

    # approximate_preview warning should always be present for metric preview
    approx_warnings = [w for w in preview.warnings if w.kind == "approximate_preview"]
    assert len(approx_warnings) == 1


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

    path = project.root / ".evidence" / "raw_previews.json"
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

    path = project.root / ".evidence" / "raw_previews.json"
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


# ---------------------------------------------------------------------------
# bind_backend_factory
# ---------------------------------------------------------------------------


def test_bind_backend_factory_materialize(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    project.bind_backend_factory(backend_factory)
    table = project.materialize_dataset("sales.orders")
    assert hasattr(table, "columns")


def test_bind_backend_factory_preview(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    project.bind_backend_factory(backend_factory)
    result = project.preview_metric("sales.total_revenue", limit=2)
    assert isinstance(result, PreviewResult)


def test_bind_backend_factory_missing_raises(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.compile_sql("sales.total_revenue")
    assert exc_info.value.kind == ErrorKind.BACKEND_FACTORY_REQUIRED


def test_bind_backend_factory_explicit_override(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    project.bind_backend_factory(backend_factory)
    sql = project.compile_sql("sales.total_revenue", backend_factory=backend_factory)
    assert isinstance(sql, str) and len(sql) > 0


def test_bind_backend_factory_preserved_across_reload(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    project.bind_backend_factory(backend_factory)
    project.reload()
    table = project.materialize_dataset("sales.orders")
    assert hasattr(table, "columns")


def test_readiness_uses_bound_factory(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    project.bind_backend_factory(backend_factory)
    report = project.readiness()
    assert report.status in ("ready", "warning", "blocked")


def test_readiness_without_bound_factory(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_PY,
            "sales/objects.py": _FULL_MODEL_PY,
        }
    )
    # readiness() produces a degraded report even without a bound factory
    report = project.readiness()
    assert report is not None
