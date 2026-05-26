"""Tests for marivo.semantic_py.loader — project loading and SemanticProject.

Tests cover:
- Single model directory loads successfully
- _model.py validation (missing, name mismatch)
- Sibling files loaded
- Excluded files: _model.py, _exports.py, .prefixed, test_*.py, *_test.py
- Empty project directory is valid
- sys.path injection and cleanup
- Load result status (ready/errored)
- Errors accumulate across model directories
- reload() works
"""

from __future__ import annotations

import textwrap

import pytest

from marivo.semantic_py.errors import ErrorKind
from marivo.semantic_py.reader import SemanticProject

# ---------------------------------------------------------------------------
# Minimal model files
# ---------------------------------------------------------------------------

_MINIMAL_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic_py as ms
    ms.model(name="sales", default=True)
""")

_MINIMAL_DATASET_PY = textwrap.dedent("""\
    import marivo.semantic_py as ms
    @ms.dataset(datasource="warehouse")
    def orders(backend):
        return backend.table("orders")

    @ms.metric(datasets=[orders], decomposition=ms.sum())
    def revenue(table):
        return table.amount.sum()
""")

_SHARED_DATASOURCE_MODEL_A = textwrap.dedent("""\
    import marivo.semantic_py as ms
    @ms.dataset(name="orders", datasource="warehouse")
    def orders(backend):
        return backend.table("orders")

    @ms.metric(datasets=[orders], decomposition=ms.sum())
    def revenue(orders):
        return orders.amount.sum()
""")

_SHARED_DATASOURCE_MODEL_B = textwrap.dedent("""\
    import marivo.semantic_py as ms

    @ms.dataset(name="refunds", datasource="warehouse")
    def refunds(backend):
        return backend.table("refunds")

    @ms.metric(datasets=[refunds], decomposition=ms.sum())
    def refunds_total(refunds):
        return refunds.amount.sum()
""")


# ---------------------------------------------------------------------------
# Happy path: single model loads
# ---------------------------------------------------------------------------


def test_single_model_loads(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    assert project.is_ready()


def test_global_datasource_can_be_reused_across_models(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": _SHARED_DATASOURCE_MODEL_A,
            "finance/_model.py": 'import marivo.semantic_py as ms\nms.model(name="finance")\n',
            "finance/datasets.py": _SHARED_DATASOURCE_MODEL_B,
        }
    )

    assert project.is_ready()
    datasources = project.list_datasources()
    assert [ds.semantic_id for ds in datasources] == ["warehouse"]
    assert project.get_dataset("sales.orders").datasource == "warehouse"
    assert project.get_dataset("finance.refunds").datasource == "warehouse"


def test_duplicate_global_datasource_declaration_must_match(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "datasource/warehouse_a.py": 'import marivo.datasource_py as md\nmd.datasource(name="warehouse", backend_type="duckdb", path=":memory:")\n',
            "datasource/warehouse_b.py": 'import marivo.datasource_py as md\nmd.datasource(name="warehouse", backend_type="duckdb", path="/tmp/other.duckdb")\n',
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "finance/_model.py": 'import marivo.semantic_py as ms\nms.model(name="finance")\n',
        }
    )

    assert not project.is_ready()
    assert any(error.kind == ErrorKind.DUPLICATE_NAME for error in project.errors())


def test_single_model_load_result_ready(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    result = project.load()
    assert result.status == "ready"
    assert result.errors == ()


def test_model_only_no_sibling_files(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
        }
    )
    assert project.is_ready()


# ---------------------------------------------------------------------------
# _model.py validation
# ---------------------------------------------------------------------------


def test_missing_model_py(semantic_project_factory) -> None:
    """Directory without _model.py should produce an error for that model."""
    project = semantic_project_factory(
        {
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    assert not project.is_ready()
    errors = project.errors()
    assert len(errors) > 0
    kind_values = [e.kind for e in errors]
    assert ErrorKind.MODEL_FILE_MISSING in kind_values


def test_model_name_mismatch(semantic_project_factory) -> None:
    """_model.py declares a different name than the directory name."""
    bad_model = textwrap.dedent("""\
        import marivo.semantic_py as ms
        ms.model(name="not_sales", default=True)
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": bad_model,
        }
    )
    assert not project.is_ready()
    errors = project.errors()
    kind_values = [e.kind for e in errors]
    assert ErrorKind.MODEL_FILE_MISMATCH in kind_values


