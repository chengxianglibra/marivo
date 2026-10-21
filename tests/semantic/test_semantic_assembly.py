"""Tests for marivo.semantic.validator — assembly-time validation.

Tests cover:
- Missing entity ref -> MISSING_ENTITY_REF
- Missing dimension ref -> MISSING_DIMENSION_REF
- Missing metric ref -> MISSING_METRIC_REF
- HourPrefixParse prefix cross-reference validation
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

from marivo.refs import ref as ref_factory
from marivo.semantic._expression_binding import (
    CompiledExpressionSidecar,
    compile_expression_body,
)
from marivo.semantic.errors import ErrorKind, WarningKind
from marivo.semantic.ir import (
    AiContextIR,
    DatasourceAiContextIR,
    DatasourceIR,
    DatasourceSourceLocation,
    DateParse,
    DimensionIR,
    DimensionKind,
    DomainIR,
    EntityIR,
    HourPrefixParse,
    JoinKey,
    RelationshipIR,
    SourceLocation,
    StrptimeParse,
    TableSourceIR,
)
from marivo.semantic.reader import SemanticProject
from marivo.semantic.validator import Registry, assembly_validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LOC = SourceLocation(file="<test>", line=0)


def _time_dimension_sidecar(fn):
    field_ref = ref_factory.time_dimension("sales.orders.order_date")
    entity_ref = ref_factory.entity("sales.orders")
    return CompiledExpressionSidecar(
        bodies={
            field_ref: compile_expression_body(
                fn,
                owning_ref=field_ref,
                ordered_entity_refs=(entity_ref,),
            )
        },
        field_owners={field_ref: entity_ref},
        catalog_refs=frozenset({field_ref, entity_ref}),
    )


def _make_registry(**overrides: object) -> Registry:
    """Create a Registry with some standard test objects."""
    registry = Registry()
    registry.domains["sales"] = DomainIR(
        name="sales",
        owner="Mina Zhang",
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
        ai_context=DatasourceAiContextIR(),
        python_symbol="wh",
        location=DatasourceSourceLocation(file="<test>", line=0),
    )
    registry.entities["sales.orders"] = EntityIR(
        semantic_id="sales.orders",
        domain="sales",
        name="orders",
        datasource="wh",
        source=TableSourceIR(table="orders"),
        primary_key=(),
        ai_context=AiContextIR(),
        python_symbol="orders",
        location=_LOC,
    )
    registry.dimensions["sales.orders.amount"] = DimensionIR(
        semantic_id="sales.orders.amount",
        domain="sales",
        entity="sales.orders",
        name="amount",
        ai_context=AiContextIR(),
        is_time_dimension=False,
        kind=DimensionKind.CATEGORICAL,
        python_symbol="amount",
        location=_LOC,
        source_column="amount",
    )
    registry.dimensions["sales.orders.order_date"] = DimensionIR(
        semantic_id="sales.orders.order_date",
        domain="sales",
        entity="sales.orders",
        name="order_date",
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        parse=DateParse(),
        python_symbol="order_date",
        location=_LOC,
        source_column="order_date",
    )
    return registry


# ---------------------------------------------------------------------------
# HourPrefixParse prefix cross-reference validation
# ---------------------------------------------------------------------------


def test_hour_prefix_with_valid_short_name_ok() -> None:
    registry = _make_registry()
    registry.dimensions["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        parse=HourPrefixParse(prefix="order_date"),
        python_symbol="order_hour",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert not any(e.kind == ErrorKind.MISSING_DIMENSION_REF for e in errors)


def test_hour_prefix_with_invalid_prefix() -> None:
    registry = _make_registry()
    registry.dimensions["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        parse=HourPrefixParse(prefix="sales.orders.nonexistent_date"),
        python_symbol="order_hour",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(
        e.kind == ErrorKind.MISSING_DIMENSION_REF and "sales.orders.order_hour" in e.semantic_refs
        for e in errors
    )


def test_hour_prefix_must_reference_time_dimension() -> None:
    registry = _make_registry()
    registry.dimensions["sales.orders.order_hour"] = DimensionIR(
        semantic_id="sales.orders.order_hour",
        domain="sales",
        entity="sales.orders",
        name="order_hour",
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        parse=HourPrefixParse(prefix="amount"),
        python_symbol="order_hour",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    assert any(
        e.kind == ErrorKind.MISSING_DIMENSION_REF and "sales.orders.order_hour" in e.semantic_refs
        for e in errors
    )


def _cast_partition_time_field(table):
    return table.dt.cast("date")


def _raw_partition_time_field(table):
    return table.dt


def test_cast_partition_time_field_emits_pushdown_advisory_warning() -> None:
    registry = _make_registry()
    registry.dimensions["sales.orders.order_date"] = DimensionIR(
        semantic_id="sales.orders.order_date",
        domain="sales",
        entity="sales.orders",
        name="order_date",
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        parse=DateParse(),
        python_symbol="order_date",
        location=_LOC,
    )

    errors, warnings = assembly_validate(
        registry, sidecar=_time_dimension_sidecar(_cast_partition_time_field)
    )

    assert errors == []
    assert any(w.kind == WarningKind.TIME_DIMENSION_PUSHDOWN_ADVISORY for w in warnings)


def test_raw_partition_time_field_has_no_pushdown_advisory_warning() -> None:
    registry = _make_registry()
    registry.dimensions["sales.orders.order_date"] = DimensionIR(
        semantic_id="sales.orders.order_date",
        domain="sales",
        entity="sales.orders",
        name="order_date",
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        parse=StrptimeParse(format="%Y%m%d"),
        python_symbol="order_date",
        location=_LOC,
    )

    errors, warnings = assembly_validate(
        registry, sidecar=_time_dimension_sidecar(_raw_partition_time_field)
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
        keys=(JoinKey(from_key="sales.orders.amount", to_key="sales.orders.amount"),),
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
        keys=(JoinKey(from_key="sales.orders.amount", to_key="sales.orders.amount"),),
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
        keys=(JoinKey(from_key="sales.orders.nonexistent_field", to_key="sales.orders.amount"),),
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
        keys=(JoinKey(from_key="sales.orders.amount", to_key="sales.orders.amount"),),
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
        keys=(
            JoinKey(from_key="sales.orders.amount", to_key="sales.orders.amount"),
            JoinKey(from_key="sales.orders.order_date", to_key="sales.orders.amount"),
        ),
        ai_context=AiContextIR(),
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    # Reusing the target key column makes the directed mapping ambiguous.
    rel_errors = [e for e in errors if "sales.bad_arity" in e.semantic_refs]
    assert any(error.kind == ErrorKind.INVALID_RELATIONSHIP_MAPPING for error in rel_errors)


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
        (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
        marivo_root = tmp_path / "models"
        root = marivo_root / "semantic"
        root.mkdir(parents=True, exist_ok=True)
        datasource_root = marivo_root / "datasources"
        datasource_root.mkdir(parents=True, exist_ok=True)
        (datasource_root / "wh.py").write_text(
            "import marivo.datasource as md\nmd.duckdb(name='wh', path=':memory:')\n"
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
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
""")


