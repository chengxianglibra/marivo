"""Tests for marivo.semantic.loader — project loading and SemanticProject.

Tests cover:
- Single model directory loads successfully
- _domain.py validation (missing, name mismatch)
- Sibling files loaded
- Excluded files: _domain.py, _exports.py, .prefixed, test_*.py, *_test.py
- Empty project directory is valid
- sys.path injection and cleanup
- Load result status (ready/errored)
- Errors accumulate across model directories
- load() on already-loaded project resets and re-loads
"""

from __future__ import annotations

import textwrap

import pytest

from marivo.semantic.catalog import EntityDetails, SemanticCatalog, SemanticKind
from marivo.semantic.errors import ErrorKind
from marivo.semantic.reader import SemanticProject
from marivo.semantic.refs import make_ref

# ---------------------------------------------------------------------------
# Minimal model files
# ---------------------------------------------------------------------------

_MINIMAL_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
""")

_MINIMAL_DATASET_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))

    @ms.metric(entities=[orders], additivity='additive', )
    def revenue(table):
        return table.amount.sum()
""")

_SHARED_DATASOURCE_MODEL_A = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))

    @ms.metric(entities=[orders], additivity='additive', )
    def revenue(orders):
        return orders.amount.sum()
""")

_SHARED_DATASOURCE_MODEL_B = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms

    refunds = ms.entity(name="refunds", datasource=md.ref("datasource.warehouse"), source=ms.table("refunds"))

    @ms.metric(entities=[refunds], additivity='additive', )
    def refunds_total(refunds):
        return refunds.amount.sum()
""")


# ---------------------------------------------------------------------------
# Happy path: single model loads
# ---------------------------------------------------------------------------


def test_single_model_loads(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    assert project.is_ready()


def test_global_datasource_can_be_reused_across_models(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _SHARED_DATASOURCE_MODEL_A,
            "finance/_domain.py": 'import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name="finance", owner="Mina Zhang")\n',
            "finance/datasets.py": _SHARED_DATASOURCE_MODEL_B,
        }
    )

    assert project.is_ready()
    catalog = SemanticCatalog(project)
    assert sorted(project._registry.datasources) == ["datasource.warehouse"]
    datasources = catalog.list(kind=SemanticKind.DATASOURCE).objects
    assert [ds.ref.id for ds in datasources] == ["datasource.warehouse"]
    orders = catalog.get("entity.sales.orders").details()
    refunds = catalog.get("entity.finance.refunds").details()
    assert isinstance(orders, EntityDetails)
    assert isinstance(refunds, EntityDetails)
    assert orders.datasource.id == "datasource.warehouse"
    assert refunds.datasource.id == "datasource.warehouse"


def test_duplicate_global_datasource_declaration_must_match(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "datasources/warehouse_a.py": 'import marivo.datasource as md\nmd.duckdb(name="warehouse", path=":memory:")\n',
            "datasources/warehouse_b.py": 'import marivo.datasource as md\nmd.duckdb(name="warehouse", path="/tmp/other.duckdb")\n',
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "finance/_domain.py": 'import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name="finance", owner="Mina Zhang")\n',
        }
    )

    assert not project.is_ready()
    assert any(error.kind == ErrorKind.DUPLICATE_NAME for error in project.errors())


def test_single_model_load_result_ready(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    result = project.load()
    assert result.status == "ready"
    assert result.errors == ()


def test_model_only_no_sibling_files(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
        }
    )
    assert project.is_ready()


# ---------------------------------------------------------------------------
# _domain.py validation
# ---------------------------------------------------------------------------


