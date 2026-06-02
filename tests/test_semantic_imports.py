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

import pytest

import marivo.semantic as ms
from marivo.semantic import errors as errors_mod
from marivo.semantic import typing as typing_mod
from marivo.semantic.constraints import get_constraint, iter_constraints
from marivo.semantic.ir import (
    AiContextIR,
    DatasetIR,
    DatasetProvenance,
    DatasetRef,
    DatasourceIR,
    DecompositionIR,
    FieldIR,
    FieldRef,
    MetricIR,
    MetricRef,
    ModelIR,
    ParityStatus,
    ProvenanceIR,
    RelationshipIR,
    RelationshipRef,
    SourceLocation,
    SymbolKind,
    TimeFieldRef,
    VerificationMode,
)

# ---------------------------------------------------------------------------
# __all__ importability
# ---------------------------------------------------------------------------


def test_all_symbols_importable() -> None:
    for name in ms.__all__:
        assert hasattr(ms, name), f"ms.{name} not found on module"


def test_all_list_matches_expected() -> None:
    expected = {
        "AiContext",
        "SemanticProject",
        "find_project",
        "model",
        "dataset",
        "file",
        "field",
        "time_field",
        "metric",
        "relationship",
        "sum",
        "table",
        "ratio",
        "weighted_average",
        "ref",
        "derived_metric",
        "snapshot",
        "validity",
        "help",
        "typing",
        "errors",
        "EvidenceSummary",
        "ParitySummary",
        "PreviewSummary",
        "ReadinessIssue",
        "ReadinessReport",
        "Candidate",
        "ConfirmationRecord",
        "DecisionInput",
        "DecisionKind",
        "DecisionRecord",
        "DemandSignal",
        "Enrichment",
        "EvidenceRef",
        "OpenQuestion",
        "ProposalResult",
        "RejectedCandidate",
        "ResidualColumn",
        "RichnessGap",
        "RichnessReport",
        "classify",
        "select_for_user",
        "to_decision_inputs",
    }
    assert set(ms.__all__) == expected
    assert not hasattr(ms, "help_text")
    assert not hasattr(ms, "component")


def test_semantic_project_class() -> None:
    assert ms.SemanticProject is not None
    project = ms.SemanticProject(root="/tmp/test")
    assert not project.is_ready()


def test_readiness_public_dtos() -> None:
    assert ms.ReadinessReport is not None
    assert ms.ReadinessIssue is not None
    assert ms.EvidenceSummary is not None
    assert ms.ParitySummary is not None
    assert ms.PreviewSummary is not None


def test_typing_submodule() -> None:
    assert ms.typing is typing_mod
    assert not hasattr(ms.typing, "ComponentExpr")


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


def test_help_text_top_level_is_compact_directory(capsys) -> None:
    ms.help()

    captured = capsys.readouterr()
    assert "marivo.semantic" in captured.out
    # Each line shows: name, kind tag in brackets, description
    assert "ms.dataset" in captured.out
    assert "ms.metric" in captured.out
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


def test_help_json_top_level_returns_compact_directory(capsys) -> None:
    result = ms.help(format="json")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert isinstance(result, dict)
    assert result["schema_version"] == "1"
    assert result["surface"] == "marivo.semantic"
    assert "entries" in result
    assert "authoring_constraints" not in result
    entries = result["entries"]
    assert isinstance(entries, list)
    assert len(entries) > 0
    for entry in entries:
        assert "name" in entry
        assert "summary" in entry
        assert "kind" in entry
        assert entry["kind"] in {"callable", "class", "module", "topic", "removed"}
    entry_names = {e["name"] for e in entries}
    assert "dataset" in entry_names
    assert "metric" in entry_names
    assert "derived_metric" in entry_names
    assert "component" not in entry_names
    assert "constraints" in entry_names
    assert "decomposition" in entry_names
    assert "SemanticProject" in entry_names
    assert "typing" in entry_names


def test_help_json_metric_includes_constraints_and_examples(capsys) -> None:
    result = ms.help("metric", format="json")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert isinstance(result, dict)
    assert "metric(" in result["signature"]
    constraints = result["constraints"]
    assert isinstance(constraints, list)
    constraint_ids = {entry["id"] for entry in constraints}
    assert "metric_datasets_required" in constraint_ids
    assert "metric_component_scope" in constraint_ids
    assert "metric_derived_shape" not in constraint_ids
    assert "ast_component_arithmetic" not in constraint_ids
    assert "examples" in result


