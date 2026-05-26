"""Tests for marivo.semantic_py.validator — assembly-time validation.

Tests cover:
- Missing dataset ref -> MISSING_DATASET_REF
- Missing field ref -> MISSING_FIELD_REF
- Missing metric ref -> MISSING_METRIC_REF
- Hour time field without required_prefix -> HOUR_TIME_FIELD_PREFIX_MISSING
- Invalid relationship endpoint -> INVALID_RELATIONSHIP_ENDPOINT
- String refs produce warnings
- Cross-file refs resolve correctly
- Cross-model cycle detection
- Unverified provenance warnings
- Valid project produces no errors
"""

from __future__ import annotations

import textwrap

import pytest

from marivo.semantic_py.errors import ErrorKind, WarningKind
from marivo.semantic_py.ir import (
    AiContextIR,
    DatasetIR,
    DatasourceAiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    DecompositionIR,
    FieldIR,
    MetricIR,
    ModelIR,
    ProvenanceIR,
    RelationshipIR,
    SourceLocation,
)
from marivo.semantic_py.reader import SemanticProject
from marivo.semantic_py.validator import Registry, assembly_validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LOC = SourceLocation(file="<test>", line=0)


def _make_registry(**overrides: object) -> Registry:
    """Create a Registry with some standard test objects."""
    registry = Registry()
    registry.models["sales"] = ModelIR(
        name="sales",
        description=None,
        default=True,
        ai_context=AiContextIR(),
        location=_LOC,
    )
    registry.datasources["wh"] = DatasourceIR(
        semantic_id="wh",
        name="wh",
        backend_type="duckdb",
        fields={},
        env_refs={},
        description=None,
        ai_context=DatasourceAiContextIR(),
        python_symbol="wh",
        location=DatasourceSourceLocation(file="<test>", line=0),
    )
    registry.datasets["sales.orders"] = DatasetIR(
        semantic_id="sales.orders",
        model="sales",
        name="orders",
        datasource="wh",
        primary_key=(),
        description=None,
        ai_context=AiContextIR(),
        python_symbol="orders",
        location=_LOC,
    )
    registry.fields["sales.amount"] = FieldIR(
        semantic_id="sales.amount",
        model="sales",
        dataset="sales.orders",
        name="amount",
        description=None,
        ai_context=AiContextIR(),
        is_time_field=False,
        data_type=None,
        granularity=None,
        required_prefix=None,
        python_symbol="amount",
        location=_LOC,
    )
    registry.fields["sales.order_date"] = FieldIR(
        semantic_id="sales.order_date",
        model="sales",
        dataset="sales.orders",
        name="order_date",
        description=None,
        ai_context=AiContextIR(),
        is_time_field=True,
        data_type="date",
        granularity="day",
        required_prefix=None,
        python_symbol="order_date",
        location=_LOC,
    )
    registry.metrics["sales.revenue"] = MetricIR(
        semantic_id="sales.revenue",
        model="sales",
        name="revenue",
        datasets=("sales.orders",),
        is_derived=False,
        decomposition=DecompositionIR(kind="sum"),
        provenance=ProvenanceIR(),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc123",
        python_symbol="revenue",
        location=_LOC,
    )
    return registry


# ---------------------------------------------------------------------------
# Missing dataset ref
# ---------------------------------------------------------------------------


def test_missing_dataset_ref_on_field() -> None:
    registry = _make_registry()
    # Add a field referencing a non-existent dataset
    registry.fields["sales.bad_field"] = FieldIR(
        semantic_id="sales.bad_field",
        model="sales",
        dataset="sales.nonexistent",
        name="bad_field",
        description=None,
        ai_context=AiContextIR(),
        is_time_field=False,
        data_type=None,
        granularity=None,
        required_prefix=None,
        python_symbol="bad_field",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.MISSING_DATASET_REF for e in errors)