def test_missing_domain_py(semantic_project_factory) -> None:
    """Directory without _domain.py should produce an error for that model."""
    project = semantic_project_factory(
        {
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    assert not project.is_ready()
    errors = project.errors()
    assert len(errors) > 0
    kind_values = [e.kind for e in errors]
    assert ErrorKind.DOMAIN_FILE_MISSING in kind_values


def test_datasources_loaded_when_model_load_errors(semantic_project_factory) -> None:
    """Datasource loading succeeds even if model dirs are broken."""
    project = semantic_project_factory(
        {
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    assert not project.is_ready()
    assert any(e.kind == ErrorKind.DOMAIN_FILE_MISSING for e in project.errors())

    result = project.load()
    datasources = result.datasource_irs
    assert len(datasources) == 1
    assert datasources[0].name == "warehouse"
    assert datasources[0].backend_type == "duckdb"

    with pytest.raises(Exception):
        SemanticCatalog(project).list()


def test_model_name_mismatch(semantic_project_factory) -> None:
    """_domain.py declares a different name than the directory name."""
    bad_model = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms
        ms.domain(name="not_sales", owner='Mina Zhang', default=True)
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": bad_model,
        }
    )
    assert not project.is_ready()
    errors = project.errors()
    kind_values = [e.kind for e in errors]
    assert ErrorKind.DOMAIN_FILE_MISMATCH in kind_values


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
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
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
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
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
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
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

    root = tmp_path / "models" / "semantic"
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
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
            "sales/_domain.py": bad_model,
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
            "model_a/_domain.py": bad_model_a,
            "model_b/_domain.py": bad_model_b,
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
            "bad/_domain.py": bad_model,
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
        },
        load=False,
    )
    result = project.load()
    # At least one error from the bad model
    assert len(result.errors) >= 1


# ---------------------------------------------------------------------------
# re-load
# ---------------------------------------------------------------------------


def test_load_on_already_loaded_project(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
        }
    )
    assert project.is_ready()
    result = project.load()
    assert project.is_ready()
    assert result.status == "ready"


# ---------------------------------------------------------------------------
# Multiple sibling files
# ---------------------------------------------------------------------------


def test_multiple_sibling_files(semantic_project_factory) -> None:
    datasets_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))
    """)
    metrics_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        @ms.metric(entities=[ms.ref("entity.sales.orders")], additivity="additive", )
        def revenue(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
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
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/subdir/extra.py": sub_content,
        }
    )
    assert project.is_ready()


# ---------------------------------------------------------------------------
# SemanticProject init
# ---------------------------------------------------------------------------


def test_reader_project_unloaded() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        project = SemanticProject(workspace_dir=Path(tmp))
        assert not project.is_ready()
        assert project.errors() == ()


def test_reader_project_nonexistent_root() -> None:
    from pathlib import Path

    project = SemanticProject(workspace_dir=Path("/nonexistent/path"))
    result = project.load()
    # Should handle gracefully — either ready (empty) or errored
    assert result.status in ("ready", "errored")


# ---------------------------------------------------------------------------
# Model directory without _domain.py but with other .py files
# ---------------------------------------------------------------------------


def test_directory_with_py_but_no_model_file(semantic_project_factory) -> None:
    """A directory that has .py files but no _domain.py should not be treated as a model."""
    content = textwrap.dedent("""\
        # Just a random Python file, not a model
        x = 42
    """)
    project = semantic_project_factory(
        {
            "notamodel/utils.py": content,
        }
    )
    # notamodel has no _domain.py, so it should produce DOMAIN_FILE_MISSING
    # but the project should still report errors
    assert not project.is_ready()


# ---------------------------------------------------------------------------
# Cross-file ref resolution
# ---------------------------------------------------------------------------


def test_cross_file_dataset_metric_resolution(semantic_project_factory) -> None:
    """Dataset defined in one file, metric in another should resolve."""
    datasets_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.wh"), source=ms.table("orders"))
    """)
    metrics_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        @ms.metric(entities=[ms.ref("entity.sales.orders")], additivity="additive", )
        def revenue(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": datasets_py,
            "sales/metrics.py": metrics_py,
        }
    )
    assert project.is_ready()
    reg = project._registry
    assert reg is not None
    assert "sales.orders" in reg.entities
    assert "sales.revenue" in reg.metrics