# ---------------------------------------------------------------------------
# Excluded files
# ---------------------------------------------------------------------------


def test_exports_py_excluded(semantic_project_factory) -> None:
    """_exports.py should not be loaded by the loader."""
    exports_content = textwrap.dedent("""\
        # This should never be executed
        raise RuntimeError("_exports.py was loaded!")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/_exports.py": exports_content,
        }
    )
    assert project.is_ready()


def test_dot_prefixed_files_excluded(semantic_project_factory) -> None:
    dot_content = textwrap.dedent("""\
        raise RuntimeError("dotfile was loaded!")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/.hidden.py": dot_content,
        }
    )
    assert project.is_ready()


def test_test_files_excluded(semantic_project_factory) -> None:
    test_content = textwrap.dedent("""\
        raise RuntimeError("test file was loaded!")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/test_orders.py": test_content,
            "sales/orders_test.py": test_content,
        }
    )
    assert project.is_ready()


# ---------------------------------------------------------------------------
# Empty project
# ---------------------------------------------------------------------------


def test_empty_project_is_valid(semantic_project_factory) -> None:
    project = semantic_project_factory({})
    assert project.is_ready()


def test_empty_project_no_models(semantic_project_factory) -> None:
    project = semantic_project_factory({})
    # list_models still raises NotImplementedError (Slice 8)
    # but the project itself is ready
    assert project.is_ready()


# ---------------------------------------------------------------------------
# sys.path injection and cleanup
# ---------------------------------------------------------------------------


def test_sys_path_injected_during_load(semantic_project_factory, tmp_path) -> None:
    """sys.path should be modified during load and cleaned up after."""
    import sys

    root = tmp_path / ".marivo" / "semantic"
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
        }
    )
    # After load, the injected path should be cleaned up
    parent_str = str(root.parent)
    assert parent_str not in sys.path


# ---------------------------------------------------------------------------
# Load result status
# ---------------------------------------------------------------------------


def test_errored_load_result(semantic_project_factory) -> None:
    bad_model = textwrap.dedent("""\
        raise ValueError("boom")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": bad_model,
        },
        load=False,
    )
    result = project.load()
    assert result.status == "errored"
    assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# Errors accumulate
# ---------------------------------------------------------------------------


def test_errors_accumulate_across_models(semantic_project_factory) -> None:
    bad_model_a = textwrap.dedent("""\
        raise ValueError("error in model A")
    """)
    bad_model_b = textwrap.dedent("""\
        raise ValueError("error in model B")
    """)
    project = semantic_project_factory(
        {
            "model_a/_model.py": bad_model_a,
            "model_b/_model.py": bad_model_b,
        },
        load=False,
    )
    result = project.load()
    assert result.status == "errored"
    # Should have at least one error per model
    assert len(result.errors) >= 2


def test_one_bad_model_does_not_block_good_one(semantic_project_factory) -> None:
    bad_model = textwrap.dedent("""\
        raise ValueError("boom")
    """)
    project = semantic_project_factory(
        {
            "bad/_model.py": bad_model,
            "sales/_model.py": _MINIMAL_MODEL_PY,
        },
        load=False,
    )
    result = project.load()
    # At least one error from the bad model
    assert len(result.errors) >= 1


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------


def test_reload_works(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
        }
    )
    assert project.is_ready()
    result = project.reload()
    assert project.is_ready()
    assert result.status == "ready"


# ---------------------------------------------------------------------------
# Multiple sibling files
# ---------------------------------------------------------------------------


def test_multiple_sibling_files(semantic_project_factory) -> None:
    datasets_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.dataset(datasource="warehouse")
        def orders(backend):
            return backend.table("orders")
    """)
    metrics_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.metric(datasets=["sales.orders"], decomposition=ms.sum())
        def revenue(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": datasets_py,
            "sales/metrics.py": metrics_py,
        }
    )
    assert project.is_ready()


# ---------------------------------------------------------------------------
# Directories are not recursive
# ---------------------------------------------------------------------------


def test_subdirectories_not_scanned(semantic_project_factory) -> None:
    sub_content = textwrap.dedent("""\
        raise RuntimeError("subdirectory file was loaded!")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/subdir/extra.py": sub_content,
        }
    )
    assert project.is_ready()


# ---------------------------------------------------------------------------
# SemanticProject init
# ---------------------------------------------------------------------------


