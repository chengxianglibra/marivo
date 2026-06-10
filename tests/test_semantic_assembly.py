"""Tests for marivo.semantic.validator — assembly-time validation.

Tests cover:
- Missing entity ref -> MISSING_ENTITY_REF
- Missing dimension ref -> MISSING_DIMENSION_REF
- Missing metric ref -> MISSING_METRIC_REF
- Hour time dimension without required_prefix -> HOUR_TIME_DIMENSION_PREFIX_MISSING
- Invalid relationship endpoint -> INVALID_RELATIONSHIP_ENDPOINT
- String refs produce warnings
- Cross-file refs resolve correctly
- Cross-model cycle detection
- Unverified provenance warnings
- Valid project produces no errors
"""

from __future__ import annotations

import dataclasses
import textwrap

import pytest

from marivo.semantic.errors import ErrorKind, WarningKind
from marivo.semantic.ir import (
    AiContextIR,
    DatasourceAiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    DecompositionIR,
    DimensionIR,
    DimensionKind,
    DomainIR,
    EntityIR,
    MetricIR,
    ProvenanceIR,
    RelationshipIR,
    SourceLocation,
    TableSourceIR,
)
from marivo.semantic.reader import SemanticProject
from marivo.semantic.validator import Registry, assembly_validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LOC = SourceLocation(file="<test>", line=0)