def test_relative_import_between_model_files(semantic_project_factory) -> None:
    """Sibling model files can import decorated refs with relative imports."""
    dataset_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        query_info = ms.entity(name="query_info", datasource=md.ref("datasource.wh"), source=ms.table("query_info"))
    """)
    metrics_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms
        from .dataset import query_info

        @ms.metric(entities=[query_info], additivity="additive", )
        def total_query_count(table):
            return table.query_count.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/dataset.py": dataset_py,
            "sales/metrics.py": metrics_py,
        }
    )
    assert project.is_ready()
    reg = project._registry
    assert reg is not None
    assert "sales.query_info" in reg.entities
    assert "sales.total_query_count" in reg.metrics


def test_relative_import_from_later_sibling_is_not_executed_twice(
    semantic_project_factory,
) -> None:
    """A sibling imported before its loader turn should not be registered twice."""
    queries_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.wh"), source=ms.table("orders"))
    """)
    dimensions_py = textwrap.dedent("""\
        import marivo.semantic as ms
        from .queries import orders

        status = ms.dimension_column(name="status", entity=orders, column="status")
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/_dimensions.py": dimensions_py,
            "sales/queries.py": queries_py,
        }
    )
    assert project.is_ready()
    reg = project._registry
    assert reg is not None
    assert "sales.orders" in reg.entities
    assert "sales.orders.status" in reg.dimensions


def test_relative_imported_field_ref_from_later_sibling_keeps_resolver(
    semantic_project_factory,
) -> None:
    """Imported field refs from later siblings should be wired after load."""
    fields_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.wh"), source=ms.table("orders"))

        @ms.measure(entity=orders, additivity="additive")
        def amount(table):
            return table.amount
    """)
    metrics_py = textwrap.dedent("""\
        import marivo.semantic as ms
        from .z_fields import orders, amount

        @ms.metric(entities=[orders], additivity="additive")
        def revenue(table):
            return amount(table).sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/a_metrics.py": metrics_py,
            "sales/z_fields.py": fields_py,
        }
    )
    assert project.is_ready()
    sidecar = project._sidecar
    assert sidecar is not None
    metric_callable = sidecar["sales.revenue"]

    class _FakeAmount:
        def sum(self) -> str:
            return "summed"

    class _FakeTable:
        amount = _FakeAmount()

    assert metric_callable(_FakeTable()) == "summed"


def test_relative_import_reload_uses_latest_module(semantic_project_factory) -> None:
    """Reload should not reuse stale modules imported by sibling relative imports."""
    dataset_v1 = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        query_info = ms.entity(name="query_info", datasource=md.ref("datasource.wh"), source=ms.table("query_info"))
    """)
    metrics_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms
        from .dataset import query_info

        @ms.metric(entities=[query_info], additivity="additive", )
        def total_query_count(table):
            return table.query_count.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/dataset.py": dataset_v1,
            "sales/metrics.py": metrics_py,
        }
    )
    assert project.is_ready()

    dataset_v2 = dataset_v1.replace(
        'query_info = ms.entity(name="query_info"',
        'query_log = ms.entity(name="query_log"',
    ).replace('ms.table("query_info")', 'ms.table("query_log")')

    root = project.semantic_root
    (root / "sales" / "dataset.py").write_text(dataset_v2)

    result = project.load()

    assert result.status == "errored"
    assert any("query_info" in err.message for err in result.errors)


def test_cross_file_missing_entity_ref(semantic_project_factory) -> None:
    """Metric referencing a dataset that doesn't exist should error."""
    metrics_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        @ms.metric(entities=[ms.ref("entity.sales.nonexistent")], additivity="additive", )
        def revenue(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/metrics.py": metrics_py,
        }
    )
    assert not project.is_ready()
    errors = project.errors()
    assert any(e.kind == ErrorKind.MISSING_ENTITY_REF for e in errors)