def test_duplicate_default_time_dimension_raises() -> None:
    registry = _make_registry()
    # Add a second time field with is_default=True on the same dataset
    registry.dimensions["sales.orders.order_date2"] = DimensionIR(
        semantic_id="sales.orders.order_date2",
        domain="sales",
        entity="sales.orders",
        name="order_date2",
        ai_context=AiContextIR(),
        is_time_dimension=True,
        kind=DimensionKind.TIME,
        parse=DateParse(),
        python_symbol="order_date2",
        location=_LOC,
        is_default=True,
    )
    # Mark the existing order_date as default too
    registry.dimensions["sales.orders.order_date"] = dataclasses.replace(
        registry.dimensions["sales.orders.order_date"],
        is_default=True,
    )

    errors, _warnings = assembly_validate(registry)

    assert any(e.kind == ErrorKind.DUPLICATE_DEFAULT_TIME_DIMENSION for e in errors), (
        f"Expected DUPLICATE_DEFAULT_TIME_DIMENSION, got: {[e.kind for e in errors]}"
    )


def test_warnings_in_load_result(semantic_project_factory) -> None:
    """LoadResult should expose an empty warnings tuple when no warnings exist."""
    metrics_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms
        orders_ref = ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))
        amount_ref = ms.measure_column(
            name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all()
        )

        finalized = ms.finalize()
        amount = finalized.resolve(amount_ref)

        @ms.metric()
        def revenue():
            return amount.sum()
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


