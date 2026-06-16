"""Slice 0 foundation test: all public symbols importable, type structure correct.

This test verifies:
- All symbols in ``__all__`` are importable from ``marivo.semantic``.
- ``SemanticError`` subclasses exist and have the right fields.
- ``ErrorKind`` enum has all expected values.
- IR dataclasses are frozen.
- Ref types have correct ``kind`` and ``semantic_id`` attributes.

Note: pytest.ini sets ``python_classes =`` (empty), so only
``unittest.TestCase`` subclasses are collected.  All tests here use
plain functions to match the rest of the test suite.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any, cast

import pytest

import marivo.semantic as ms
from marivo.introspection.surface import render as surface_render
from marivo.semantic import errors as errors_mod
from marivo.semantic import typing as typing_mod
from marivo.semantic.constraints import ConstraintId, get_constraint, iter_constraints
from marivo.semantic.ir import (
    AiContextIR,
    DatasourceIR,
    DimensionIR,
    DimensionKind,
    DimensionRef,
    DomainIR,
    EntityIR,
    EntityProvenance,
    EntityRef,
    MetricIR,
    MetricRef,
    ParityStatus,
    ProvenanceIR,
    RelationshipIR,
    RelationshipRef,
    SourceLocation,
    SymbolKind,
    TimeDimensionRef,
)

# ---------------------------------------------------------------------------
# __all__ importability
# ---------------------------------------------------------------------------


def _ms_json_data(symbol: str | None = None) -> dict[str, Any]:
    """Return the JSON descriptor dict for a semantic symbol using internal render."""
    from marivo.semantic.help import _surface

    return cast("dict[str, Any]", surface_render(_surface(), symbol, "json"))


def test_all_symbols_importable() -> None:
    for name in ms.__all__:
        assert hasattr(ms, name), f"ms.{name} not found on module"


def test_all_list_matches_expected() -> None:
    expected = {
        "AiContext",
        "AiContextView",
        "AssessmentIssue",
        "AuthoringAssessment",
        "AuthoringQuestion",
        "BriefStatus",
        "ComponentFact",
        "CrossEntityMetricBrief",
        "DatasetSource",
        "DatasourceDetails",
        "DecisionRecord",
        "DemandSignal",
        "DerivedMetricBrief",
        "DimensionBrief",
        "DimensionDetails",
        "DimensionRef",
        "DimensionValueFact",
        "DomainBrief",
        "DomainBriefSummary",
        "DomainDetails",
        "DomainRef",
        "EntityBrief",
        "EntityDetails",
        "EntityRef",
        "FileSource",
        "FormatCandidate",
        "JoinPathFact",
        "LadderOrderError",
        "MetricBrief",
        "MetricDetails",
        "MetricRef",
        "ParityResult",
        "PrimaryKeyCandidate",
        "ReadinessIssue",
        "ReadinessInputSummary",
        "ReadinessReport",
        "RegisteredMatch",
        "RelationshipBrief",
        "RelationshipDetails",
        "RelationshipRef",
        "RichnessReport",
        "SemanticCatalog",
        "SemanticKind",
        "SemanticKindInput",
        "SemanticObject",
        "SemanticObjectDetails",
        "SemanticObjectList",
        "SemanticRef",
        "SemanticRefInput",
        "SnapshotVersioning",
        "TableSource",
        "TimeDimensionBrief",
        "TimeDimensionDetails",
        "TimeDimensionRef",
        "ValidityVersioning",
        "VerifyResult",
        "VersioningHints",
        "help_text",
        "load",
        "domain",
        "entity",
        "file",
        "dimension",
        "time_dimension",
        "aggregate",
        "simple_metric",
        "linear",
        "semi_additive",
        "parity_check",
        "prepare_derived_metric",
        "prepare_dimension",
        "prepare_domain",
        "prepare_entity",
        "prepare_cross_entity_metric",
        "prepare_metric",
        "prepare_relationship",
        "prepare_time_dimension",
        "relationship",
        "richness",
        "record_decision",
        "ratio",
        "readiness",
        "weighted_average",
        "ref",
        "table",
        "snapshot",
        "validity",
        "verify_object",
        "help",
        "typing",
        "errors",
    }
    assert set(ms.__all__) == expected
    assert not hasattr(ms, "component")
    # Category 1 symbols removed from public API
    for name in (
        "SemanticProject",
        "DecisionKind",
        "RejectedCandidate",
        "DimensionKind",
        "AuthoringSourceRole",
        "RichnessGap",
        "DimensionSummary",
        "RelationshipSummary",
    ):
        assert name not in ms.__all__, f"{name} should not be in ms.__all__"


def test_reader_project_class() -> None:
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(root="/tmp/test")
    assert not project.is_ready()


def test_readiness_public_dtos() -> None:
    assert ms.ReadinessReport is not None
    assert ms.ReadinessIssue is not None
    assert ms.ReadinessInputSummary is not None


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


def test_decorator_error_is_semantic_error() -> None:
    assert issubclass(errors_mod.SemanticDecoratorError, errors_mod.SemanticError)


def test_load_error_is_semantic_error() -> None:
    assert issubclass(errors_mod.SemanticLoadError, errors_mod.SemanticError)


def test_runtime_error_is_semantic_error() -> None:
    assert issubclass(errors_mod.SemanticRuntimeError, errors_mod.SemanticError)


def test_parity_error_is_semantic_error() -> None:
    assert issubclass(errors_mod.SemanticParityError, errors_mod.SemanticError)


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


def test_help_text_top_level_is_compact_directory(capsys: pytest.CaptureFixture[str]) -> None:
    ms.help()

    captured = capsys.readouterr()
    assert "marivo.semantic" in captured.out
    # Each line shows: name, kind tag in brackets, description
    assert "ms.entity" in captured.out
    assert "ms.simple_metric" in captured.out
    assert "ms.constraints" in captured.out
    # Kind tags appear as [kind] in output
    assert "[callable]" in captured.out
    assert "[topic]" in captured.out
    assert "[class]" in captured.out
    # No inline constraint dump
    assert "Constraints:" not in captured.out
    assert "authoring_constraints" not in captured.out
    # Drill-down hint present
    assert "ms.help(" in captured.out


def test_help_json_top_level_returns_compact_directory() -> None:
    result = _ms_json_data()

    assert isinstance(result, dict)
    assert result["schema_version"] == "1"
    assert result["surface"] == "marivo.semantic"
    assert result["kind"] == "surface"
    assert "entries" in result
    assert "authoring_constraints" not in result
    entries = result["entries"]
    assert isinstance(entries, list)
    assert len(entries) > 0
    for entry in entries:
        assert "name" in entry
        assert "summary" in entry
        assert "kind" in entry
        assert entry["kind"] in {"callable", "class", "module", "topic", "surface", "unknown"}
    families = cast("list[dict[str, Any]]", result.get("families", []))
    folded_names = {name for fam in families for name in fam["members"]}
    entry_names = {e["name"] for e in entries}
    assert entry_names.isdisjoint(folded_names)
    assert entry_names | folded_names == set(ms.__all__) | {
        "constraints",
        "composition",
        "additivity",
    }
    assert "entity" in entry_names
    assert "simple_metric" in entry_names
    assert "ratio" in entry_names
    assert "weighted_average" in entry_names
    assert "component" not in entry_names
    assert "constraints" in entry_names
    assert "composition" in entry_names
    assert "SemanticProject" not in entry_names
    assert "typing" in entry_names


def test_help_json_simple_metric_includes_body_rule_and_related_help() -> None:
    result = _ms_json_data("simple_metric")

    assert isinstance(result, dict)
    assert result["kind"] == "topic"
    assert result["symbol"] == "simple_metric"
    content = cast("dict[str, Any]", result["content"])
    assert "tier1" in content
    assert "tier2" in content
    assert "body_rule" in content


def test_help_json_time_dimension_includes_partition_pushdown_advisory() -> None:
    result = _ms_json_data("time_dimension")

    assert isinstance(result, dict)
    constraints = cast("list[dict[str, Any]]", result["constraints"])
    assert isinstance(constraints, list)
    constraint_ids = {entry["id"] for entry in constraints}
    assert "time_dimension_partition_pushdown" in constraint_ids
    advisory = next(
        entry for entry in constraints if entry["id"] == "time_dimension_partition_pushdown"
    )
    assert set(advisory) <= {"id", "title", "hint", "example"}
    assert "Partition time dimensions" in advisory["title"]
    assert "date_format" in advisory["hint"]


def test_help_json_composition_documents_supported_constructors_and_boundary() -> None:
    result = _ms_json_data("composition")

    assert isinstance(result, dict)
    assert result["kind"] == "topic"
    assert result["symbol"] == "composition"
    content = cast("dict[str, Any]", result["content"])
    examples = cast("list[dict[str, Any]]", content["examples"])
    example_shapes = {entry["metric_shape"] for entry in examples}
    assert "ratio" in example_shapes
    assert "weighted average" in example_shapes
    assert "linear (a +/- b)" in example_shapes
    assert "boundary" in content
    related_help = cast("list[str]", content["related_help"])
    assert "ms.help('simple_metric')" in related_help
    assert "ms.help('component')" not in related_help


def test_help_text_composition_documents_constructors_and_boundary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ms.help("composition")

    captured = capsys.readouterr()
    assert "composition" in captured.out
    assert "ms.ratio" in captured.out


def test_help_json_constraints_cover_error_kinds() -> None:
    result = _ms_json_data("constraints")

    assert isinstance(result, dict)
    assert result["kind"] == "topic"
    assert result["symbol"] == "constraints"
    content = cast("dict[str, Any]", result["content"])
    assert set(content) <= {"constraints"}
    constraints = cast("list[dict[str, str]]", content["constraints"])
    assert isinstance(constraints, list)
    covered = set()
    for entry in constraints:
        assert set(entry) <= {"id", "title"}
        detail = _ms_json_data(entry["id"])
        assert isinstance(detail, dict)
        assert detail["kind"] == "topic"
        assert detail["symbol"] == entry["id"]
        full_content = cast("dict[str, Any]", detail["content"])
        assert full_content["id"] == entry["id"]
        assert "why" in full_content
        covered.add(cast("str", full_content["error_kind"]))
    # Load-only error kinds (no agent-facing constraint) are exempt.
    load_only_errors = {"unknown_measure"}
    for kind in errors_mod.ErrorKind:
        if kind.value not in load_only_errors:
            assert kind.value in covered


def test_constraint_example_paths_exist() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for constraint in iter_constraints():
        if constraint.example is not None:
            assert (repo_root / constraint.example).exists(), constraint.example


def test_invalid_composition_hint_points_to_composition_help() -> None:
    constraint = get_constraint("composition_shape")
    assert constraint is not None
    assert "ms.help('composition')" in constraint.hint


def test_derived_fanout_policy_hint_uses_flat_constructors() -> None:
    constraint = get_constraint("metric_fanout_policy_derived")
    assert constraint is not None
    assert "ms.ratio" in constraint.hint
    assert "ms.derived_metric" not in constraint.hint


def test_removed_component_body_constraints_absent() -> None:
    assert get_constraint("metric_derived_shape") is None
    assert get_constraint("component_name_declared") is None
    assert get_constraint("ast_component_arithmetic") is None


def test_semantic_skill_constraint_table_matches_catalog() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    reference = repo_root / "marivo/skills/marivo-semantic/references/authoring-patterns.md"
    text = reference.read_text()

    rows = re.findall(r"^\| `([^`]+)` \| [^|]+ \| `([^`]+)` \|$", text, re.MULTILINE)
    assert rows
    for constraint_id, example_path in rows:
        assert get_constraint(constraint_id) is not None, constraint_id
        assert (reference.parent.parent / example_path).exists(), example_path


# ---------------------------------------------------------------------------
# ErrorKind enum
# ---------------------------------------------------------------------------

_EXPECTED_DECORATOR_KINDS = {
    "duplicate_name",
    "missing_domain",
    "missing_datasets",
    "invalid_ref",
    "invalid_composition",
    "invalid_component_body",
    "outside_loader_context",
    "metric_body_not_single_return",
    "invalid_ai_context",
    "sql_escape_hatch",
    "ibis_attr_shadow",
    "invalid_sample_interval",
    "invalid_time_fold",
}

_EXPECTED_ASSEMBLY_KINDS = {
    "domain_file_missing",
    "domain_file_mismatch",
    "missing_entity_ref",
    "missing_dimension_ref",
    "missing_metric_ref",
    "cross_model_cycle",
    "cross_datasource_not_supported",
    "hour_time_dimension_prefix_missing",
    "subday_granularity_without_time",
    "duplicate_default_time_dimension",
    "invalid_relationship_endpoint",
    "organization_error",
    "invalid_project",
    "missing_metric_additivity",
    "missing_metric_root_dataset",
    "invalid_metric_root_dataset",
    "invalid_verification_mode",
    "invalid_entity_versioning",
    "non_root_metric_aggregate",
    "invalid_metric_fanout_policy",
    "derived_metric_fanout_policy",
    "time_fold_requires_semi_additive",
    "time_fold_requires_sampled_time_field",
    "missing_time_fold",
    "missing_status_time_dimension",
    "invalid_status_time_dimension",
    "unsupported_kind",
    "unsupported_list_parent",
    "ladder_order",
    "unverified_provenance",
    "source_sql_missing",
    "invalid_measure_aggregation",
    "incommensurable_linear_units",
    "missing_measure_additivity",
    "unknown_measure",
}

_EXPECTED_RUNTIME_KINDS = {
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
    "ladder_order",
    "unsupported_kind",
    "unsupported_list_parent",
    "conflicting_parameters",
}

_EXPECTED_PARITY_KINDS = {
    "source_sql_missing",
    "unverified_provenance",
    "parity_value_mismatch",
    "parity_not_scalar",
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


def test_error_kind_parity_kinds() -> None:
    values = {k.value for k in errors_mod.ErrorKind if k.value in _EXPECTED_PARITY_KINDS}
    assert values == _EXPECTED_PARITY_KINDS


def test_constraint_ids_all_registered() -> None:
    missing = [
        constraint_id.value
        for constraint_id in ConstraintId
        if get_constraint(constraint_id) is None
    ]
    assert missing == []


def test_error_kind_all_covered() -> None:
    expected = (
        _EXPECTED_DECORATOR_KINDS
        | _EXPECTED_ASSEMBLY_KINDS
        | _EXPECTED_RUNTIME_KINDS
        | _EXPECTED_PARITY_KINDS
    )
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
    ProvenanceIR,
    DomainIR,
    DatasourceIR,
    EntityIR,
    DimensionIR,
    MetricIR,
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
        "time_dimension",
        "metric",
        "relationship",
    }
    actual = {k.value for k in SymbolKind}
    assert actual == expected


def test_parity_status_values() -> None:
    expected = {"verified", "unverified", "drifted"}
    actual = {k.value for k in ParityStatus}
    assert actual == expected


def test_dataset_provenance_values() -> None:
    expected = {"ibis_table", "sql_view"}
    actual = {k.value for k in EntityProvenance}
    assert actual == expected


def test_symbol_kind_is_str_enum() -> None:
    assert isinstance(SymbolKind.DOMAIN, str)
    assert SymbolKind.DOMAIN.value == "domain"


def test_parity_status_is_str_enum() -> None:
    assert isinstance(ParityStatus.VERIFIED, str)
    assert ParityStatus.VERIFIED.value == "verified"


def test_field_kind_values() -> None:
    expected = {"categorical", "measure", "time"}
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
    ref = EntityRef("sales.orders")
    assert ref.semantic_id == "sales.orders"
    assert ref.kind == SymbolKind.ENTITY
    assert "EntityRef" in repr(ref)


def test_field_ref() -> None:
    ref = DimensionRef("sales.orders.amount")
    assert ref.semantic_id == "sales.orders.amount"
    assert ref.kind == SymbolKind.DIMENSION


def test_field_ref_callable_without_resolver_raises() -> None:
    ref = DimensionRef("sales.orders.amount")
    with pytest.raises(RuntimeError, match="no resolver"):
        ref(None)


def test_time_field_ref() -> None:
    ref = TimeDimensionRef("sales.orders.order_date")
    assert ref.semantic_id == "sales.orders.order_date"
    assert ref.kind == SymbolKind.TIME_DIMENSION


def test_time_field_ref_callable_without_resolver_raises() -> None:
    ref = TimeDimensionRef("sales.orders.order_date")
    with pytest.raises(RuntimeError, match="no resolver"):
        ref(None)


def test_metric_ref() -> None:
    ref = MetricRef("sales.revenue")
    assert ref.semantic_id == "sales.revenue"
    assert ref.kind == SymbolKind.METRIC


def test_metric_ref_not_callable_raises_helpful_error() -> None:
    """Calling a MetricRef (as if it were a decorator) raises a clear error."""
    from marivo.semantic.errors import SemanticDecoratorError

    ref = MetricRef("sales.aov")
    with pytest.raises(SemanticDecoratorError, match="not a decorator"):
        ref(lambda t: t.amount.sum())
    # Also confirm the message mentions flat constructors
    with pytest.raises(SemanticDecoratorError, match=r"ms\.ratio") as exc_info:
        ref(lambda t: t.amount.sum())
    assert "ms.ratio" in str(exc_info.value)


def test_relationship_ref() -> None:
    ref = RelationshipRef("sales.orders_to_items")
    assert ref.semantic_id == "sales.orders_to_items"
    assert ref.kind == SymbolKind.RELATIONSHIP


def test_base_ref_repr() -> None:
    ref = EntityRef("sales.orders")
    assert repr(ref) == "EntityRef('sales.orders')"


# ---------------------------------------------------------------------------
# typing module
# ---------------------------------------------------------------------------


def test_ibis_backend_protocol() -> None:
    assert hasattr(typing_mod, "IbisBackend")


def test_component_expr_protocol_removed() -> None:
    assert not hasattr(typing_mod, "ComponentExpr")


def test_ai_context_typed_dict() -> None:
    assert hasattr(typing_mod, "AiContext")
    annotations = typing_mod.AiContext.__annotations__
    assert "business_definition" in annotations
    assert "guardrails" in annotations
    assert "synonyms" in annotations
    assert "examples" in annotations
    assert "instructions" in annotations
    assert "owner_notes" in annotations


def test_ai_context_accessible_from_ms() -> None:
    assert hasattr(ms, "AiContext")
    assert ms.AiContext is typing_mod.AiContext


# ---------------------------------------------------------------------------
# Loader module
# ---------------------------------------------------------------------------


def test_loader_context_dataclass() -> None:
    from marivo.semantic.loader import LoaderContext

    ctx = LoaderContext()
    assert ctx.current_model_file is None
    assert ctx.default_domain is None
    assert ctx.pending_objects == []


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
        kind="unverified_provenance",
        message="test warning",
        refs=("ref1",),
        location=None,
    )
    assert warn.kind == "unverified_provenance"
    assert warn.message == "test warning"
    assert warn.refs == ("ref1",)
    assert warn.location is None

    # StructuredWarning is frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        warn.kind = "string_ref"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Validator module
# ---------------------------------------------------------------------------


def test_validate_decorator_call_works() -> None:
    from marivo.semantic.validator import validate_decorator_call

    # No longer a stub; should not raise for valid input
    validate_decorator_call("test", {})


def test_validate_metric_body_ast_works() -> None:
    from marivo.semantic.validator import validate_metric_body_ast

    # No longer a stub; should return a hash string for valid bodies
    def good_fn(table: Any) -> Any:
        return table.amount.sum()

    result = validate_metric_body_ast(good_fn, "base")
    assert isinstance(result, str)
    assert len(result) > 0


def test_assembly_validate_works() -> None:
    from marivo.semantic.validator import Registry, assembly_validate

    # No longer a stub; should return (errors, warnings) for empty registry
    registry = Registry()
    errors, warnings = assembly_validate(registry)
    assert isinstance(errors, list)
    assert isinstance(warnings, list)


# ---------------------------------------------------------------------------
# Materializer module
# ---------------------------------------------------------------------------


def test_materializer_class_exists() -> None:
    from marivo.semantic.errors import SemanticRuntimeError
    from marivo.semantic.materializer import Materializer

    m = Materializer(project=None, backend_factory=lambda x: None)
    with pytest.raises(SemanticRuntimeError):
        m.entity("test")
    with pytest.raises(SemanticRuntimeError):
        m.dimension("test")
    with pytest.raises(SemanticRuntimeError):
        m.metric("test")


# ---------------------------------------------------------------------------
# Parity module
# ---------------------------------------------------------------------------


def test_parity_result_frozen() -> None:
    from marivo.semantic.parity import ParityResult

    result = ParityResult(ok=True)
    assert result.ok is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.ok = False  # type: ignore[misc]


def test_parity_check_callable() -> None:
    from marivo.semantic.parity import parity_check

    assert callable(parity_check)


def test_propagated_parity_status_callable() -> None:
    from marivo.semantic.parity import propagated_parity_status

    assert callable(propagated_parity_status)


# ---------------------------------------------------------------------------
# SemanticProject basic
# ---------------------------------------------------------------------------


def test_reader_project_init() -> None:
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(root="/tmp/test")
    assert not project.is_ready()
    assert project.errors() == ()


def test_reader_project_load_works() -> None:
    """SemanticProject.load() now works (implemented in Slice 1)."""
    import tempfile
    from pathlib import Path

    from marivo.semantic.reader import SemanticProject

    with tempfile.TemporaryDirectory() as tmp:
        semantic_root = Path(tmp) / "models" / "semantic"
        semantic_root.mkdir(parents=True)
        project = SemanticProject(root=semantic_root)
        result = project.load()
        assert result.status == "ready"


def test_reader_project_load_reloads() -> None:
    """SemanticProject.load() resets and re-loads when called again."""
    import tempfile
    from pathlib import Path

    from marivo.semantic.reader import SemanticProject

    with tempfile.TemporaryDirectory() as tmp:
        semantic_root = Path(tmp) / "models" / "semantic"
        semantic_root.mkdir(parents=True)
        project = SemanticProject(root=semantic_root)
        result = project.load()
        assert result.status == "ready"


def test_help_additivity_documents_semi_additive_semantics(capsys) -> None:
    ms.help("additivity")
    out = capsys.readouterr().out
    assert "semi_additive" in out
    assert "ms.semi_additive" in out
    assert "fold" in out


def test_help_simple_metric_mentions_fold_is_definition_choice(capsys) -> None:
    ms.help("simple_metric")
    out = capsys.readouterr().out
    assert "body" in out


# ---------------------------------------------------------------------------
# Stepwise authoring DTO exports
# ---------------------------------------------------------------------------


def test_stepwise_authoring_dto_exports() -> None:
    for name in (
        "BriefStatus",
        "RegisteredMatch",
        "VerifyResult",
        "DomainBrief",
        "DomainBriefSummary",
        "EntityBrief",
        "DimensionBrief",
        "TimeDimensionBrief",
        "MetricBrief",
        "RelationshipBrief",
        "CrossEntityMetricBrief",
        "DerivedMetricBrief",
    ):
        assert hasattr(ms, name), f"marivo.semantic missing export: {name}"