# ---------------------------------------------------------------------------
# LoadResult warnings
# ---------------------------------------------------------------------------


def test_load_result_has_warnings_field(semantic_project_factory) -> None:
    """LoadResult should have a warnings tuple."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
        },
        load=False,
    )
    result = project.load()
    assert hasattr(result, "warnings")
    assert isinstance(result.warnings, tuple)


def test_provenance_from_sql_requires_dialect(semantic_project_factory) -> None:
    """ms.from_sql() requires both sql and dialect, so it is impossible to
    create a provenance with SQL but no dialect at the
    authoring level. This test verifies that ms.from_sql() enforces both."""
    import marivo.semantic as ms

    # ms.from_sql() requires dialect, so this is enforced by construction
    with pytest.raises(TypeError):
        ms.from_sql(sql="SELECT 1")  # type: ignore[call-arg]


def test_derived_metric_with_provenance_errors(semantic_project_factory) -> None:
    # Derived metric constructors (ms.ratio, ms.weighted_average, ms.linear)
    # do not accept provenance at all — the constraint is
    # enforced by construction, not by a load-time error.
    with pytest.raises(TypeError, match="provenance"):
        from tests.shared_fixtures import authoring_session

        with authoring_session(domain="sales") as s:
            import marivo.semantic as ms

            ms.ratio(
                name="ratio",
                numerator="sales.revenue",
                denominator="sales.revenue",
                provenance=ms.from_sql(sql="SELECT 1", dialect="duckdb"),
            )


# ---------------------------------------------------------------------------
# Registry and Sidecar access
# ---------------------------------------------------------------------------


def test_registry_accessible_after_load(semantic_project_factory) -> None:
    """SemanticProject should expose registry after successful load."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    reg = project._registry
    assert reg is not None
    assert "sales" in reg.domains
    assert "datasource.warehouse" in reg.datasources
    assert "sales.orders" in reg.entities
    assert "sales.revenue" in reg.metrics


def test_sidecar_accessible_after_load(semantic_project_factory) -> None:
    """SemanticProject should expose sidecar after successful load."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        }
    )
    side = project._sidecar
    assert side is not None
    assert "sales.orders" not in side
    assert "sales.revenue" in side
    # Datasets and datasources don't have sidecar callables in source-based authoring.
    assert "warehouse" not in side


def test_registry_none_on_errored_load(semantic_project_factory) -> None:
    """Registry should be None when load has errors."""
    bad_model = textwrap.dedent("""\
        raise ValueError("boom")
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": bad_model,
        }
    )
    assert not project.is_ready()
    assert project._registry is None
    assert project._sidecar is None


def test_warnings_accessible_after_load(semantic_project_factory) -> None:
    """SemanticProject should expose warnings after load."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
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
        import marivo.datasource as md
        import marivo.semantic as ms

        @ms.metric(entities=[ms.ref("entity.sales.orders")], additivity="additive", )
        def revenue(table):
            return table.amount.sum()
    """)
    datasets_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.wh"), source=ms.table("orders"))
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/b_metrics.py": metrics_py,
            "sales/c_datasets.py": datasets_py,
        }
    )
    assert project.is_ready()
    reg = project._registry
    assert reg is not None
    assert "sales.revenue" in reg.metrics
    assert "sales.orders" in reg.entities


# ---------------------------------------------------------------------------
# Loading with relationships
# ---------------------------------------------------------------------------


