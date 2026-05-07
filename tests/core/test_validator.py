"""Tests for app.core.semantic.validator pure functions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.semantic.validator import (
    ValidationIssue,
    ValidationResult,
    _expected_metric_input_types,
    _expected_predicate_operand_types,
    _normalize_metric_dimension_ref,
    _optional_str,
    gate_dimension_additivity_condition,
    gate_entity_field_resolution,
    gate_intent_specific,
    gate_intent_support,
    gate_profile_integrity,
    gate_request_shape,
    validation_error_message,
)

# ── Shared helpers ──────────────────────────────────────────────────────


class _FakeResolvedObject:
    def __init__(
        self,
        ref: str = "metric.test",
        revision: int = 1,
        object_id: str = "oid",
        semantic_object: dict | None = None,
    ) -> None:
        self.ref = ref
        self.revision = revision
        self.object_id = object_id
        self.semantic_object = semantic_object or {}


# ── ValidationIssue / ValidationResult ─────────────────────────────────


def test_validation_issue_to_dict() -> None:
    issue = ValidationIssue(
        code="TEST_CODE",
        gate="test_gate",
        category="compiler",
        severity="error",
        message="test message",
        subject_ref="metric.test",
    )
    d = issue.to_dict()
    assert d["code"] == "TEST_CODE"
    assert d["gate"] == "test_gate"
    assert d["severity"] == "error"
    assert d["subject_ref"] == "metric.test"


def test_validation_result_ok() -> None:
    result = ValidationResult(ok=True)
    assert result.ok is True
    assert result.error_issues() == []


def test_validation_result_error_issues() -> None:
    issue = ValidationIssue(code="E", gate="g", category="c", severity="error", message="m")
    warning = ValidationIssue(code="W", gate="g", category="c", severity="warning", message="w")
    result = ValidationResult(ok=False, issues=[issue, warning])
    errors = result.error_issues()
    assert len(errors) == 1
    assert errors[0].code == "E"


def test_validation_result_issues_for_category() -> None:
    issue = ValidationIssue(
        code="E", gate="g", category="compatibility", severity="error", message="m"
    )
    result = ValidationResult(ok=False, issues=[issue])
    compat = result.issues_for_category("compatibility")
    assert len(compat) == 1
    assert result.issues_for_category("compiler") == []


def test_validation_result_primary_error_issue_compiler_first() -> None:
    compat = ValidationIssue(
        code="C", gate="g", category="compatibility", severity="error", message="c"
    )
    compiler = ValidationIssue(
        code="R", gate="g", category="compiler", severity="error", message="r"
    )
    result = ValidationResult(ok=False, issues=[compat, compiler])
    primary = result.primary_error_issue()
    assert primary.code == "R"  # compiler takes precedence


def test_validation_result_to_dict() -> None:
    issue = ValidationIssue(code="E", gate="g", category="c", severity="error", message="m")
    result = ValidationResult(
        ok=False,
        issues=[issue],
        resolved_filter_time_ref="time.event_date",
        validated_dimension_refs=["dimension.platform"],
    )
    d = result.to_dict()
    assert d["ok"] is False
    assert len(d["issues"]) == 1
    assert d["resolved_filter_time_ref"] == "time.event_date"


def test_validation_error_message() -> None:
    issue = ValidationIssue(
        code="COMPILER_TEST", gate="g", category="c", severity="error", message="something broke"
    )
    result = ValidationResult(ok=False, issues=[issue])
    assert validation_error_message(result) == "COMPILER_TEST: something broke"


# ── gate_profile_integrity ─────────────────────────────────────────────


@dataclass
class _FakeProfileIssue:
    code: str
    message: str
    subject_ref: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeDerivedState:
    profile_validation_issues: list[Any] = field(default_factory=list)
    metric_capabilities: Any = None
    metric_requirements: Any = None
    process_capabilities: Any = None


def test_gate_profile_integrity_empty() -> None:
    state = _FakeDerivedState()
    assert gate_profile_integrity(state) == []


def test_gate_profile_integrity_with_issues() -> None:
    issues = [_FakeProfileIssue(code="MISSING", message="profile missing")]
    state = _FakeDerivedState(profile_validation_issues=issues)
    result = gate_profile_integrity(state)
    assert len(result) == 1
    assert result[0].code == "MISSING"
    assert result[0].gate == "profile_integrity"
    assert result[0].category == "readiness"


# ── gate_request_shape ─────────────────────────────────────────────────


@dataclass
class _FakeNormRequest:
    metric_ref: str | None = None
    request_time_scope: dict[str, Any] | None = None
    intent_kind: str = "metric_query"
    process_ref: str | None = None
    request_dimensions: list[str] = field(default_factory=list)


@dataclass
class _FakeResolvedInputs:
    normalized_request: Any = None
    resolved_metric: Any = None
    resolved_process: Any = None
    resolved_filter_time: Any = None
    field_resolution_issues: list[Any] = field(default_factory=list)
    resolved_entity_fields: dict[str, Any] = field(default_factory=dict)
    entity_field_usage_details: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    resolved_dimensions: list[Any] = field(default_factory=list)
    resolved_imported_dimensions: list[Any] = field(default_factory=list)
    imported_dimension_conflicts: dict[str, list[Any]] = field(default_factory=dict)
    metric_entity_anchor_ref: str | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)


def test_gate_request_shape_metric_query_no_ref() -> None:
    req = _FakeNormRequest(metric_ref=None)
    inputs = _FakeResolvedInputs(normalized_request=req, resolved_metric=_FakeResolvedObject())
    issues = gate_request_shape("metric_query", inputs)
    assert len(issues) == 1
    assert issues[0].code == "COMPILER_REQUEST_INVALID"


def test_gate_request_shape_time_unresolved() -> None:
    req = _FakeNormRequest(
        metric_ref="metric.revenue",
        request_time_scope={"mode": "single_window"},
    )
    inputs = _FakeResolvedInputs(
        normalized_request=req,
        resolved_metric=_FakeResolvedObject(),
        resolved_filter_time=None,
    )
    issues = gate_request_shape("observe", inputs)
    assert len(issues) == 1
    assert issues[0].code == "COMPILER_TIME_REF_UNRESOLVED"


# ── gate_intent_support ────────────────────────────────────────────────


@dataclass
class _FakeCapabilities:
    supports_compare: bool = True
    supports_validate: bool = True
    capability_condition: str | None = None
    additive_dimensions: list[str] | None = None


def test_gate_intent_support_no_compare() -> None:
    caps = _FakeCapabilities(supports_compare=False)
    state = _FakeDerivedState(metric_capabilities=caps)
    req = _FakeNormRequest(request_time_scope={"mode": "compare"})
    inputs = _FakeResolvedInputs(
        normalized_request=req,
        resolved_metric=_FakeResolvedObject(),
    )
    issues = gate_intent_support("metric_query", inputs, state)
    assert len(issues) == 1
    assert issues[0].code == "COMPILER_INTENT_UNSUPPORTED"


def test_gate_intent_support_ok() -> None:
    caps = _FakeCapabilities(supports_compare=True)
    state = _FakeDerivedState(metric_capabilities=caps)
    req = _FakeNormRequest(request_time_scope={"mode": "compare"})
    inputs = _FakeResolvedInputs(
        normalized_request=req,
        resolved_metric=_FakeResolvedObject(),
    )
    issues = gate_intent_support("metric_query", inputs, state)
    assert len(issues) == 0


# ── gate_entity_field_resolution ───────────────────────────────────────


@dataclass
class _FakeFieldIssue:
    code: str
    field_ref: str
    message: str
    usage_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def test_gate_entity_field_resolution_empty() -> None:
    inputs = _FakeResolvedInputs(field_resolution_issues=[])
    assert gate_entity_field_resolution(inputs) == []


def test_gate_entity_field_resolution_binding() -> None:
    issues = [_FakeFieldIssue(code="missing_entity_binding", field_ref="f", message="m")]
    inputs = _FakeResolvedInputs(field_resolution_issues=issues)
    result = gate_entity_field_resolution(inputs)
    assert len(result) == 1
    assert result[0].category == "readiness"


def test_gate_entity_field_resolution_compatibility() -> None:
    issues = [_FakeFieldIssue(code="incompatible_field", field_ref="f", message="m")]
    inputs = _FakeResolvedInputs(field_resolution_issues=issues)
    result = gate_entity_field_resolution(inputs)
    assert result[0].category == "compatibility"


# ── Pure helpers ───────────────────────────────────────────────────────


def test_expected_metric_input_types_sum() -> None:
    assert _expected_metric_input_types("sum") == {"integer", "number"}


def test_expected_metric_input_types_boolean() -> None:
    assert _expected_metric_input_types("boolean_any") == {"boolean"}


def test_expected_metric_input_types_default() -> None:
    result = _expected_metric_input_types("count")
    assert "string" in result
    assert "integer" in result


def test_expected_predicate_operand_types_gt() -> None:
    assert _expected_predicate_operand_types("gt") == {"integer", "number", "date", "datetime"}


def test_expected_predicate_operand_types_eq() -> None:
    assert _expected_predicate_operand_types("eq") == set()


def test_normalize_metric_dimension_ref_full() -> None:
    assert _normalize_metric_dimension_ref("dimension.platform") == "dimension.platform"


def test_normalize_metric_dimension_ref_bare() -> None:
    assert _normalize_metric_dimension_ref("platform") == "dimension.platform"


def test_normalize_metric_dimension_ref_other_dotted() -> None:
    assert _normalize_metric_dimension_ref("entity.user") is None


def test_normalize_metric_dimension_ref_none() -> None:
    assert _normalize_metric_dimension_ref(None) is None


def test_optional_str_none() -> None:
    assert _optional_str(None) is None


def test_optional_str_whitespace() -> None:
    assert _optional_str("  ") is None


def test_optional_str_value() -> None:
    assert _optional_str("  hello  ") == "hello"


# ── gate_dimension_additivity_condition ─────────────────────────────────


def test_gate_dimension_additivity_ok() -> None:
    caps = _FakeCapabilities(
        capability_condition="dimension_must_be_allowed",
        additive_dimensions=["dimension.platform"],
    )
    state = _FakeDerivedState(metric_capabilities=caps)

    @dataclass
    class _Req:
        request_dimensions: list[str] = field(default_factory=list)

    @dataclass
    class _Inputs:
        resolved_dimension_refs: list[str] = field(default_factory=list)
        normalized_request: Any = None
        resolved_metric: Any = None

    inputs = _Inputs(
        resolved_dimension_refs=["dimension.platform"],
        normalized_request=_Req(request_dimensions=["dimension.platform"]),
        resolved_metric=_FakeResolvedObject(),
    )
    issues = gate_dimension_additivity_condition("decompose", inputs, state)
    assert len(issues) == 0


def test_gate_dimension_additivity_blocked() -> None:
    caps = _FakeCapabilities(
        capability_condition="dimension_must_be_allowed",
        additive_dimensions=["dimension.platform"],
    )
    state = _FakeDerivedState(metric_capabilities=caps)

    @dataclass
    class _Req:
        request_dimensions: list[str] = field(default_factory=list)

    @dataclass
    class _Inputs:
        resolved_dimension_refs: list[str] = field(default_factory=list)
        normalized_request: Any = None
        resolved_metric: Any = None

    inputs = _Inputs(
        resolved_dimension_refs=["dimension.region"],
        normalized_request=_Req(request_dimensions=["dimension.region"]),
        resolved_metric=_FakeResolvedObject(),
    )
    issues = gate_dimension_additivity_condition("decompose", inputs, state)
    assert len(issues) == 1
    assert issues[0].code == "COMPILER_DIMENSION_NOT_ADDITIVE"


def test_gate_dimension_additivity_wrong_step_type() -> None:
    caps = _FakeCapabilities(
        capability_condition="dimension_must_be_allowed",
        additive_dimensions=["dimension.platform"],
    )
    state = _FakeDerivedState(metric_capabilities=caps)
    issues = gate_dimension_additivity_condition("observe", _FakeResolvedInputs(), state)
    assert len(issues) == 0


# ── gate_intent_specific ───────────────────────────────────────────────


def test_gate_intent_specific_validate_not_supported() -> None:
    caps = _FakeCapabilities(supports_validate=False)
    state = _FakeDerivedState(metric_capabilities=caps)
    inputs = _FakeResolvedInputs(resolved_metric=_FakeResolvedObject())
    issues = gate_intent_specific("validate", inputs, state)
    assert len(issues) == 1
    assert issues[0].code == "COMPILER_INTENT_UNSUPPORTED"


def test_gate_intent_specific_validate_supported() -> None:
    caps = _FakeCapabilities(supports_validate=True)
    state = _FakeDerivedState(metric_capabilities=caps)
    inputs = _FakeResolvedInputs(resolved_metric=_FakeResolvedObject())
    issues = gate_intent_specific("validate", inputs, state)
    assert len(issues) == 0