def test_missing_dataset_ref_on_metric() -> None:
    registry = _make_registry()
    registry.metrics["sales.bad_metric"] = MetricIR(
        semantic_id="sales.bad_metric",
        model="sales",
        name="bad_metric",
        datasets=("sales.nonexistent",),
        is_derived=False,
        decomposition=DecompositionIR(kind="sum"),
        provenance=ProvenanceIR(),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc",
        python_symbol="bad_metric",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.MISSING_DATASET_REF for e in errors)


def test_missing_datasource_ref_on_dataset() -> None:
    registry = _make_registry()
    registry.datasets["sales.bad_ds"] = DatasetIR(
        semantic_id="sales.bad_ds",
        model="sales",
        name="bad_ds",
        datasource="sales.nonexistent_wh",
        primary_key=(),
        description=None,
        ai_context=AiContextIR(),
        python_symbol="bad_ds",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.MISSING_DATASET_REF for e in errors)


# ---------------------------------------------------------------------------
# Missing metric ref
# ---------------------------------------------------------------------------


def test_missing_metric_ref_in_decomposition() -> None:
    registry = _make_registry()
    registry.metrics["sales.ratio_metric"] = MetricIR(
        semantic_id="sales.ratio_metric",
        model="sales",
        name="ratio_metric",
        datasets=(),
        is_derived=True,
        decomposition=DecompositionIR(
            kind="ratio",
            components={"numerator": "sales.nonexistent", "denominator": "sales.revenue"},
        ),
        provenance=ProvenanceIR(),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc",
        python_symbol="ratio_metric",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.MISSING_METRIC_REF for e in errors)


# ---------------------------------------------------------------------------
# Hour time field required_prefix validation
# ---------------------------------------------------------------------------


def test_timestamp_hour_time_field_without_required_prefix() -> None:
    registry = _make_registry()
    registry.fields["sales.order_hour"] = FieldIR(
        semantic_id="sales.order_hour",
        model="sales",
        dataset="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_field=True,
        data_type="timestamp",
        granularity="hour",
        required_prefix=None,
        python_symbol="order_hour",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.HOUR_TIME_FIELD_PREFIX_MISSING for e in errors)


def test_hour_only_string_time_field_without_required_prefix() -> None:
    registry = _make_registry()
    registry.fields["sales.order_hour"] = FieldIR(
        semantic_id="sales.order_hour",
        model="sales",
        dataset="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_field=True,
        data_type="string",
        granularity="hour",
        required_prefix=None,
        python_symbol="order_hour",
        location=_LOC,
        format="hh",
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.HOUR_TIME_FIELD_PREFIX_MISSING for e in errors)


def test_complete_hour_string_time_field_without_required_prefix() -> None:
    registry = _make_registry()
    registry.fields["sales.order_hour"] = FieldIR(
        semantic_id="sales.order_hour",
        model="sales",
        dataset="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_field=True,
        data_type="string",
        granularity="hour",
        required_prefix=None,
        python_symbol="order_hour",
        location=_LOC,
        format="yyyymmddhh",
    )
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.HOUR_TIME_FIELD_PREFIX_MISSING for e in errors)


def test_hour_time_field_with_required_prefix_ok() -> None:
    registry = _make_registry()
    registry.fields["sales.order_hour"] = FieldIR(
        semantic_id="sales.order_hour",
        model="sales",
        dataset="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_field=True,
        data_type="timestamp",
        granularity="hour",
        required_prefix="sales.order_date",  # Points to valid field
        python_symbol="order_hour",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.HOUR_TIME_FIELD_PREFIX_MISSING for e in errors)