def _make_registry(**overrides: object) -> Registry:
    """Create a Registry with some standard test objects."""
    registry = Registry()
    registry.models["sales"] = DomainIR(
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
    registry.datasets["sales.orders"] = EntityIR(
        semantic_id="sales.orders",
        domain="sales",
        name="orders",
        datasource="wh",
        source=TableSourceIR(table="orders"),
        primary_key=(),
        description=None,
        ai_context=AiContextIR(),
        python_symbol="orders",
        location=_LOC,
    )
    registry.fields["sales.orders.amount"] = DimensionIR(
        semantic_id="sales.orders.amount",
        domain="sales",
        entity="sales.orders",
        name="amount",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=False,
        kind=DimensionKind.CATEGORICAL,
        data_type=None,
        granularity=None,
        required_prefix=None,
        python_symbol="amount",
        location=_LOC,
    )
    registry.fields["sales.orders.order_date"] = DimensionIR(
        semantic_id="sales.orders.order_date",
        domain="sales",
        entity="sales.orders",
        name="order_date",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="date",
        granularity="day",
        required_prefix=None,
        python_symbol="order_date",
        location=_LOC,
    )
    registry.metrics["sales.revenue"] = MetricIR(
        semantic_id="sales.revenue",
        domain="sales",
        name="revenue",
        entities=("sales.orders",),
        is_derived=False,
        decomposition=DecompositionIR(kind="sum"),
        provenance=ProvenanceIR(verification_mode="python_native"),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc123",
        python_symbol="revenue",
        location=_LOC,
        additivity="additive",
    )
    return registry


# ---------------------------------------------------------------------------
# Missing dataset ref
# ---------------------------------------------------------------------------


def test_missing_entity_ref_on_dimension() -> None:
    registry = _make_registry()
    # Add a field referencing a non-existent dataset
    registry.fields["sales.nonexistent.bad_field"] = DimensionIR(
        semantic_id="sales.nonexistent.bad_field",
        domain="sales",
        entity="sales.nonexistent",
        name="bad_field",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=False,
        kind=DimensionKind.CATEGORICAL,
        data_type=None,
        granularity=None,
        required_prefix=None,
        python_symbol="bad_field",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.MISSING_ENTITY_REF for e in errors)


def test_missing_entity_ref_on_metric() -> None:
    registry = _make_registry()
    registry.metrics["sales.bad_metric"] = MetricIR(
        semantic_id="sales.bad_metric",
        domain="sales",
        name="bad_metric",
        entities=("sales.nonexistent",),
        is_derived=False,
        decomposition=DecompositionIR(kind="sum"),
        provenance=ProvenanceIR(verification_mode="python_native"),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc",
        python_symbol="bad_metric",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.MISSING_ENTITY_REF for e in errors)


def test_missing_datasource_ref_on_dataset() -> None:
    registry = _make_registry()
    registry.datasets["sales.bad_ds"] = EntityIR(
        semantic_id="sales.bad_ds",
        domain="sales",
        name="bad_ds",
        datasource="sales.nonexistent_wh",
        source=TableSourceIR(table="bad_ds"),
        primary_key=(),
        description=None,
        ai_context=AiContextIR(),
        python_symbol="bad_ds",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.MISSING_ENTITY_REF for e in errors)


# ---------------------------------------------------------------------------
# Missing metric ref
# ---------------------------------------------------------------------------


def test_missing_metric_ref_in_decomposition() -> None:
    registry = _make_registry()
    registry.metrics["sales.ratio_metric"] = MetricIR(
        semantic_id="sales.ratio_metric",
        domain="sales",
        name="ratio_metric",
        entities=(),
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
    registry.fields["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="timestamp",
        granularity="hour",
        required_prefix=None,
        python_symbol="order_hour",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.HOUR_TIME_DIMENSION_PREFIX_MISSING for e in errors)


def test_hour_only_string_time_field_without_required_prefix() -> None:
    registry = _make_registry()
    registry.fields["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="string",
        granularity="hour",
        required_prefix=None,
        python_symbol="order_hour",
        location=_LOC,
        format=None,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.HOUR_TIME_DIMENSION_PREFIX_MISSING for e in errors)


def test_hour_only_integer_int_time_field_without_required_prefix() -> None:
    registry = _make_registry()
    registry.fields["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="integer",
        granularity="hour",
        required_prefix=None,
        python_symbol="order_hour",
        location=_LOC,
        format=None,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.HOUR_TIME_DIMENSION_PREFIX_MISSING for e in errors)


def test_complete_hour_string_time_field_without_required_prefix() -> None:
    registry = _make_registry()
    registry.fields["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="string",
        granularity="hour",
        required_prefix=None,
        python_symbol="order_hour",
        location=_LOC,
        format="%Y%m%d%H",
    )
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.HOUR_TIME_DIMENSION_PREFIX_MISSING for e in errors)


def test_strptime_hour_only_without_required_prefix() -> None:
    """format='%H' is an hour-only format and requires required_prefix."""
    registry = _make_registry()
    registry.fields["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="string",
        granularity="hour",
        required_prefix=None,
        python_symbol="order_hour",
        location=_LOC,
        format="%H",
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.HOUR_TIME_DIMENSION_PREFIX_MISSING for e in errors)


def test_strptime_hour_with_date_without_required_prefix() -> None:
    """format='%Y-%m-%d %H' includes date and does NOT require required_prefix."""
    registry = _make_registry()
    registry.fields["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="string",
        granularity="hour",
        required_prefix=None,
        python_symbol="order_hour",
        location=_LOC,
        format="%Y-%m-%d %H",
    )
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.HOUR_TIME_DIMENSION_PREFIX_MISSING for e in errors)


def test_hour_time_field_with_required_prefix_ok() -> None:
    registry = _make_registry()
    registry.fields["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="timestamp",
        granularity="hour",
        required_prefix="order_date",  # Points to valid field
        python_symbol="order_hour",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.HOUR_TIME_DIMENSION_PREFIX_MISSING for e in errors)


def test_hour_time_field_with_required_prefix_name_ok() -> None:
    registry = _make_registry()
    registry.fields["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="string",
        granularity="hour",
        required_prefix="order_date",
        python_symbol="order_hour",
        location=_LOC,
        format=None,
    )
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.MISSING_DIMENSION_REF for e in errors)