def test_help_json_time_field_includes_partition_pushdown_advisory(capsys) -> None:
    result = ms.help("time_field", format="json")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert isinstance(result, dict)
    constraints = result["constraints"]
    assert isinstance(constraints, list)
    constraint_ids = {entry["id"] for entry in constraints}
    assert "time_field_partition_pushdown" in constraint_ids
    advisory = next(
        entry for entry in constraints if entry["id"] == "time_field_partition_pushdown"
    )
    assert advisory["phase"] == "assembly"
    assert "date_format" in advisory["hint"]


def test_help_json_decomposition_documents_supported_builders_and_aggregation_boundary(
    capsys,
) -> None:
    result = ms.help("decomposition", format="json")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert isinstance(result, dict)
    assert result["kind"] == "topic"
    assert result["topic"] == "decomposition"
    builder_names = {entry["name"] for entry in result["builders"]}
    assert builder_names == {"sum", "ratio", "weighted_average"}
    assert "SQL aggregation" in result["summary"]
    assert any("ms.count()" in item for item in result["anti_patterns"])
    assert any("ms.mean()" in item for item in result["anti_patterns"])
    guidance = {entry["metric_shape"]: entry for entry in result["guidance"]}
    assert guidance["count"]["decomposition"] == "ms.sum()"
    assert ".count()" in guidance["count"]["body"]
    assert any("ms.derived_metric" in entry["body"] for entry in guidance.values())
    assert guidance["mean_or_average"]["decomposition"] == "ms.ratio(...)"
    assert (
        guidance["mean_or_average"]["body"] == "ms.derived_metric(..., decomposition=ms.ratio(...))"
    )
    assert (
        guidance["weighted_average"]["body"]
        == "ms.derived_metric(..., decomposition=ms.weighted_average(...))"
    )
    assert "ms.help('derived_metric', format='json')" in result["related_help"]
    assert "ms.help('component', format='json')" not in result["related_help"]


def test_help_text_decomposition_documents_aggregation_boundary(capsys) -> None:
    ms.help("decomposition")

    captured = capsys.readouterr()
    assert "decomposition is not SQL aggregation" in captured.out
    assert "ms.count()" in captured.out
    assert "ms.mean()" in captured.out


def test_help_json_constraints_cover_error_kinds() -> None:
    result = ms.help("constraints", format="json")

    assert isinstance(result, dict)
    constraints = result["constraints"]
    assert isinstance(constraints, list)
    covered = {entry["error_kind"] for entry in constraints}
    for kind in errors_mod.ErrorKind:
        assert kind.value in covered


def test_constraint_example_paths_exist() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for constraint in iter_constraints():
        if constraint.example is not None:
            assert (repo_root / constraint.example).exists(), constraint.example


def test_invalid_decomposition_hint_points_to_decomposition_help() -> None:
    constraint = get_constraint("decomposition_shape")
    assert constraint is not None
    assert "ms.help('decomposition', format='json')" in constraint.hint
    assert "aggregation" in constraint.hint


def test_derived_fanout_policy_hint_uses_derived_metric_api() -> None:
    constraint = get_constraint("metric_fanout_policy_derived")
    assert constraint is not None
    assert "ms.derived_metric" in constraint.hint
    assert "derived @ms.metric" not in constraint.hint


def test_removed_component_body_constraints_absent() -> None:
    assert get_constraint("metric_derived_shape") is None
    assert get_constraint("component_name_declared") is None
    assert get_constraint("ast_component_arithmetic") is None


def test_semantic_skill_constraint_table_matches_catalog() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    reference = repo_root / "marivo-skills/marivo-semantic/references/authoring-patterns.md"
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
    "missing_model",
    "missing_datasets",
    "invalid_ref",
    "invalid_decomposition",
    "invalid_component_body",
    "outside_loader_context",
    "metric_body_not_single_return",
    "invalid_ai_context",
    "sql_escape_hatch",
}

_EXPECTED_ASSEMBLY_KINDS = {
    "model_file_missing",
    "model_file_mismatch",
    "missing_dataset_ref",
    "missing_field_ref",
    "missing_metric_ref",
    "cross_model_cycle",
    "hour_time_field_prefix_missing",
    "invalid_relationship_endpoint",
    "organization_error",
    "invalid_project",
    "missing_metric_additivity",
    "missing_metric_root_dataset",
    "invalid_metric_root_dataset",
    "invalid_verification_mode",
    "invalid_dataset_versioning",
    "non_root_metric_aggregate",
    "invalid_metric_fanout_policy",
    "derived_metric_fanout_policy",
}

