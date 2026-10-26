"""Slice 0 foundation test: all public symbols importable, type structure correct.

This test verifies:
- All symbols in ``__all__`` are importable from ``marivo.semantic``.
- ``SemanticError`` subclasses exist and have the right fields.
- ``ErrorKind`` enum has all expected values.
- IR dataclasses are frozen.
- Unified refs have exact ``kind`` and ``path`` attributes.

Note: pytest.ini sets ``python_classes =`` (empty), so only
``unittest.TestCase`` subclasses are collected.  All tests here use
plain functions to match the rest of the test suite.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys

import pytest
from ibis.common.deferred import Deferred

import marivo.semantic as ms
from marivo.refs import RefPayloadV1
from marivo.semantic import errors as errors_mod
from marivo.semantic import typing as typing_mod
from marivo.semantic.check import _error_to_dict
from marivo.semantic.constraints import ConstraintId, get_constraint
from marivo.semantic.ir import (
    AiContextIR,
    DatasourceIR,
    DimensionIR,
    DimensionKind,
    DomainIR,
    EntityIR,
    EntityProvenance,
    RelationshipIR,
    SemanticKind,
    SourceLocation,
    TargetDimensionContract,
)
from tests.support.refs import as_ref

# ---------------------------------------------------------------------------
# __all__ importability
# ---------------------------------------------------------------------------


def test_all_symbols_importable() -> None:
    for name in ms.__all__:
        assert hasattr(ms, name), f"ms.{name} not found on module"


def test_readiness_public_dtos() -> None:
    assert ms.ReadinessReport is not None
    assert not hasattr(ms, "ReadinessIssue")
    assert not hasattr(ms, "ReadinessInputSummary")


def test_semantic_import_does_not_load_analysis() -> None:
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import marivo.semantic; "
            "raise SystemExit(1 if 'marivo.analysis' in sys.modules else 0)",
        ],
        check=False,
    )

    assert probe.returncode == 0


def test_typing_submodule() -> None:
    assert ms.typing is typing_mod


def test_errors_submodule() -> None:
    assert ms.errors is errors_mod


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


def test_semantic_error_base() -> None:
    err = errors_mod.SemanticError(
        kind="test_kind",
        message="test message",
    )
    assert err.kind == "test_kind"
    assert err.message == "test message"
    assert err.semantic_refs == ()
    assert err.location is None
    assert err.hint is None
    assert err.details == {}
    assert isinstance(err, Exception)


def test_semantic_error_str_template() -> None:
    loc = SourceLocation(file="/tmp/test.py", line=42)
    err = errors_mod.SemanticError(
        kind="test_kind",
        message="something broke",
        refs=("ref1", "ref2"),
        location=loc,
        hint="try this",
    )
    s = str(err)
    assert "[test_kind] something broke" in s
    assert "refs: ref1, ref2" in s
    assert "at: /tmp/test.py:42" in s
    assert "hint: try this" in s


def test_semantic_error_normalizes_target_dimension_refs() -> None:
    dimension = TargetDimensionContract(
        ref=RefPayloadV1(
            schema="marivo.semantic_ref/v1",
            kind=SemanticKind.DIMENSION,
            path="sales.orders.region",
        ),
        entity_ref=RefPayloadV1(
            schema="marivo.semantic_ref/v1",
            kind=SemanticKind.ENTITY,
            path="sales.orders",
        ),
        source_column="region",
        logical_type="string",
        nullable=False,
        is_time_dimension=False,
        sample_interval=None,
        is_default=False,
        timezone=None,
    )
    err = errors_mod.SemanticError(
        kind="test_kind",
        message="invalid dimension",
        refs=("sales.orders", dimension, "sales.revenue"),
    )

    expected_refs = ("sales.orders", "sales.orders.region", "sales.revenue")
    assert err.semantic_refs == expected_refs
    assert "refs: sales.orders, sales.orders.region, sales.revenue" in str(err)
    assert "TargetDimensionContract" not in str(err)
    assert json.loads(json.dumps(_error_to_dict(err)))["refs"] == list(expected_refs)


def test_decorator_error_is_semantic_error() -> None:
    assert issubclass(errors_mod.SemanticDecoratorError, errors_mod.SemanticError)


def test_load_error_is_semantic_error() -> None:
    assert issubclass(errors_mod.SemanticLoadError, errors_mod.SemanticError)


def test_runtime_error_is_semantic_error() -> None:
    assert issubclass(errors_mod.SemanticRuntimeError, errors_mod.SemanticError)


def test_load_failed_not_semantic_error() -> None:
    assert not issubclass(errors_mod.SemanticLoadFailed, errors_mod.SemanticError)
    assert issubclass(errors_mod.SemanticLoadFailed, Exception)


def test_load_failed_wraps_errors() -> None:
    err1 = errors_mod.SemanticError(kind="a", message="first")
    err2 = errors_mod.SemanticError(kind="b", message="second")
    failed = errors_mod.SemanticLoadFailed([err1, err2])
    assert len(failed.errors) == 2
    assert failed.errors[0] is err1


def test_raise_helper() -> None:
    with pytest.raises(errors_mod.SemanticDecoratorError) as exc_info:
        errors_mod._raise(
            errors_mod.ErrorKind.DUPLICATE_NAME,
            "name already taken",
            refs=["model.sales"],
        )
    err = exc_info.value
    assert err.kind == "duplicate_name"
    assert err.message == "name already taken"
    assert err.semantic_refs == ("model.sales",)
    assert err.hint is not None  # auto-populated from HINTS
    assert err.constraint_id == "unique_semantic_name"


# ---------------------------------------------------------------------------
# ErrorKind enum
# ---------------------------------------------------------------------------

_EXPECTED_DECORATOR_KINDS = {
    "duplicate_name",
    "missing_domain",
    "missing_entities",
    "missing_metric_root_entity",
    "invalid_ref",
    "invalid_filter",
    "invalid_composition",
    "invalid_component_body",
    "outside_loader_context",
    "metric_body_not_single_return",
    "invalid_ai_context",
    "invalid_domain_owner",
    "sql_escape_hatch",
    "ibis_attr_shadow",
    "invalid_sample_interval",
    "invalid_time_fold",
    "entity_constructor_as_decorator",
    "invalid_expression_stage",
    "invalid_expression_value",
    "cross_root_row_expression",
    "missing_sum_permission",
    "invalid_where_scope",
    "invalid_time_path",
    "invalid_target_semantics",
}

_EXPECTED_ASSEMBLY_KINDS = {
    "domain_file_missing",
    "domain_file_mismatch",
    "missing_entity_ref",
    "missing_dimension_ref",
    "missing_metric_ref",
    "cross_model_cycle",
    "definition_cycle",
    "cross_datasource_not_supported",
    "duplicate_default_time_dimension",
    "invalid_relationship_endpoint",
    "invalid_relationship_mapping",
    "organization_error",
    "invalid_project",
    "missing_metric_additivity",
    "missing_metric_root_entity",
    "invalid_metric_root_entity",
    "invalid_entity_versioning",
    "duplicate_identity_key",
    "missing_identity_key_column",
    "identity_version_overlap",
    "non_root_metric_aggregate",
    "invalid_metric_fanout_policy",
    "invalid_filter",
    "derived_metric_fanout_policy",
    "time_fold_requires_semi_additive",
    "time_fold_requires_sampled_time_field",
    "missing_time_fold",
    "missing_status_time_dimension",
    "invalid_status_time_dimension",
    "invalid_measure_aggregation",
    "incommensurable_linear_units",
    "missing_measure_additivity",
    "unknown_measure",
}

_EXPECTED_RUNTIME_KINDS = {
    "catalog_search_failed",
    "catalog_search_stale_cursor",
    "definition_read_failed",
    "not_found",
    "entity_not_found",
    "dimension_not_found",
    "metric_not_found",
    "materialize_failed",
    "backend_mismatch",
    "compile_error",
    "ambiguous_reference",
    "cross_datasource_not_supported",
    "backend_factory_required",
    "inspect_source_required",
    "project_not_loaded",
    "binding_context_missing",
    "invalid_binding_ref",
    "binding_alias_not_direct",
    "binding_alias_ambiguous",
    "binding_entity_mismatch",
    "binding_not_declared",
    "binding_target_missing",
    "binding_cycle",
    "binding_result_invalid",
    "invalid_filter",
    "filter_value_runtime_incompatible",
}


def test_error_kind_decorator_kinds() -> None:
    values = {k.value for k in errors_mod.ErrorKind if k.value in _EXPECTED_DECORATOR_KINDS}
    assert values == _EXPECTED_DECORATOR_KINDS


def test_error_kind_assembly_kinds() -> None:
    values = {k.value for k in errors_mod.ErrorKind if k.value in _EXPECTED_ASSEMBLY_KINDS}
    assert values == _EXPECTED_ASSEMBLY_KINDS


def test_error_kind_runtime_kinds() -> None:
    values = {k.value for k in errors_mod.ErrorKind if k.value in _EXPECTED_RUNTIME_KINDS}
    assert values == _EXPECTED_RUNTIME_KINDS


def test_constraint_ids_all_registered() -> None:
    missing = [
        constraint_id.value
        for constraint_id in ConstraintId
        if get_constraint(constraint_id) is None
    ]
    assert missing == []


def test_error_kind_all_covered() -> None:
    expected = _EXPECTED_DECORATOR_KINDS | _EXPECTED_ASSEMBLY_KINDS | _EXPECTED_RUNTIME_KINDS
    actual = {k.value for k in errors_mod.ErrorKind}
    assert actual == expected


def test_hints_cover_all_kinds() -> None:
    """Every ErrorKind must have a corresponding hint factory."""
    for kind in errors_mod.ErrorKind:
        assert kind in errors_mod.HINTS, f"Missing hint for {kind.value}"


# ---------------------------------------------------------------------------
# IR dataclasses are frozen
# ---------------------------------------------------------------------------

_FROZEN_CLASSES = [
    SourceLocation,
    AiContextIR,
    DomainIR,
    DatasourceIR,
    EntityIR,
    DimensionIR,
    RelationshipIR,
]


@pytest.mark.parametrize("cls", _FROZEN_CLASSES)
def test_ir_frozen(cls: type) -> None:
    assert dataclasses.is_dataclass(cls)
    assert getattr(cls, "__dataclass_params__", None) is not None
    assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# IR enum types
# ---------------------------------------------------------------------------


def test_symbol_kind_values() -> None:
    expected = {
        "domain",
        "datasource",
        "entity",
        "dimension",
        "measure",
        "time_dimension",
        "metric",
        "relationship",
        "period_calendar",
        "temporal_set",
    }
    actual = {k.value for k in SemanticKind}
    assert actual == expected


def test_dataset_provenance_values() -> None:
    expected = {"ibis_table", "sql_view", "table_projection"}
    actual = {k.value for k in EntityProvenance}
    assert actual == expected


def test_symbol_kind_is_str_enum() -> None:
    assert isinstance(SemanticKind.DOMAIN, str)
    assert SemanticKind.DOMAIN.value == "domain"


def test_field_kind_values() -> None:
    expected = {"categorical", "time"}
    actual = {k.value for k in DimensionKind}
    assert actual == expected


def test_field_kind_is_str_enum() -> None:
    assert isinstance(DimensionKind.CATEGORICAL, str)
    assert DimensionKind.CATEGORICAL.value == "categorical"
    assert DimensionKind.CATEGORICAL == "categorical"


# ---------------------------------------------------------------------------
# Ref types
# ---------------------------------------------------------------------------


def test_dataset_ref() -> None:
    ref = ms.ref.entity("sales.orders")
    assert as_ref(ref).path == "sales.orders"
    assert as_ref(ref).kind == SemanticKind.ENTITY
    assert isinstance(ref, Deferred)


def test_field_ref() -> None:
    ref = ms.ref.dimension("sales.orders.amount")
    assert as_ref(ref).path == "sales.orders.amount"
    assert as_ref(ref).kind == SemanticKind.DIMENSION


def test_field_ref_bind_without_context_raises() -> None:
    ref = ms.ref.dimension("sales.orders.amount")
    with pytest.raises(errors_mod.SemanticRuntimeError) as exc_info:
        ms.bind(ref, None)  # type: ignore[arg-type]
    assert exc_info.value.kind == "binding_context_missing"


def test_time_field_ref() -> None:
    ref = ms.ref.time_dimension("sales.orders.order_date")
    assert as_ref(ref).path == "sales.orders.order_date"
    assert as_ref(ref).kind == SemanticKind.TIME_DIMENSION


def test_time_field_ref_bind_without_context_raises() -> None:
    ref = ms.ref.time_dimension("sales.orders.order_date")
    with pytest.raises(errors_mod.SemanticRuntimeError) as exc_info:
        ms.bind(ref, None)  # type: ignore[arg-type]
    assert exc_info.value.kind == "binding_context_missing"


def test_metric_ref() -> None:
    ref = ms.ref.metric("sales.revenue")
    assert as_ref(ref).path == "sales.revenue"
    assert as_ref(ref).kind == SemanticKind.METRIC


def test_metric_ref_rejects_field_binding_call() -> None:
    ref = ms.ref.metric("sales.aov")
    with pytest.raises(errors_mod.SemanticRuntimeError) as exc_info:
        ms.bind(ref, lambda t: t.amount.sum())  # type: ignore[arg-type]
    assert exc_info.value.kind == "invalid_binding_ref"
    assert "ms.bind(field_ref, entity_alias)" in str(exc_info.value)


def test_relationship_ref() -> None:
    ref = ms.ref.relationship("sales.orders_to_items")
    assert as_ref(ref).path == "sales.orders_to_items"
    assert as_ref(ref).kind == SemanticKind.RELATIONSHIP


# ---------------------------------------------------------------------------
# typing module
# ---------------------------------------------------------------------------


def test_ibis_backend_protocol() -> None:
    assert hasattr(typing_mod, "IbisBackend")


def test_ai_context_value_fields() -> None:
    assert hasattr(typing_mod, "AiContextValue")
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(typing_mod.AiContextValue)}
    assert field_names == {"business_definition", "guardrails", "aliases"}


def test_ai_context_value_uses_existing_typing_namespace() -> None:
    assert not hasattr(ms, "AiContextValue")
    assert isinstance(ms.ai_context(), ms.typing.AiContextValue)


# ---------------------------------------------------------------------------
# Loader module
# ---------------------------------------------------------------------------


def test_loader_context_dataclass() -> None:
    from marivo.semantic.loader import LoaderContext

    ctx = LoaderContext()
    assert ctx.current_model_file is None
    assert ctx.default_domain is None
    assert ctx.pending_definitions == []


def test_load_result_dataclass() -> None:
    from marivo.semantic.loader import LoadResult

    result = LoadResult(status="ready")
    assert result.status == "ready"
    assert result.errors == ()

    # LoadResult is frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "errored"  # type: ignore[misc]


def test_structured_warning_is_frozen() -> None:
    from marivo.semantic.errors import StructuredWarning

    warn = StructuredWarning(
        kind="string_ref",
        message="test warning",
        refs=("ref1",),
        location=None,
    )
    assert warn.kind == "string_ref"
    assert warn.message == "test warning"
    assert warn.refs == ("ref1",)
    assert warn.location is None

    # StructuredWarning is frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        warn.kind = "string_ref"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Removed SQL provenance entry
# ---------------------------------------------------------------------------


def test_sql_provenance_has_no_public_entry() -> None:
    import marivo.semantic as ms
    from marivo.semantic.reader import SemanticProject

    assert not hasattr(ms, "from_sql")
    assert not hasattr(ms, "SqlProvenance")
    assert not hasattr(ms, "parity_check")
    assert not hasattr(SemanticProject, "parity_check")


# ---------------------------------------------------------------------------
# SemanticProject basic
# ---------------------------------------------------------------------------


def test_reader_project_init() -> None:
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(root="/tmp/test")
    assert not project.is_ready()
    assert project.errors() == ()


def test_removed_temporal_models_have_no_direct_entry_or_ref_factory() -> None:
    import marivo._temporal as temporal
    import marivo.refs as refs
    import marivo.semantic._authoring_temporal as authoring
    import marivo.semantic.catalog as catalog

    for owner, names in (
        (
            ms,
            (
                "datetime",
                "period_correspondence",
                "work_schedule",
                "cumulative",
                "grain_to_date",
                "trailing",
            ),
        ),
        (authoring, ("PeriodCorrespondence", "period_correspondence", "work_schedule")),
        (refs, ("WorkScheduleKind",)),
        (ms.ref, ("work_schedule",)),
        (catalog, ("WorkScheduleEntry", "WorkScheduleDetails")),
        (
            temporal,
            (
                "WorkScheduleBindingV1",
                "WorkScheduleSnapshotV1",
                "WorkScheduleSnapshotStore",
                "CorrespondenceRecord",
            ),
        ),
    ):
        for name in names:
            assert not hasattr(owner, name), (owner, name)
    with pytest.raises(ValueError, match="work_schedule"):
        refs._decode_ref_payload(
            {"schema": "marivo.semantic_ref/v1", "kind": "work_schedule", "path": "sales.schedule"}
        )
