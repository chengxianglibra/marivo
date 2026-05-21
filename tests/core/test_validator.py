"""Tests for app.core.semantic.validator pure functions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from marivo.core.semantic.validator import (
    ValidationIssue,
    ValidationResult,
    _normalize_metric_dimension_ref,
    _optional_str,
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
    resolved_dimensions: list[Any] = field(default_factory=list)
    resolved_imported_dimensions: list[Any] = field(default_factory=list)
    imported_dimension_conflicts: dict[str, list[Any]] = field(default_factory=dict)
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
    capability_condition: str | None = None


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


# ── Pure helpers ───────────────────────────────────────────────────────


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