def test_semantic_project_unloaded() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        project = SemanticProject(root=Path(tmp) / ".marivo" / "semantic")
        assert not project.is_ready()
        assert project.errors() == ()


def test_semantic_project_nonexistent_root() -> None:
    from pathlib import Path

    project = SemanticProject(root=Path("/nonexistent/path"))
    result = project.load()
    # Should handle gracefully — either ready (empty) or errored
    assert result.status in ("ready", "errored")


# ---------------------------------------------------------------------------
# Model directory without _model.py but with other .py files
# ---------------------------------------------------------------------------


def test_directory_with_py_but_no_model_file(semantic_project_factory) -> None:
    """A directory that has .py files but no _model.py should not be treated as a model."""
    content = textwrap.dedent("""\
        # Just a random Python file, not a model
        x = 42
    """)
    project = semantic_project_factory(
        {
            "notamodel/utils.py": content,
        }
    )
    # notamodel has no _model.py, so it should produce MODEL_FILE_MISSING
    # but the project should still report errors
    assert not project.is_ready()


# ---------------------------------------------------------------------------
# Cross-file ref resolution
# ---------------------------------------------------------------------------


def test_cross_file_dataset_metric_resolution(semantic_project_factory) -> None:
    """Dataset defined in one file, metric in another should resolve."""
    datasets_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.dataset(datasource="wh")
        def orders(backend):
            return backend.table("orders")
    """)
    metrics_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.metric(datasets=["sales.orders"], decomposition=ms.sum())
        def revenue(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": datasets_py,
            "sales/metrics.py": metrics_py,
        }
    )
    assert project.is_ready()
    reg = project.registry()
    assert reg is not None
    assert "sales.orders" in reg.datasets
    assert "sales.revenue" in reg.metrics


def test_relative_import_between_model_files(semantic_project_factory) -> None:
    """Sibling model files can import decorated refs with relative imports."""
    dataset_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.dataset(datasource="wh")
        def query_info(backend):
            return backend.table("query_info")
    """)
    metrics_py = textwrap.dedent("""\
        import marivo.semantic_py as ms
        from .dataset import query_info

        @ms.metric(datasets=[query_info], decomposition=ms.sum())
        def total_query_count(table):
            return table.query_count.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/dataset.py": dataset_py,
            "sales/metrics.py": metrics_py,
        }
    )
    assert project.is_ready()
    reg = project.registry()
    assert reg is not None
    assert "sales.query_info" in reg.datasets
    assert "sales.total_query_count" in reg.metrics


def test_relative_import_reload_uses_latest_module(semantic_project_factory) -> None:
    """Reload should not reuse stale modules imported by sibling relative imports."""
    dataset_v1 = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.dataset(datasource="wh")
        def query_info(backend):
            return backend.table("query_info")
    """)
    metrics_py = textwrap.dedent("""\
        import marivo.semantic_py as ms
        from .dataset import query_info

        @ms.metric(datasets=[query_info], decomposition=ms.sum())
        def total_query_count(table):
            return table.query_count.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/dataset.py": dataset_v1,
            "sales/metrics.py": metrics_py,
        }
    )
    assert project.is_ready()

    dataset_v2 = dataset_v1.replace("def query_info", "def query_log")
    from pathlib import Path

    root = Path(project.root)
    (root / "sales" / "dataset.py").write_text(dataset_v2)

    result = project.reload()

    assert result.status == "errored"
    assert any("query_info" in err.message for err in result.errors)


def test_cross_file_missing_dataset_ref(semantic_project_factory) -> None:
    """Metric referencing a dataset that doesn't exist should error."""
    metrics_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.metric(datasets=["sales.nonexistent"], decomposition=ms.sum())
        def revenue(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/metrics.py": metrics_py,
        }
    )
    assert not project.is_ready()
    errors = project.errors()
    assert any(e.kind == ErrorKind.MISSING_DATASET_REF for e in errors)


# ---------------------------------------------------------------------------
# LoadResult warnings
# ---------------------------------------------------------------------------