def test_invalid_relationship_via_loader(semantic_project_factory) -> None:
    """Relationship referencing a non-existent dataset should fail via loader."""
    rels_py = textwrap.dedent("""\
        import marivo.datasource as md
        import marivo.semantic as ms

        ms.relationship(
            name="bad_rel",
            from_entity=ms.ref.entity("sales.nonexistent"),
            to_entity=ms.ref.entity("sales.also_nonexistent"),
            keys=[ms.join_on(ms.ref.dimension("sales.orders.f1"), ms.ref.dimension("sales.orders.f2"))],
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


# ---------------------------------------------------------------------------
# did_you_mean in MISSING_*_REF errors
# ---------------------------------------------------------------------------


def test_missing_datasource_ref_includes_did_you_mean() -> None:
    registry = _make_registry()
    registry.entities["sales.bad_ds"] = EntityIR(
        semantic_id="sales.bad_ds",
        domain="sales",
        name="bad_ds",
        datasource="w",  # close to "wh"
        source=TableSourceIR(table="bad_ds"),
        primary_key=(),
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
    registry.dimensions["sales.ordrs.bad_field"] = DimensionIR(
        semantic_id="sales.ordrs.bad_field",
        domain="sales",
        entity="sales.ordrs",  # close to "sales.orders"
        name="bad_field",
        ai_context=AiContextIR(),
        is_time_dimension=False,
        kind=DimensionKind.CATEGORICAL,
        python_symbol="bad_field",
        location=_LOC,
    )
    errors, _warnings = assembly_validate(registry)
    dym_errors = [e for e in errors if e.kind == ErrorKind.MISSING_ENTITY_REF]
    assert len(dym_errors) >= 1
    dym = dym_errors[0].details.get("did_you_mean", [])
    assert "sales.orders" in dym


def test_missing_dimension_ref_includes_did_you_mean() -> None:
    registry = _make_registry()
    registry.relationships["rel"] = RelationshipIR(
        semantic_id="rel",
        domain="sales",
        name="rel",
        from_entity="sales.orders",
        to_entity="sales.orders",
        keys=(JoinKey(from_key="sales.orders.amoun", to_key="sales.orders.amount"),),
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


# ---------------------------------------------------------------------------
# Semi-additive metric validation (SemiAdditive struct)
# ---------------------------------------------------------------------------
# In the metric-split model, semi-additivity is expressed as:
#   additivity=SemiAdditive(over="field_id", fold=TimeFoldIR(...))
# The old string additivity="semi_additive" + status_time_dimension field no
# longer exist.  The validator checks that SemiAdditive.over references a
# declared time dimension (INVALID_STATUS_TIME_DIMENSION).


def test_physical_entity_dependency_fingerprint_preserves_consumed_identity() -> None:
    from marivo.semantic._physical_identity import entity_dependency_fingerprint

    # Captured before removing the Metric graph; physical identity is independent.
    assert entity_dependency_fingerprint(_make_registry(), "sales.orders") == (
        "286567904d7b1d6f84c8fc8aaad672d0e901d816268de1a831cead1d7690cde1"
    )