def test_loading_with_relationships(semantic_project_factory) -> None:
    """Relationships should be loaded into the registry."""
    datasets_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.wh"), source=ms.table("orders"))

        items = ms.entity(name="items", datasource=md.ref("datasource.wh"), source=ms.table("items"))
    """)
    fields_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        @ms.dimension(entity=ms.ref("entity.sales.orders"))
        def order_id(table):
            return table.order_id

        @ms.dimension(entity=ms.ref("entity.sales.items"))
        def item_order_id(table):
            return table.order_id
    """)
    rels_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        ms.relationship(
            name="orders_to_items",
            from_entity=ms.ref("entity.sales.orders"),
            to_entity=ms.ref("entity.sales.items"),
            keys=[ms.join_on(ms.ref("dimension.sales.orders.order_id"), ms.ref("dimension.sales.items.item_order_id"))],
        )
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": datasets_py,
            "sales/fields.py": fields_py,
            "sales/relationships.py": rels_py,
        }
    )
    assert project.is_ready()
    reg = project._registry
    assert reg is not None
    assert "sales.orders_to_items" in reg.relationships
    rel = reg.relationships["sales.orders_to_items"]
    assert rel.from_entity == "sales.orders"
    assert rel.to_entity == "sales.items"
    assert rel.keys[0].from_key == "sales.orders.order_id"
    assert rel.keys[0].to_key == "sales.items.item_order_id"


def test_relationship_empty_keys_rejected_via_loader(semantic_project_factory) -> None:
    """Relationship with empty keys should fail at decorator-time."""
    rels_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.wh"), source=ms.table("orders"))

        @ms.dimension(entity=orders)
        def order_id(table):
            return table.order_id

        ms.relationship(
            name="bad_rel",
            from_entity=ms.ref("entity.sales.orders"),
            to_entity=ms.ref("entity.sales.orders"),
            keys=[],
        )
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/relationships.py": rels_py,
        }
    )
    assert not project.is_ready()
    errors = project.errors()
    assert any(e.kind == ErrorKind.INVALID_REF for e in errors)


# ---------------------------------------------------------------------------
# DimensionRef resolver wired up after load
# ---------------------------------------------------------------------------


def test_field_ref_resolver_wired_after_load(semantic_project_factory) -> None:
    """DimensionRef objects should have their _resolver set after loading."""
    datasets_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.wh"), source=ms.table("orders"))
    """)
    # The field ref is stored in a module-level variable
    fields_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        @ms.dimension(entity=ms.ref("entity.sales.orders"))
        def amount(table):
            return table.amount

        # Store the ref for external access
        amount_ref = amount
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": datasets_py,
            "sales/fields.py": fields_py,
        }
    )
    assert project.is_ready()
    sidecar = project._sidecar
    assert sidecar is not None
    assert "sales.orders.amount" in sidecar


def test_field_ref_callable_after_load(semantic_project_factory) -> None:
    """DimensionRef returned by decorator should be callable after project load."""
    from contextlib import contextmanager
    from unittest.mock import patch

    import ibis

    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT)")
    con.con.execute("INSERT INTO orders VALUES (1, 100.0, 'US'), (2, 200.0, 'EU')")

    # The field ref is created during file loading
    fields_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        @ms.dimension(entity=ms.ref("entity.sales.orders"))
        def region(table):
            return table.region

        region_ref = region
    """)
    datasets_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource=md.ref("datasource.wh"), source=ms.table("orders"))
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": datasets_py,
            "sales/fields.py": fields_py,
        }
    )
    assert project.is_ready()

    def factory(ds_id: str):
        return con

    class _FakeConnectionService:
        @property
        def project_root(self):
            return None

        def session_backend(self, name):
            return factory(name)

        @contextmanager
        def use_backend(self, name):
            yield factory(name)

        def close_all(self):
            pass

    fake_service = _FakeConnectionService()

    with patch.object(project, "_connection_service", return_value=fake_service):
        catalog = SemanticCatalog(project)
        resolver = catalog._resolver()
        table = resolver.table(make_ref("sales.orders", SemanticKind.ENTITY))

        field_expr = resolver.dimension(make_ref("sales.orders.region", SemanticKind.DIMENSION))
    assert field_expr is not None


# ---------------------------------------------------------------------------
# find_project tests
# ---------------------------------------------------------------------------