_EXPECTED_RUNTIME_KINDS = {
    "metric_not_found",
    "materialize_failed",
    "backend_mismatch",
    "compile_error",
    "cross_datasource_not_supported",
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
    ModelIR,
    DatasourceIR,
    DatasetIR,
    FieldIR,
    DecompositionIR,
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
    expected = {"model", "datasource", "dataset", "field", "time_field", "metric", "relationship"}
    actual = {k.value for k in SymbolKind}
    assert actual == expected


def test_parity_status_values() -> None:
    expected = {"verified", "unverified", "drifted"}
    actual = {k.value for k in ParityStatus}
    assert actual == expected


def test_verification_mode_values() -> None:
    expected = {"sql_parity", "python_native"}
    actual = {k.value for k in VerificationMode}
    assert actual == expected


def test_dataset_provenance_values() -> None:
    expected = {"ibis_table", "sql_view"}
    actual = {k.value for k in DatasetProvenance}
    assert actual == expected


def test_symbol_kind_is_str_enum() -> None:
    assert isinstance(SymbolKind.MODEL, str)
    assert SymbolKind.MODEL == "model"


def test_parity_status_is_str_enum() -> None:
    assert isinstance(ParityStatus.VERIFIED, str)
    assert ParityStatus.VERIFIED == "verified"


# ---------------------------------------------------------------------------
# Ref types
# ---------------------------------------------------------------------------


def test_dataset_ref() -> None:
    ref = DatasetRef("sales.orders")
    assert ref.semantic_id == "sales.orders"
    assert ref.kind == SymbolKind.DATASET
    assert "DatasetRef" in repr(ref)


def test_field_ref() -> None:
    ref = FieldRef("sales.orders.amount")
    assert ref.semantic_id == "sales.orders.amount"
    assert ref.kind == SymbolKind.FIELD


def test_field_ref_callable_without_resolver_raises() -> None:
    ref = FieldRef("sales.orders.amount")
    with pytest.raises(RuntimeError, match="no resolver"):
        ref(None)


def test_time_field_ref() -> None:
    ref = TimeFieldRef("sales.orders.order_date")
    assert ref.semantic_id == "sales.orders.order_date"
    assert ref.kind == SymbolKind.TIME_FIELD


def test_time_field_ref_callable_without_resolver_raises() -> None:
    ref = TimeFieldRef("sales.orders.order_date")
    with pytest.raises(RuntimeError, match="no resolver"):
        ref(None)


def test_metric_ref() -> None:
    ref = MetricRef("sales.revenue")
    assert ref.semantic_id == "sales.revenue"
    assert ref.kind == SymbolKind.METRIC


def test_relationship_ref() -> None:
    ref = RelationshipRef("sales.orders_to_items")
    assert ref.semantic_id == "sales.orders_to_items"
    assert ref.kind == SymbolKind.RELATIONSHIP


def test_base_ref_repr() -> None:
    ref = DatasetRef("sales.orders")
    assert repr(ref) == "DatasetRef('sales.orders')"


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


def test_find_project_exists() -> None:
    assert callable(ms.find_project)


def test_find_project_returns_none_without_project() -> None:
    """find_project should return None when no .marivo/semantic/ is found."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        result = ms.find_project(start_dir=tmp)
        assert result is None


def test_loader_context_dataclass() -> None:
    from marivo.semantic.loader import LoaderContext

    ctx = LoaderContext()
    assert ctx.current_model_file is None
    assert ctx.default_model is None
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
    def good_fn(table):
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
        m.dataset("test")
    with pytest.raises(SemanticRuntimeError):
        m.field("test")
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


def test_semantic_project_init() -> None:
    project = ms.SemanticProject(root="/tmp/test")
    assert not project.is_ready()
    assert project.errors() == ()


def test_semantic_project_load_works() -> None:
    """SemanticProject.load() now works (implemented in Slice 1)."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        semantic_root = Path(tmp) / ".marivo" / "semantic"
        semantic_root.mkdir(parents=True)
        project = ms.SemanticProject(root=semantic_root)
        result = project.load()
        assert result.status == "ready"


def test_semantic_project_reload_works() -> None:
    """SemanticProject.reload() now works (implemented in Slice 1)."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        semantic_root = Path(tmp) / ".marivo" / "semantic"
        semantic_root.mkdir(parents=True)
        project = ms.SemanticProject(root=semantic_root)
        result = project.reload()
        assert result.status == "ready"
