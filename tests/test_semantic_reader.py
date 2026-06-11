"""Tests for marivo.semantic reader API — preview/readiness/internal project access.

Tests cover:
- catalog get and internal registry bridge access
- reader on unloaded/errored project
- preview operations
"""

from __future__ import annotations

import json
import textwrap

import ibis
import pytest

from marivo.preview import PreviewLimitError, PreviewResult
from marivo.semantic._registry_bridge import get_metric_ir
from marivo.semantic.catalog import SemanticCatalog, SemanticKind
from marivo.semantic.errors import ErrorKind, SemanticLoadFailed, SemanticRuntimeError

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
    from marivo.datasource.metadata import TableMetadata

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


def test_semantic_project_does_not_expose_catalog_list_surface(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    removed = (
        "list_domains",
        "list_datasources",
        "list_entities",
        "list_dimensions",
        "list_time_dimensions",
        "list_metrics",
        "list_relationships",
        "get_entity",
        "get_metric",
    )
    for name in removed:
        assert not hasattr(project, name), f"{name} should be available through ms.load()"


# ---------------------------------------------------------------------------
# get_dataset / get_datasource / get_field / get_metric
# ---------------------------------------------------------------------------


def test_catalog_get_entity(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    entity = SemanticCatalog(project).get("sales.orders")
    assert entity.name == "orders"
    assert entity.kind == SemanticKind.ENTITY


def test_catalog_get_entity_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    with pytest.raises(SemanticRuntimeError):
        SemanticCatalog(project).get("nonexistent")


def test_catalog_get_metric(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    metric = SemanticCatalog(project).get("sales.total_revenue")
    assert metric.name == "total_revenue"
    assert metric.kind == SemanticKind.METRIC


def test_catalog_get_metric_not_found(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    with pytest.raises(SemanticRuntimeError):
        SemanticCatalog(project).get("nonexistent")


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
        get_metric_ir(project, "sales.total_revenue")


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
        get_metric_ir(project, "sales.total_revenue")


def test_require_registry_uses_project_not_loaded_error_kind(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        },
        load=False,
    )
    with pytest.raises(SemanticLoadFailed) as exc_info:
        get_metric_ir(project, "sales.total_revenue")
    errors = exc_info.value.errors
    assert len(errors) == 1
    assert errors[0].kind == "project_not_loaded"
    assert errors[0].constraint_id == "project_loaded_required"
    assert "ms.load()" in (errors[0].hint or "")
    assert "catalog.list" not in (errors[0].hint or "")


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
# Default datasource access (replaces bind_datasource_access)
# ---------------------------------------------------------------------------


@pytest.fixture
def semantic_project_with_duckdb(tmp_path):
    """Create a SemanticProject backed by a real file-backed DuckDB datasource."""
    from marivo.datasource.authoring import DatasourceSpec
    from marivo.datasource.store import save_one

    marivo_root = tmp_path / ".marivo"
    semantic_root = marivo_root / "semantic"
    semantic_root.mkdir(parents=True, exist_ok=True)

    # Create a file-backed DuckDB with the orders table
    db_path = tmp_path / "data.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-02-01')"
    )
    con.disconnect()

    # Register the datasource pointing to the file-backed DB
    save_one(
        DatasourceSpec(name="warehouse", backend_type="duckdb", path=str(db_path)),
        project_root=tmp_path,
    )

    # Write the semantic model files
    (semantic_root / "sales").mkdir(parents=True, exist_ok=True)
    (semantic_root / "sales" / "__init__.py").write_text("")
    (semantic_root / "sales" / "_domain.py").write_text(_DOMAIN_PY)
    (semantic_root / "sales" / "objects.py").write_text(_FULL_DOMAIN_PY)

    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=tmp_path)
    project.load()
    return project


def test_materialize_requires_explicit_factory(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    with pytest.raises(SemanticRuntimeError) as exc_info:
        project.materialize_dataset("sales.orders")
    assert exc_info.value.kind == ErrorKind.BACKEND_FACTORY_REQUIRED


def test_materialize_with_explicit_factory(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    table = project.materialize_dataset("sales.orders", backend_factory=backend_factory)
    assert hasattr(table, "columns")


def test_preview_dataset_defaults_to_project_datasource(semantic_project_with_duckdb) -> None:
    project = semantic_project_with_duckdb
    result = project.preview_dataset("sales.orders")
    assert isinstance(result, PreviewResult)
    assert result.returned_row_count >= 0  # no bind_datasource_access, no explicit factory


def test_preview_metric_with_default_factory(semantic_project_with_duckdb) -> None:
    project = semantic_project_with_duckdb
    result = project.preview_metric("sales.total_revenue", limit=2)
    assert isinstance(result, PreviewResult)


def test_default_factory_backends_are_closed(semantic_project_with_duckdb, monkeypatch) -> None:
    import marivo.semantic.reader as reader_mod

    closed: list[bool] = []
    real_connect = reader_mod._default_connect

    def tracking_connect(name: str, project_root=None):
        backend = real_connect(name, project_root=project_root)
        real_disconnect = backend.disconnect

        def spy_disconnect() -> None:
            closed.append(True)
            real_disconnect()

        monkeypatch.setattr(backend, "disconnect", spy_disconnect, raising=False)
        return backend

    monkeypatch.setattr(reader_mod, "_default_connect", tracking_connect)
    project = semantic_project_with_duckdb
    project.preview_dataset("sales.orders")
    assert closed and all(closed)


def test_injected_factory_is_never_closed(semantic_project_with_duckdb) -> None:
    project = semantic_project_with_duckdb
    db_path = project.semantic_root.parent.parent / "data.duckdb"
    con = ibis.duckdb.connect(str(db_path))
    project.preview_dataset("sales.orders", backend_factory=lambda name: con)
    # still usable: semantic must not have disconnected the injected backend
    assert con.raw_sql("SELECT 1") is not None
    con.disconnect()


def test_materialize_dataset_requires_explicit_factory(semantic_project_with_duckdb) -> None:
    project = semantic_project_with_duckdb
    with pytest.raises(SemanticRuntimeError) as excinfo:
        project.materialize_dataset("sales.orders")
    assert "backend_factory" in str(excinfo.value)


def test_audit_datasources_reports_missing(semantic_project_with_duckdb) -> None:
    project = semantic_project_with_duckdb
    report = project.audit_datasources()
    assert report.missing == []
    assert report.present == ["warehouse"]


def test_readiness_uses_default_factory(semantic_project_factory, backend_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/objects.py": _FULL_DOMAIN_PY,
        }
    )
    report = project.readiness()
    assert report.status in ("ready", "ready_with_warnings", "warning", "blocked")