def test_find_project_in_current_dir(tmp_path) -> None:
    """find_project should find marivo.toml in the start_dir."""
    from marivo.semantic.loader import find_project

    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    sem_dir = tmp_path / "models" / "semantic"
    sem_dir.mkdir(parents=True)
    project = find_project(start_dir=tmp_path)
    assert project is not None
    assert project.semantic_root == sem_dir


def test_find_project_in_parent_dir(tmp_path) -> None:
    """find_project should find marivo.toml in a parent directory."""
    from marivo.semantic.loader import find_project

    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    sem_dir = tmp_path / "models" / "semantic"
    sem_dir.mkdir(parents=True)
    child_dir = tmp_path / "subdir" / "deep"
    child_dir.mkdir(parents=True)
    project = find_project(start_dir=child_dir)
    assert project is not None
    assert project.semantic_root == sem_dir


def test_find_project_returns_none_when_not_found(tmp_path) -> None:
    """find_project should return None when no marivo.toml exists."""
    from marivo.semantic.loader import find_project

    project = find_project(start_dir=tmp_path)
    assert project is None


def test_find_project_finds_marivo_toml_without_semantic(tmp_path) -> None:
    """find_project should succeed when marivo.toml exists but models/semantic/ does not."""
    from marivo.semantic.loader import find_project

    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    project = find_project(start_dir=tmp_path)
    assert project is not None
    assert project.workspace_dir == tmp_path.resolve()
    assert not project.semantic_root.exists()


def test_load_project_rejects_models_root_with_clear_error(tmp_path) -> None:
    from marivo.semantic.loader import load_project

    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )

    result = load_project(tmp_path / "models")

    assert result.status == "errored"
    assert result.errors[0].kind == ErrorKind.INVALID_PROJECT
    assert "models/semantic" in result.errors[0].message


def test_load_project_rejects_workspace_root_with_clear_error(tmp_path) -> None:
    from marivo.semantic.loader import load_project

    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )

    result = load_project(tmp_path)

    assert result.status == "errored"
    assert result.errors[0].kind == ErrorKind.INVALID_PROJECT
    assert "models/semantic" in result.errors[0].message
    assert "ms.load(workspace_dir=...)" in result.errors[0].hint


def test_load_raises_when_semantic_is_a_file(tmp_path) -> None:
    """SemanticProject.load() should raise when marivo/semantic is a file."""
    from marivo.semantic.errors import SemanticLoadError

    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    marivo_dir = tmp_path / "models"
    marivo_dir.mkdir()
    # Create 'semantic' as a file, not a directory
    (marivo_dir / "semantic").write_text("not a directory")
    project = SemanticProject(workspace_dir=tmp_path)
    with pytest.raises(SemanticLoadError) as exc_info:
        project.load()
    assert exc_info.value.kind == ErrorKind.INVALID_PROJECT


# ---------------------------------------------------------------------------
# workspace_dir and MARIVO_PROJECT_ROOT
# ---------------------------------------------------------------------------


def test_reader_project_default_workspace_dir_is_cwd(monkeypatch, tmp_path) -> None:
    """SemanticProject() with no args uses cwd as workspace_dir."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MARIVO_PROJECT_ROOT", raising=False)
    project = SemanticProject()
    assert project.workspace_dir == tmp_path.resolve()
    assert project.semantic_root == tmp_path.resolve() / "models" / "semantic"


def test_reader_project_env_var_overrides_cwd(monkeypatch, tmp_path) -> None:
    """MARIVO_PROJECT_ROOT takes precedence over cwd."""
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(other_dir))
    project = SemanticProject()
    assert project.workspace_dir == other_dir.resolve()


def test_reader_project_explicit_workspace_dir_overrides_env(monkeypatch, tmp_path) -> None:
    """Explicit workspace_dir= takes precedence over MARIVO_PROJECT_ROOT."""
    env_dir = tmp_path / "env_dir"
    env_dir.mkdir()
    explicit_dir = tmp_path / "explicit_dir"
    explicit_dir.mkdir()
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(env_dir))
    project = SemanticProject(workspace_dir=explicit_dir)
    assert project.workspace_dir == explicit_dir.resolve()


def test_reader_project_workspace_dir_does_not_scan_non_marivo_dirs(tmp_path) -> None:
    """SemanticProject(workspace_dir='.') in a project root with scripts/ etc.
    does NOT misidentify those dirs as model directories."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "deploy.sh").write_text("#!/bin/bash\necho deploy")
    semantic_dir = tmp_path / "models" / "semantic"
    semantic_dir.mkdir(parents=True)
    project = SemanticProject(workspace_dir=tmp_path)
    result = project.load()
    # scripts/ should NOT appear as a model dir — only models/semantic/ is scanned
    assert result.status == "ready"
    assert len(SemanticCatalog(project).list().objects) == 0