def test_hour_time_field_with_invalid_prefix() -> None:
    registry = _make_registry()
    registry.fields["sales.order_hour"] = FieldIR(
        semantic_id="sales.order_hour",
        model="sales",
        dataset="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_field=True,
        data_type="timestamp",
        granularity="hour",
        required_prefix="sales.nonexistent_date",  # Not in registry
        python_symbol="order_hour",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(
        e.kind == ErrorKind.MISSING_FIELD_REF and "sales.order_hour" in e.semantic_refs
        for e in errors
    )


def test_hour_time_field_prefix_must_reference_time_field() -> None:
    registry = _make_registry()
    registry.fields["sales.order_hour"] = FieldIR(
        semantic_id="sales.order_hour",
        model="sales",
        dataset="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_field=True,
        data_type="string",
        granularity="hour",
        required_prefix="sales.amount",
        python_symbol="order_hour",
        location=_LOC,
        format="hh",
    )
    errors, _warnings = assembly_validate(registry)
    assert any(
        e.kind == ErrorKind.MISSING_FIELD_REF and "sales.order_hour" in e.semantic_refs
        for e in errors
    )


def test_day_time_field_no_prefix_required() -> None:
    """Day (or coarser) granularity does not require required_prefix."""
    registry = _make_registry()
    # sales.order_date is already day granularity with no prefix — should be fine
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.HOUR_TIME_FIELD_PREFIX_MISSING for e in errors)


# ---------------------------------------------------------------------------
# Invalid relationship endpoint
# ---------------------------------------------------------------------------


def test_invalid_relationship_from_dataset() -> None:
    registry = _make_registry()
    registry.relationships["sales.bad_rel"] = RelationshipIR(
        semantic_id="sales.bad_rel",
        model="sales",
        name="bad_rel",
        from_dataset="sales.nonexistent",
        to_dataset="sales.orders",
        from_fields=("sales.amount",),
        to_fields=("sales.amount",),
        description=None,
        ai_context=AiContextIR(),
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.INVALID_RELATIONSHIP_ENDPOINT for e in errors)


def test_invalid_relationship_to_dataset() -> None:
    registry = _make_registry()
    registry.relationships["sales.bad_rel"] = RelationshipIR(
        semantic_id="sales.bad_rel",
        model="sales",
        name="bad_rel",
        from_dataset="sales.orders",
        to_dataset="sales.nonexistent",
        from_fields=("sales.amount",),
        to_fields=("sales.amount",),
        description=None,
        ai_context=AiContextIR(),
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.INVALID_RELATIONSHIP_ENDPOINT for e in errors)


def test_invalid_relationship_field_ref() -> None:
    registry = _make_registry()
    registry.relationships["sales.bad_rel"] = RelationshipIR(
        semantic_id="sales.bad_rel",
        model="sales",
        name="bad_rel",
        from_dataset="sales.orders",
        to_dataset="sales.orders",
        from_fields=("sales.nonexistent_field",),
        to_fields=("sales.amount",),
        description=None,
        ai_context=AiContextIR(),
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.MISSING_FIELD_REF for e in errors)