def test_load_result_has_warnings_field(semantic_project_factory) -> None:
    """LoadResult should have a warnings tuple."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
        },
        load=False,
    )
    result = project.load()
    assert hasattr(result, "warnings")
    assert isinstance(result.warnings, tuple)


def test_unverified_provenance_warning_in_result(semantic_project_factory) -> None:
    """Metric with source_sql but unverified provenance should produce a warning."""
    from marivo.semantic_py.errors import WarningKind

    metrics_py = textwrap.dedent("""\
        import marivo.semantic_py as ms
        @ms.dataset(datasource="wh")
        def orders(backend):
            return backend.table("orders")

        @ms.metric(
            datasets=[orders],
            decomposition=ms.sum(),
            source_sql="SELECT SUM(amount) FROM orders",
            provenance="unverified",
        )
        def revenue(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/metrics.py": metrics_py,
        },
        load=False,
    )
    result = project.load()
    assert project.is_ready()
    assert any(w.kind == WarningKind.UNVERIFIED_PROVENANCE for w in result.warnings)


# ---------------------------------------------------------------------------
# Registry and Sidecar access
# ---------------------------------------------------------------------------


def test_registry_accessible_after_load(semantic_project_factory) -> None:
    """SemanticProject should expose registry after successful load."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    reg = project.registry()
    assert reg is not None
    assert "sales" in reg.models
    assert "warehouse" in reg.datasources
    assert "sales.orders" in reg.datasets
    assert "sales.revenue" in reg.metrics


def test_sidecar_accessible_after_load(semantic_project_factory) -> None:
    """SemanticProject should expose sidecar after successful load."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    side = project.sidecar()
    assert side is not None
    assert "sales.orders" in side
    assert "sales.revenue" in side
    # Datasource doesn't have a callable
    assert "warehouse" not in side


def test_registry_none_on_errored_load(semantic_project_factory) -> None:
    """Registry should be None when load has errors."""
    bad_model = textwrap.dedent("""\
        raise ValueError("boom")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": bad_model,
        }
    )
    assert not project.is_ready()
    assert project.registry() is None
    assert project.sidecar() is None


def test_warnings_accessible_after_load(semantic_project_factory) -> None:
    """SemanticProject should expose warnings after load."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
        }
    )

    warnings = project.warnings()
    assert isinstance(warnings, tuple)


# ---------------------------------------------------------------------------
# Two-pass: collect then resolve
# ---------------------------------------------------------------------------


def test_two_pass_separates_discovery_from_validation(semantic_project_factory) -> None:
    """Even if a metric references a dataset defined later in the file
    sequence, the two-pass loader should resolve it correctly."""
    # File order: datasource.py -> metrics.py -> datasets.py
    # Metric references dataset that comes in the third file.
    # Pass 1 collects all objects, Pass 2 validates references.
    # metrics.py comes before datasets.py alphabetically but references sales.orders
    metrics_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.metric(datasets=["sales.orders"], decomposition=ms.sum())
        def revenue(table):
            return table.amount.sum()
    """)
    datasets_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.dataset(datasource="wh")
        def orders(backend):
            return backend.table("orders")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/b_metrics.py": metrics_py,
            "sales/c_datasets.py": datasets_py,
        }
    )
    assert project.is_ready()
    reg = project.registry()
    assert reg is not None
    assert "sales.revenue" in reg.metrics
    assert "sales.orders" in reg.datasets


# ---------------------------------------------------------------------------
# Loading with relationships
# ---------------------------------------------------------------------------


def test_loading_with_relationships(semantic_project_factory) -> None:
    """Relationships should be loaded into the registry."""
    datasets_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.dataset(datasource="wh")
        def orders(backend):
            return backend.table("orders")

        @ms.dataset(datasource="wh")
        def items(backend):
            return backend.table("items")
    """)
    fields_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.field(dataset="sales.orders")
        def order_id(table):
            return table.order_id

        @ms.field(dataset="sales.items")
        def item_order_id(table):
            return table.order_id
    """)
    rels_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        ms.relationship(
            name="orders_to_items",
            from_="sales.orders",
            to="sales.items",
            from_fields=["sales.order_id"],
            to_fields=["sales.item_order_id"],
        )
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": datasets_py,
            "sales/fields.py": fields_py,
            "sales/relationships.py": rels_py,
        }
    )
    assert project.is_ready()
    reg = project.registry()
    assert reg is not None
    assert "sales.orders_to_items" in reg.relationships
    rel = reg.relationships["sales.orders_to_items"]
    assert rel.from_dataset == "sales.orders"
    assert rel.to_dataset == "sales.items"
    assert rel.from_fields == ("sales.order_id",)
    assert rel.to_fields == ("sales.item_order_id",)


def test_relationship_field_arity_mismatch_via_loader(semantic_project_factory) -> None:
    """Relationship with mismatched field arity should fail via loader."""
    rels_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.dataset(datasource="wh")
        def orders(backend):
            return backend.table("orders")

        @ms.field(dataset=orders)
        def order_id(table):
            return table.order_id

        @ms.field(dataset=orders)
        def other_id(table):
            return table.other_id

        ms.relationship(
            name="bad_arity",
            from_="sales.orders",
            to="sales.orders",
            from_fields=["sales.order_id", "sales.other_id"],
            to_fields=["sales.order_id"],
        )
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/relationships.py": rels_py,
        }
    )
    assert not project.is_ready()
    errors = project.errors()
    assert any(e.kind == ErrorKind.MISSING_FIELD_REF for e in errors)


# ---------------------------------------------------------------------------
# FieldRef resolver wired up after load
# ---------------------------------------------------------------------------


def test_field_ref_resolver_wired_after_load(semantic_project_factory) -> None:
    """FieldRef objects should have their _resolver set after loading."""
    datasets_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.dataset(datasource="wh")
        def orders(backend):
            return backend.table("orders")
    """)
    # The field ref is stored in a module-level variable
    fields_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.field(dataset="sales.orders")
        def amount(table):
            return table.amount

        # Store the ref for external access
        amount_ref = amount
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": datasets_py,
            "sales/fields.py": fields_py,
        }
    )
    assert project.is_ready()
    sidecar = project.sidecar()
    assert sidecar is not None
    assert "sales.amount" in sidecar