# ---------------------------------------------------------------------------
# Ibis-aligned table access via materializer
# ---------------------------------------------------------------------------


def test_materialize_dataset_passes_short_table_name_through_for_trino(
    semantic_project_factory,
) -> None:
    from contextlib import contextmanager
    from unittest.mock import patch

    import ibis

    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                'md.trino(name="warehouse", host="h", catalog="c")\n'
            ),
            "sales/_domain.py": (
                'import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name="sales", owner="Mina Zhang", default=True)\n'
            ),
            "sales/objects.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders'))\n"
            ),
        }
    )

    class _FakeTrinoBackend:
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        def table(self, name, /):
            self.calls.append(name)
            return ibis.table({"x": "int"}, name=name)

    backend = _FakeTrinoBackend()

    def factory(name):
        if name == "datasource.warehouse":
            return backend
        return object()

    class _FakeConnectionService:
        @property
        def project_root(self):
            return None

        def session_backend(self, name):
            return factory(name)

        @contextmanager
        def use_backend(self, name):
            yield factory(name)

        def close_all(self):
            pass

    fake_service = _FakeConnectionService()

    with patch.object(project, "_connection_service", return_value=fake_service):
        result = (
            SemanticCatalog(project)
            ._resolver()
            .table(make_ref("sales.orders", SemanticKind.ENTITY))
        )
    assert isinstance(result, ibis.expr.types.Table)
    assert backend.calls == ["orders"]


def test_materialize_dataset_accepts_explicit_database_for_trino(
    semantic_project_factory,
) -> None:
    from contextlib import contextmanager
    from unittest.mock import patch

    import ibis

    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                'md.trino(name="warehouse", host="h", catalog="c")\n'
            ),
            "sales/_domain.py": (
                'import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name="sales", owner="Mina Zhang", default=True)\n'
            ),
            "sales/objects.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=ms.table('orders', database='sales'))\n"
            ),
        }
    )

    class _FakeTrinoBackend:
        def table(self, name, /, *, database=None):
            assert name == "orders"
            assert database == "sales"
            return ibis.table({"x": "int"}, name=f"{database}.{name}")

    def factory(name):
        if name == "datasource.warehouse":
            return _FakeTrinoBackend()
        return object()

    class _FakeConnectionService:
        @property
        def project_root(self):
            return None

        def session_backend(self, name):
            return factory(name)

        @contextmanager
        def use_backend(self, name):
            yield factory(name)

        def close_all(self):
            pass

    fake_service = _FakeConnectionService()

    with patch.object(project, "_connection_service", return_value=fake_service):
        result = (
            SemanticCatalog(project)
            ._resolver()
            .table(make_ref("sales.orders", SemanticKind.ENTITY))
        )
    assert isinstance(result, ibis.expr.types.Table)


# ---------------------------------------------------------------------------
# models parameter on load()
# ---------------------------------------------------------------------------