def test_hour_time_field_with_invalid_prefix() -> None:
    registry = _make_registry()
    registry.fields["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="timestamp",
        granularity="hour",
        required_prefix="sales.orders.nonexistent_date",  # Not in registry
        python_symbol="order_hour",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(
        e.kind == ErrorKind.MISSING_DIMENSION_REF and "sales.orders.order_hour" in e.semantic_refs
        for e in errors
    )


def test_hour_time_dimension_prefix_must_reference_time_dimension() -> None:
    registry = _make_registry()
    registry.fields["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="string",
        granularity="hour",
        required_prefix="amount",
        python_symbol="order_hour",
        location=_LOC,
        format=None,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(
        e.kind == ErrorKind.MISSING_DIMENSION_REF and "sales.orders.order_hour" in e.semantic_refs
        for e in errors
    )


def test_day_time_field_no_prefix_required() -> None:
    """Day (or coarser) granularity does not require required_prefix."""
    registry = _make_registry()
    # sales.order_date is already day granularity with no prefix — should be fine
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.HOUR_TIME_DIMENSION_PREFIX_MISSING for e in errors)


def _cast_partition_time_field(table):
    return table.dt.cast("date")


def _raw_partition_time_field(table):
    return table.dt


def test_cast_partition_time_field_emits_pushdown_advisory_warning() -> None:
    registry = _make_registry()
    registry.fields["sales.orders.order_date"] = DimensionIR(
        semantic_id="sales.orders.order_date",
        domain="sales",
        entity="sales.orders",
        name="order_date",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="date",
        granularity="day",
        required_prefix=None,
        python_symbol="order_date",
        location=_LOC,
    )

    errors, warnings = assembly_validate(
        registry, sidecar={"sales.orders.order_date": _cast_partition_time_field}
    )

    assert errors == []
    assert any(w.kind == WarningKind.TIME_DIMENSION_PUSHDOWN_ADVISORY for w in warnings)


def test_raw_partition_time_field_has_no_pushdown_advisory_warning() -> None:
    registry = _make_registry()
    registry.fields["sales.orders.order_date"] = DimensionIR(
        semantic_id="sales.orders.order_date",
        domain="sales",
        entity="sales.orders",
        name="order_date",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="string",
        granularity="day",
        required_prefix=None,
        python_symbol="order_date",
        location=_LOC,
        format="%Y%m%d",
    )

    errors, warnings = assembly_validate(
        registry, sidecar={"sales.orders.order_date": _raw_partition_time_field}
    )

    assert errors == []
    assert not any(w.kind == WarningKind.TIME_DIMENSION_PUSHDOWN_ADVISORY for w in warnings)


# ---------------------------------------------------------------------------
# Invalid relationship endpoint
# ---------------------------------------------------------------------------


def test_invalid_relationship_from_dataset() -> None:
    registry = _make_registry()
    registry.relationships["sales.bad_rel"] = RelationshipIR(
        semantic_id="sales.bad_rel",
        domain="sales",
        name="bad_rel",
        from_entity="sales.nonexistent",
        to_entity="sales.orders",
        from_dimensions=("sales.orders.amount",),
        to_dimensions=("sales.orders.amount",),
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
        domain="sales",
        name="bad_rel",
        from_entity="sales.orders",
        to_entity="sales.nonexistent",
        from_dimensions=("sales.orders.amount",),
        to_dimensions=("sales.orders.amount",),
        description=None,
        ai_context=AiContextIR(),
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.INVALID_RELATIONSHIP_ENDPOINT for e in errors)


def test_invalid_relationship_dimension_ref() -> None:
    registry = _make_registry()
    registry.relationships["sales.bad_rel"] = RelationshipIR(
        semantic_id="sales.bad_rel",
        domain="sales",
        name="bad_rel",
        from_entity="sales.orders",
        to_entity="sales.orders",
        from_dimensions=("sales.orders.nonexistent_field",),
        to_dimensions=("sales.orders.amount",),
        description=None,
        ai_context=AiContextIR(),
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(e.kind == ErrorKind.MISSING_DIMENSION_REF for e in errors)


def test_valid_relationship_no_errors() -> None:
    registry = _make_registry()
    registry.relationships["sales.self_rel"] = RelationshipIR(
        semantic_id="sales.self_rel",
        domain="sales",
        name="self_rel",
        from_entity="sales.orders",
        to_entity="sales.orders",
        from_dimensions=("sales.orders.amount",),
        to_dimensions=("sales.orders.amount",),
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
        domain="sales",
        name="bad_arity",
        from_entity="sales.orders",
        to_entity="sales.orders",
        from_dimensions=("sales.orders.amount", "sales.orders.order_date"),
        to_dimensions=("sales.orders.amount",),
        description=None,
        ai_context=AiContextIR(),
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(
        e.kind == ErrorKind.MISSING_DIMENSION_REF and "sales.bad_arity" in e.semantic_refs
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
        domain="sales",
        name="metric_a",
        entities=(),
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
        domain="sales",
        name="metric_b",
        entities=(),
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
        domain="sales",
        name="double_revenue",
        entities=(),
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
# Verification mode validation
# ---------------------------------------------------------------------------


def test_sql_parity_metric_without_source_dialect_errors() -> None:
    registry = _make_registry()
    registry.metrics["sales.unverified_metric"] = MetricIR(
        semantic_id="sales.unverified_metric",
        domain="sales",
        name="unverified_metric",
        entities=("sales.orders",),
        is_derived=False,
        decomposition=DecompositionIR(kind="sum"),
        provenance=ProvenanceIR(
            source_sql="SELECT SUM(amount) FROM orders",
            verification_mode="sql_parity",
        ),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc",
        python_symbol="unverified_metric",
        location=_LOC,
    )
    errors, warnings = assembly_validate(registry)
    assert any(
        e.kind == ErrorKind.SOURCE_SQL_MISSING and "sales.unverified_metric" in e.semantic_refs
        for e in errors
    )
    assert warnings == []


def test_python_native_provenance_no_warning() -> None:
    registry = _make_registry()
    registry.metrics["sales.native_metric"] = MetricIR(
        semantic_id="sales.native_metric",
        domain="sales",
        name="native_metric",
        entities=("sales.orders",),
        is_derived=False,
        decomposition=DecompositionIR(kind="sum"),
        provenance=ProvenanceIR(verification_mode="python_native"),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc",
        python_symbol="native_metric",
        location=_LOC,
        additivity="additive",
    )
    errors, warnings = assembly_validate(registry)
    assert not errors
    assert not warnings


def test_no_source_sql_no_warning() -> None:
    """python_native metric without source_sql should not produce warnings."""
    registry = _make_registry()
    registry.metrics["sales.revenue"] = dataclasses.replace(
        registry.metrics["sales.revenue"],
        provenance=ProvenanceIR(verification_mode="python_native"),
    )
    errors, warnings = assembly_validate(registry)
    assert not errors
    assert not warnings


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
            "import marivo.datasource as md\n"
            "md.datasource(name='wh', backend_type='duckdb', path=':memory:')\n"
        )
        for rel, src in files.items():
            full = root / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(src)
        project = SemanticProject(workspace_dir=tmp_path)
        if load:
            project.load()
        return project

    return _make


_MINIMAL_DOMAIN_PY = textwrap.dedent("""\
    import marivo.semantic as ms
    ms.domain(name="sales", default=True)
""")


def test_cross_file_dataset_metric_refs(semantic_project_factory) -> None:
    """Dataset in one file, metric referencing it in another should work."""
    datasets_py = textwrap.dedent("""\
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource="wh", source=ms.table("orders"))
    """)
    metrics_py = textwrap.dedent("""\
        import marivo.semantic as ms

        @ms.metric(entities=["sales.orders"], additivity="additive", decomposition=ms.sum(), verification_mode="python_native",)
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
    assert "sales.orders" in reg.datasets
    assert "sales.revenue" in reg.metrics


def test_duplicate_default_time_dimension_raises() -> None:
    registry = _make_registry()
    # Add a second time field with is_default=True on the same dataset
    registry.fields["sales.orders.order_date2"] = DimensionIR(
        semantic_id="sales.orders.order_date2",
        domain="sales",
        entity="sales.orders",
        name="order_date2",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="date",
        granularity="day",
        required_prefix=None,
        python_symbol="order_date2",
        location=_LOC,
        is_default=True,
    )
    # Mark the existing order_date as default too
    registry.fields["sales.orders.order_date"] = dataclasses.replace(
        registry.fields["sales.orders.order_date"],
        is_default=True,
    )

    errors, _warnings = assembly_validate(registry)

    assert any(e.kind == ErrorKind.DUPLICATE_DEFAULT_TIME_DIMENSION for e in errors), (
        f"Expected DUPLICATE_DEFAULT_TIME_DIMENSION, got: {[e.kind for e in errors]}"
    )


def test_cross_file_refs_with_missing_dataset(semantic_project_factory) -> None:
    """Metric referencing a non-existent dataset should produce an error."""
    metrics_py = textwrap.dedent("""\
        import marivo.semantic as ms

        @ms.metric(entities=["sales.nonexistent"], additivity="additive", decomposition=ms.sum(), verification_mode="python_native",)
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


def test_registry_and_sidecar_populated(semantic_project_factory) -> None:
    """After loading, registry and sidecar should be populated."""
    datasets_py = textwrap.dedent("""\
        import marivo.semantic as ms

        orders = ms.entity(name="orders", datasource="wh", source=ms.table("orders"))
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": datasets_py,
        }
    )
    assert project.is_ready()
    reg = project._registry
    assert reg is not None
    assert "sales" in reg.models
    assert "wh" in reg.datasources
    assert "sales.orders" in reg.datasets
    side = project._sidecar
    assert side is not None
    assert "sales.orders" not in side


def test_warnings_in_load_result(semantic_project_factory) -> None:
    """LoadResult should expose an empty warnings tuple when no warnings exist."""
    metrics_py = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="wh", source=ms.table("orders"))

        @ms.metric(
            entities=[orders],
            additivity='additive',
            decomposition=ms.sum(),
            verification_mode="python_native",
        )
        def revenue(table):
            return table.amount.sum()
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/metrics.py": metrics_py,
        },
        load=False,
    )
    result = project.load()
    assert project.is_ready()
    assert result.warnings == ()


def test_hour_time_field_without_prefix_via_loader(semantic_project_factory) -> None:
    """Hour-only string time field with bare %H format requires required_prefix.

    A bare ``%H`` is a valid strptime format so it passes decorator validation,
    but hour-only string fields still require a ``required_prefix`` pointing to a
    day-level time field for pushdown (assembly guard).
    """
    fields_py = textwrap.dedent("""\
        import marivo.semantic as ms

        @ms.time_dimension(
            entity="sales.orders",
            data_type="string",
            granularity="hour",
            date_format="%H",
        )
        def order_hour(table):
            return table.order_hour
    """)
    datasource = textwrap.dedent("""\
        import marivo.semantic as ms
        orders = ms.entity(name="orders", datasource="wh", source=ms.table("orders"))
    """)
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": datasource,
            "sales/fields.py": fields_py,
        }
    )
    assert not project.is_ready()
    errors = project.errors()
    assert any(e.kind == ErrorKind.HOUR_TIME_DIMENSION_PREFIX_MISSING for e in errors)


def test_invalid_relationship_via_loader(semantic_project_factory) -> None:
    """Relationship referencing a non-existent dataset should fail via loader."""
    rels_py = textwrap.dedent("""\
        import marivo.semantic as ms

        ms.relationship(
            name="bad_rel",
            from_entity="sales.nonexistent",
            to_entity="sales.also_nonexistent",
            from_dimensions=["sales.orders.f1"],
            to_dimensions=["sales.orders.f2"],
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
    assert any(e.kind == ErrorKind.INVALID_RELATIONSHIP_ENDPOINT for e in errors)


def test_field_ir_accepts_is_default_flag() -> None:
    field = DimensionIR(
        semantic_id="sales.orders.log_date",
        domain="sales",
        entity="sales.orders",
        name="log_date",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="date",
        granularity="day",
        required_prefix=None,
        python_symbol="log_date",
        location=_LOC,
        is_default=True,
    )
    assert field.is_default is True


def test_field_ir_is_default_defaults_to_false() -> None:
    field = DimensionIR(
        semantic_id="sales.orders.log_date",
        domain="sales",
        entity="sales.orders",
        name="log_date",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        data_type="date",
        granularity="day",
        required_prefix=None,
        python_symbol="log_date",
        location=_LOC,
    )
    assert field.is_default is False


# ---------------------------------------------------------------------------
# did_you_mean in MISSING_*_REF errors
# ---------------------------------------------------------------------------


def test_missing_datasource_ref_includes_did_you_mean() -> None:
    registry = _make_registry()
    registry.datasets["sales.bad_ds"] = EntityIR(
        semantic_id="sales.bad_ds",
        domain="sales",
        name="bad_ds",
        datasource="w",  # close to "wh"
        source=TableSourceIR(table="bad_ds"),
        primary_key=(),
        description=None,
        ai_context=AiContextIR(),
        python_symbol="bad_ds",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    dym_errors = [e for e in errors if e.kind == ErrorKind.MISSING_ENTITY_REF]
    assert len(dym_errors) >= 1
    dym = dym_errors[0].details.get("did_you_mean", [])
    assert "wh" in dym


def test_missing_entity_ref_on_dimension_includes_did_you_mean() -> None:
    registry = _make_registry()
    registry.fields["sales.ordrs.bad_field"] = DimensionIR(
        semantic_id="sales.ordrs.bad_field",
        domain="sales",
        entity="sales.ordrs",  # close to "sales.orders"
        name="bad_field",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=False,
        kind=DimensionKind.CATEGORICAL,
        data_type=None,
        granularity=None,
        required_prefix=None,
        python_symbol="bad_field",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    dym_errors = [e for e in errors if e.kind == ErrorKind.MISSING_ENTITY_REF]
    assert len(dym_errors) >= 1
    dym = dym_errors[0].details.get("did_you_mean", [])
    assert "sales.orders" in dym


def test_missing_metric_ref_includes_did_you_mean() -> None:
    registry = _make_registry()
    registry.metrics["sales.ratio_metric"] = MetricIR(
        semantic_id="sales.ratio_metric",
        domain="sales",
        name="ratio_metric",
        entities=(),
        is_derived=True,
        decomposition=DecompositionIR(
            kind="ratio",
            components={"numerator": "sales.revenu", "denominator": "sales.revenue"},
        ),
        provenance=ProvenanceIR(),
        description=None,
        ai_context=AiContextIR(),
        body_ast_hash="abc",
        python_symbol="ratio_metric",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    dym_errors = [e for e in errors if e.kind == ErrorKind.MISSING_METRIC_REF]
    assert len(dym_errors) >= 1
    dym = dym_errors[0].details.get("did_you_mean", [])
    assert "sales.revenue" in dym


def test_missing_dimension_ref_includes_did_you_mean() -> None:
    registry = _make_registry()
    registry.relationships["rel"] = RelationshipIR(
        semantic_id="rel",
        domain="sales",
        name="rel",
        from_entity="sales.orders",
        to_entity="sales.orders",
        from_dimensions=("sales.orders.amoun",),  # close to "sales.orders.amount"
        to_dimensions=("sales.orders.amount",),
        description=None,
        ai_context=AiContextIR(),
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    dym_errors = [e for e in errors if e.kind == ErrorKind.MISSING_DIMENSION_REF]
    assert len(dym_errors) >= 1
    dym = dym_errors[0].details.get("did_you_mean", [])
    assert "sales.orders.amount" in dym


def test_semantic_error_str_renders_did_you_mean() -> None:
    from marivo.semantic.errors import SemanticLoadError

    err = SemanticLoadError(
        kind="MISSING_ENTITY_REF",
        message="references unknown datasource 'w'.",
        refs=("sales.bad_ds", "w"),
        details={"missing_ref": "w", "did_you_mean": ["wh"]},
    )

    rendered = str(err)
    assert "Did you mean: wh" in rendered


def test_semantic_error_str_omits_did_you_mean_when_empty() -> None:
    from marivo.semantic.errors import SemanticLoadError

    err = SemanticLoadError(
        kind="MISSING_ENTITY_REF",
        message="references unknown datasource 'xyz'.",
        refs=("sales.bad_ds", "xyz"),
        details={"missing_ref": "xyz", "did_you_mean": []},
    )

    rendered = str(err)
    assert "Did you mean" not in rendered