def test_valid_relationship_no_errors() -> None:
    registry = _make_registry()
    registry.relationships["sales.self_rel"] = RelationshipIR(
        semantic_id="sales.self_rel",
        model="sales",
        name="self_rel",
        from_dataset="sales.orders",
        to_dataset="sales.orders",
        from_fields=("sales.amount",),
        to_fields=("sales.amount",),
        description=None,
        ai_context=AiContextIR(),
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    rel_errors = [e for e in errors if "sales.self_rel" in e.semantic_refs]
    assert len(rel_errors) == 0


def test_relationship_field_arity_mismatch() -> None:
    """Relationship with mismatched field arity should produce an error."""
    registry = _make_registry()
    registry.relationships["sales.bad_arity"] = RelationshipIR(
        semantic_id="sales.bad_arity",
        model="sales",
        name="bad_arity",
        from_dataset="sales.orders",
        to_dataset="sales.orders",
        from_fields=("sales.amount", "sales.order_date"),
        to_fields=("sales.amount",),
        description=None,
        ai_context=AiContextIR(),
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(
        e.kind == ErrorKind.MISSING_FIELD_REF and "sales.bad_arity" in e.semantic_refs
        for e in errors
    )


# ---------------------------------------------------------------------------
# Cross-model cycle detection
# ---------------------------------------------------------------------------


def test_metric_cycle_detected() -> None:
    registry = _make_registry()
    # Create a cycle: a -> b -> a
    registry.metrics["sales.metric_a"] = MetricIR(
        semantic_id="sales.metric_a",
        model="sales",
        name="metric_a",
        datasets=(),
        is_derived=True,
        decomposition=DecompositionIR(
            kind="sum",
            components={"x": "sales.metric_b"},
        ),
        provenance=ProvenanceIR(),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc",
        python_symbol="metric_a",
        location=_LOC,
    )
    registry.metrics["sales.metric_b"] = MetricIR(
        semantic_id="sales.metric_b",
        model="sales",
        name="metric_b",
        datasets=(),
        is_derived=True,
        decomposition=DecompositionIR(
            kind="sum",
            components={"x": "sales.metric_a"},
        ),
        provenance=ProvenanceIR(),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="def",
        python_symbol="metric_b",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.CROSS_MODEL_CYCLE for e in errors)


def test_no_cycle_when_valid() -> None:
    registry = _make_registry()
    # sales.revenue exists; a derived metric referencing it is fine
    registry.metrics["sales.double_revenue"] = MetricIR(
        semantic_id="sales.double_revenue",
        model="sales",
        name="double_revenue",
        datasets=(),
        is_derived=True,
        decomposition=DecompositionIR(
            kind="sum",
            components={"x": "sales.revenue"},
        ),
        provenance=ProvenanceIR(),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="ghi",
        python_symbol="double_revenue",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.CROSS_MODEL_CYCLE for e in errors)


# ---------------------------------------------------------------------------
# Unverified provenance warnings
# ---------------------------------------------------------------------------


def test_unverified_provenance_warning() -> None:
    registry = _make_registry()
    registry.metrics["sales.unverified_metric"] = MetricIR(
        semantic_id="sales.unverified_metric",
        model="sales",
        name="unverified_metric",
        datasets=("sales.orders",),
        is_derived=False,
        decomposition=DecompositionIR(kind="sum"),
        provenance=ProvenanceIR(
            source_sql="SELECT SUM(amount) FROM orders",
            declared_status="unverified",
        ),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc",
        python_symbol="unverified_metric",
        location=_LOC,
    )
    errors, warnings = assembly_validate(registry)
    assert not any(
        e.kind == ErrorKind.MISSING_DATASET_REF and "sales.unverified_metric" in e.semantic_refs
        for e in errors
    )
    assert any(
        w.kind == WarningKind.UNVERIFIED_PROVENANCE and "sales.unverified_metric" in w.refs
        for w in warnings
    )


def test_python_native_provenance_no_warning() -> None:
    registry = _make_registry()
    registry.metrics["sales.native_metric"] = MetricIR(
        semantic_id="sales.native_metric",
        model="sales",
        name="native_metric",
        datasets=("sales.orders",),
        is_derived=False,
        decomposition=DecompositionIR(kind="sum"),
        provenance=ProvenanceIR(
            source_sql="SELECT SUM(amount) FROM orders",
            declared_status="python_native",
        ),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc",
        python_symbol="native_metric",
        location=_LOC,
    )
    errors, warnings = assembly_validate(registry)
    assert not any(
        w.kind == WarningKind.UNVERIFIED_PROVENANCE and "sales.native_metric" in w.refs
        for w in warnings
    )


def test_no_source_sql_no_warning() -> None:
    """Metric without source_sql should not produce unverified warning."""
    registry = _make_registry()
    # sales.revenue has no source_sql — should not produce warning
    errors, warnings = assembly_validate(registry)
    assert not any(w.kind == WarningKind.UNVERIFIED_PROVENANCE for w in warnings)


# ---------------------------------------------------------------------------
# Valid project produces no errors
# ---------------------------------------------------------------------------


def test_valid_registry_no_errors() -> None:
    registry = _make_registry()
    errors, warnings = assembly_validate(registry)
    assert len(errors) == 0


def test_empty_registry_no_errors() -> None:
    registry = Registry()
    errors, warnings = assembly_validate(registry)
    assert len(errors) == 0


# ---------------------------------------------------------------------------
# Cross-file refs resolve correctly (integration via SemanticProject)
# ---------------------------------------------------------------------------


@pytest.fixture
def semantic_project_factory(tmp_path):
    """Factory that creates a SemanticProject from a dict of files."""

    def _make(files: dict[str, str], load: bool = True) -> SemanticProject:
        marivo_root = tmp_path / ".marivo"
        root = marivo_root / "semantic"
        root.mkdir(parents=True, exist_ok=True)
        datasource_root = marivo_root / "datasource"
        datasource_root.mkdir(parents=True, exist_ok=True)
        (datasource_root / "wh.py").write_text(
            "import marivo.datasource_py as md\n"
            "md.datasource(name='wh', backend_type='duckdb', path=':memory:')\n"
        )
        for rel, src in files.items():
            full = root / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(src)
        project = SemanticProject(root=root)
        if load:
            project.load()
        return project

    return _make


_MINIMAL_MODEL_PY = textwrap.dedent("""\
    import marivo.semantic_py as ms
    ms.model(name="sales", default=True)
""")


def test_cross_file_dataset_metric_refs(semantic_project_factory) -> None:
    """Dataset in one file, metric referencing it in another should work."""
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


def test_cross_file_refs_with_missing_dataset(semantic_project_factory) -> None:
    """Metric referencing a non-existent dataset should produce an error."""
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


def test_registry_and_sidecar_populated(semantic_project_factory) -> None:
    """After loading, registry and sidecar should be populated."""
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
        }
    )
    assert project.is_ready()
    reg = project.registry()
    assert reg is not None
    assert "sales" in reg.models
    assert "wh" in reg.datasources
    assert "sales.orders" in reg.datasets
    side = project.sidecar()
    assert side is not None
    assert "sales.orders" in side