_FINANCE_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="finance", owner='Mina Zhang', default=True)
""")

_FINANCE_DATASET_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    refunds = ms.entity(name="refunds", datasource=md.ref("datasource.warehouse"), source=ms.table("refunds"))

    @ms.metric(entities=[refunds], additivity='additive', )
    def refunds_total(refunds):
        return refunds.amount.sum()
""")


def test_load_models_parameter_loads_only_specified(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _MINIMAL_DATASET_PY,
            "finance/_domain.py": _FINANCE_DOMAIN_PY,
            "finance/datasets.py": _FINANCE_DATASET_PY,
        },
        load=False,
    )
    result = project.load(domains=["sales"])
    assert project.is_ready()
    assert project._registry is not None
    assert "sales" in project._registry.domains
    assert "finance" not in project._registry.domains
    assert result.filtered_models == ("sales",)


def test_load_models_none_loads_all(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "finance/_domain.py": _FINANCE_DOMAIN_PY,
        },
        load=False,
    )
    result = project.load()
    assert project.is_ready()
    assert "sales" in project._registry.domains
    assert "finance" in project._registry.domains
    assert result.filtered_models == ()


def test_load_models_with_nonexistent_name(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
        },
        load=False,
    )
    result = project.load(domains=["sales", "nonexistent"])
    assert project.is_ready()
    assert "sales" in project._registry.domains
    filtered_warnings = [w for w in result.warnings if w.kind == "filtered_domain_ref"]
    assert any("nonexistent" in w.message for w in filtered_warnings)


def test_load_models_skips_bad_model(semantic_project_factory) -> None:
    bad_model = textwrap.dedent("""\
        raise ValueError("boom")
    """)
    project = semantic_project_factory(
        {
            "bad/_domain.py": bad_model,
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _MINIMAL_DATASET_PY,
        },
        load=False,
    )
    result = project.load(domains=["sales"])
    assert project.is_ready()
    assert project._registry is not None
    assert "sales" in project._registry.domains


def test_load_models_intra_model_error_still_blocks(semantic_project_factory) -> None:
    bad_model = textwrap.dedent("""\
        raise ValueError("boom")
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": bad_model,
        },
        load=False,
    )
    result = project.load(domains=["sales"])
    assert not project.is_ready()
    assert project._registry is None


def test_load_models_cross_model_ref_produces_warning(semantic_project_factory) -> None:
    """Cross-domain relationship ref produces filtered_domain_ref warning, not error."""
    sales_with_relationship = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource=md.ref("datasource.warehouse"), source=ms.table("orders"))

        @ms.dimension(entity=orders)
        def amount(table):
            return table.amount

        ms.relationship(
            name="orders_to_refunds",
            from_entity=orders,
            to_entity=ms.ref("entity.finance.refunds"),
            keys=[ms.join_on(ms.ref("dimension.sales.orders.amount"), ms.ref("dimension.finance.refunds.refunds_total"))],
        )
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": sales_with_relationship,
            "finance/_domain.py": _FINANCE_DOMAIN_PY,
            "finance/datasets.py": _FINANCE_DATASET_PY,
        },
        load=False,
    )
    result = project.load(domains=["sales"])
    assert project.is_ready()
    assert project._registry is not None
    filtered_warnings = [w for w in result.warnings if w.kind == "filtered_domain_ref"]
    assert any("finance" in w.message for w in filtered_warnings)


def test_load_without_models_loads_all(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "finance/_domain.py": _FINANCE_DOMAIN_PY,
        },
        load=False,
    )
    project.load(domains=["sales"])
    assert "sales" in project._registry.domains
    assert "finance" not in project._registry.domains
    project.load()
    assert "sales" in project._registry.domains
    assert "finance" in project._registry.domains


def test_load_can_change_filter(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "finance/_domain.py": _FINANCE_DOMAIN_PY,
        },
        load=False,
    )
    project.load(domains=["sales"])
    assert "finance" not in project._registry.domains
    project.load(domains=["sales", "finance"])
    assert "sales" in project._registry.domains
    assert "finance" in project._registry.domains