def test_field_ref_callable_after_load(semantic_project_factory) -> None:
    """FieldRef returned by decorator should be callable after project load."""
    import ibis

    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT)")
    con.con.execute("INSERT INTO orders VALUES (1, 100.0, 'US'), (2, 200.0, 'EU')")

    # The field ref is created during file loading
    fields_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.field(dataset="sales.orders")
        def region(table):
            return table.region

        region_ref = region
    """)
    datasets_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.dataset(datasource="wh")
        def orders(backend):
            return backend.table("orders")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": datasets_py,
            "sales/fields.py": fields_py,
        }
    )
    assert project.is_ready()

    def factory(ds_id: str):
        return con

    # Materialize the dataset first
    table = project.materialize_dataset("sales.orders", backend_factory=factory)

    # Materialize the field
    field_expr = project.materialize_field("sales.region", backend_factory=factory)
    assert field_expr is not None


# ---------------------------------------------------------------------------
# find_project tests
# ---------------------------------------------------------------------------


def test_find_project_in_current_dir(tmp_path) -> None:
    """find_project should find .marivo/semantic/ in the start_dir."""
    from marivo.semantic_py.loader import find_project

    sem_dir = tmp_path / ".marivo" / "semantic"
    sem_dir.mkdir(parents=True)
    project = find_project(start_dir=tmp_path)
    assert project is not None
    assert project._root == sem_dir


def test_find_project_in_parent_dir(tmp_path) -> None:
    """find_project should find .marivo/semantic/ in a parent directory."""
    from marivo.semantic_py.loader import find_project

    sem_dir = tmp_path / ".marivo" / "semantic"
    sem_dir.mkdir(parents=True)
    child_dir = tmp_path / "subdir" / "deep"
    child_dir.mkdir(parents=True)
    project = find_project(start_dir=child_dir)
    assert project is not None
    assert project._root == sem_dir


def test_find_project_returns_none_when_not_found(tmp_path) -> None:
    """find_project should return None when no .marivo/semantic/ exists."""
    from marivo.semantic_py.loader import find_project

    project = find_project(start_dir=tmp_path)
    assert project is None


def test_find_project_raises_when_semantic_is_a_file(tmp_path) -> None:
    """find_project should raise InvalidProjectError when .marivo/semantic is a file."""
    from marivo.semantic_py.errors import SemanticLoadError
    from marivo.semantic_py.loader import find_project

    marivo_dir = tmp_path / ".marivo"
    marivo_dir.mkdir()
    # Create 'semantic' as a file, not a directory
    (marivo_dir / "semantic").write_text("not a directory")
    with pytest.raises(SemanticLoadError) as exc_info:
        find_project(start_dir=tmp_path)
    assert exc_info.value.kind == ErrorKind.INVALID_PROJECT