def test_warnings_in_load_result(semantic_project_factory) -> None:
    """LoadResult should include warnings."""
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
    # Should have at least one unverified provenance warning
    assert len(result.warnings) > 0
    assert any(w.kind == WarningKind.UNVERIFIED_PROVENANCE for w in result.warnings)


def test_hour_time_field_without_prefix_via_loader(semantic_project_factory) -> None:
    """Hour-only string time field without required_prefix should fail via loader."""
    fields_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        @ms.time_field(
            dataset="sales.orders",
            data_type="string",
            granularity="hour",
            format="hh",
        )
        def order_hour(table):
            return table.order_hour
    """)
    datasource_py = textwrap.dedent("""\
        import marivo.semantic_py as ms
        @ms.dataset(datasource="wh")
        def orders(backend):
            return backend.table("orders")
    """)
    project = semantic_project_factory(
        {
            "sales/_model.py": _MINIMAL_MODEL_PY,
            "sales/datasets.py": datasource_py,
            "sales/fields.py": fields_py,
        }
    )
    assert not project.is_ready()
    errors = project.errors()
    assert any(e.kind == ErrorKind.HOUR_TIME_FIELD_PREFIX_MISSING for e in errors)


def test_invalid_relationship_via_loader(semantic_project_factory) -> None:
    """Relationship referencing a non-existent dataset should fail via loader."""
    rels_py = textwrap.dedent("""\
        import marivo.semantic_py as ms

        ms.relationship(
            name="bad_rel",
            from_="sales.nonexistent",
            to="sales.also_nonexistent",
            from_fields=["sales.f1"],
            to_fields=["sales.f2"],
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
    assert any(e.kind == ErrorKind.INVALID_RELATIONSHIP_ENDPOINT for e in errors)
