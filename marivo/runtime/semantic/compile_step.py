"""I/O-bound compile_step orchestrator.

Extracted from ``marivo.analysis_core.compiler``.  Pure SQL builder helpers
live in ``marivo.core.semantic.compiler``; this module keeps only the
I/O-coupled orchestration (resolution, validation, IR bundle construction).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, TypedDict, cast

from marivo.core.semantic.calendar import (
    get_calendar_policy,
    resolve_calendar_baseline_window,
    resolve_calendar_bucket_pairing,
)
from marivo.core.semantic.compiler import (
    CompiledQuery,
    SemanticCompilerError,
    SemanticRequestCompatibilityError,
    build_aggregate_comparison_query,
    build_metric_query,
    build_windowed_aggregate_query,
)
from marivo.core.semantic.ir import (
    STEP_ARTIFACT_KINDS,
    AnalysisStepIR,
    ArtifactLineageEntry,
    CompileReport,
    IntentNode,
    IntentRequestSnapshot,
    IrArtifact,
    IrBundle,
    IrInputSnapshot,
    IrPlan,
    IrPlanHeader,
    LoweringRequirement,
    MeasurementNode,
    MetricRefSnapshot,
    OutputBinding,
    ProcessNode,
    ProcessRefSnapshot,
    ProfileUsageTrace,
    RelationshipRefSnapshot,
    SemanticCompileError,
    ValidationRecord,
    ValidationSummary,
)
from marivo.core.semantic.resolution import ResolvedSemanticObject
from marivo.runtime.evidence.ref_boundary import assert_no_canonical_refs_in_semantic_payload
from marivo.runtime.evidence.semantic_repository import SemanticRuntimeRepository
from marivo.runtime.semantic.analysis_validator import (
    ValidationIssue,
    validate_compiler_inputs,
    validation_error_message,
)
from marivo.runtime.semantic.calendar_data_runtime import (
    CalendarDataReaderLike,
    CalendarDataReadResult,
    CalendarDataResolutionError,
)
from marivo.runtime.semantic.resolution_orchestrator import (
    NormalizedCompilerRequest,
    ResolvedCompilerInputs,
    normalize_step_request,
    resolve_compiler_inputs,
)

__all__ = [
    "compile_step",
]

# ── Capability profile stubs (inlined from analysis_core.capability_profiles) ──


@dataclass(slots=True)
class DerivedMetricCapabilities:
    supports_observe: bool = True
    supports_compare: bool = False
    supports_decompose: bool = False
    supports_attribute: bool = False
    supports_test: bool = False
    supports_detect: bool = False
    supports_validate: bool = False
    additive_dimensions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DerivedProcessCapabilities:
    supports_time_projection: bool = False
    supports_experiment_inference: bool = False
    supports_cohort_inference: bool = False
    inferential_ready: bool | None = None
    supported_sample_summaries: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DerivedBindingCapabilities:
    inferential_ready: bool | None = None


@dataclass(slots=True)
class MetricProcessRequirements:
    contract_modes: list[str] = field(default_factory=list)
    context_kinds: list[str] = field(default_factory=list)
    entity_refs: list[str] = field(default_factory=list)
    population_subject_refs: list[str] = field(default_factory=list)
    required_relationship_refs: list[str] = field(default_factory=list)
    grain_compatibility: dict[str, Any] | None = None
    time_compatibility: dict[str, Any] | None = None
    field_profile_requirements: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ProfileTrace:
    subject_ref: str
    profile_ref: str | None
    subject_revision: int | None
    resolved_subject_revision: int | None
    applied: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_ref": self.subject_ref,
            "profile_ref": self.profile_ref,
            "subject_revision": self.subject_revision,
            "resolved_subject_revision": self.resolved_subject_revision,
            "applied": self.applied,
            "reason": self.reason,
        }


@dataclass(slots=True)
class ProfileValidationIssue:
    code: str
    message: str
    subject_ref: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DerivedCompilerState:
    metric_capabilities: DerivedMetricCapabilities | None = None
    process_capabilities: DerivedProcessCapabilities | None = None
    binding_capabilities: dict[str, DerivedBindingCapabilities] = field(default_factory=dict)
    metric_requirements: MetricProcessRequirements = field(
        default_factory=MetricProcessRequirements
    )
    profile_traces: list[ProfileTrace] = field(default_factory=list)
    profile_validation_issues: list[ProfileValidationIssue] = field(default_factory=list)
    usage_trace: list[dict[str, Any]] = field(default_factory=list)

    def capabilities_payload(self) -> dict[str, Any]:
        return {}


def derive_compiler_state(*_args: Any, **_kwargs: Any) -> DerivedCompilerState:
    """Stub — returns default state during OSI v2 migration.  See Task 7."""
    return DerivedCompilerState()


# ── Predicate validator stubs (inlined from analysis_core.predicate_validator) ──
# These are preserved for import compatibility only — all return empty results.


@dataclass(slots=True)
class PredicateRefWithUsage:
    ref: str
    required_usage: Any = ""
    usage: str = ""
    scope_expression: dict[str, Any] | None = None


@dataclass(slots=True)
class PredicateLayerRef:
    layer: str
    ref: str


@dataclass(slots=True)
class ResolvedAtom:
    ref: str
    usage: str
    operator: str = ""
    value: Any = None
    scope_expression: dict[str, Any] | None = None


class NormalizedPredicateAtom(TypedDict, total=False):
    ref: str
    usage: str
    operator: str
    value: Any
    scope_expression: dict[str, Any]


class NormalizedComponentPredicateInput(TypedDict, total=False):
    component_ref: str
    atoms: list[NormalizedPredicateAtom]


class NormalizedPredicateInput(TypedDict, total=False):
    metric_ref: str
    components: list[NormalizedComponentPredicateInput]


class ComponentLoweringInput(TypedDict, total=False):
    component_ref: str
    binding_ref: str
    atoms: list[NormalizedPredicateAtom]


class LoweringPrecheckDiagnostic(TypedDict, total=False):
    component_ref: str
    atom_ref: str
    code: str
    message: str


def validate_predicate_contracts(*_args: Any, **_kwargs: Any) -> list[Any]:
    return []


def validate_request_scope(*_args: Any, **_kwargs: Any) -> list[Any]:
    return []


def validate_predicate_conflicts(*_args: Any, **_kwargs: Any) -> list[Any]:
    return []


def build_predicate_filter_lineage(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {}


def build_normalized_predicate_input(*_args: Any, **_kwargs: Any) -> NormalizedPredicateInput:
    return NormalizedPredicateInput()


def build_component_lowering_inputs(*_args: Any, **_kwargs: Any) -> list[ComponentLoweringInput]:
    return []


def run_lowering_precheck(*_args: Any, **_kwargs: Any) -> list[Any]:
    return []


def collect_component_fields(*_args: Any, **_kwargs: Any) -> list[str]:
    return []


def collect_layered_predicate_refs(*_args: Any, **_kwargs: Any) -> list[PredicateLayerRef]:
    return []


# ── Scoped query helpers (imported from core.semantic.compiler) ──
# These are needed by compile_step but live in core now.  We import them
# at module level so the compile_step function can call them.

from marivo.core.semantic.compiler import (  # noqa: E402
    _build_scoped_query_parts,
    _expand_group_by_aliases,
    _metric_query_dimension_sql_expressions,
    _normalize_metric_query_order,
    _require_scoped_query_mode,
)

# ── Validation gate order (needed by _build_validation_trace) ──

_VALIDATION_GATE_ORDER: tuple[
    Literal[
        "request_shape",
        "intent_support",
        "metric_process_compatibility",
        "binding_grounding",
        "predicate_contract",
        "scope_validation",
        "predicate_conflict",
        "dimension_compatibility",
        "intent_specific",
        "dimension_additivity",
        "lowering_precheck",
    ],
    ...,
] = (
    "request_shape",
    "intent_support",
    "metric_process_compatibility",
    "binding_grounding",
    "predicate_contract",
    "scope_validation",
    "predicate_conflict",
    "dimension_compatibility",
    "intent_specific",
    "dimension_additivity",
    "lowering_precheck",
)


_CALENDAR_ALIGNMENT_SUPPORTED_GRAINS = frozenset({"day", "week", "month"})


# ── I/O-bound helper functions ──


def _build_compile_error(validation_message: str, validation_result: Any) -> SemanticCompileError:
    first_error = validation_result.primary_error_issue()
    compile_error: SemanticCompileError = {
        "error_code": first_error.code,
        "failed_gate": first_error.gate,
        "message": validation_message,
    }
    if first_error.subject_ref is not None:
        compile_error["subject_ref"] = first_error.subject_ref
    if first_error.details:
        compile_error["details"] = dict(first_error.details)
    return compile_error


def _build_request_compatibility_error(
    *,
    step_type: str,
    normalized_request: Any,
    resolved_inputs: Any,
    validation_result: Any,
) -> dict[str, Any]:
    issues = validation_result.issues_for_category("compatibility")
    primary_issue = issues[0]
    request_context = {
        "step_type": step_type,
        "intent_kind": normalized_request.intent_kind,
        "metric_ref": normalized_request.metric_ref,
        "process_ref": normalized_request.process_ref,
        "dimension_refs": list(normalized_request.request_dimensions),
    }
    request_context = {
        key: value for key, value in request_context.items() if value not in (None, [])
    }
    return {
        "message": "Request is incompatible with resolved semantic objects",
        "code": "semantic_request_incompatible",
        "category": "compatibility",
        "subject_ref": primary_issue.subject_ref,
        "issues": [issue.to_dict() for issue in issues],
        "request_context": request_context,
    }


def _requests_imported_dimensions(resolved_inputs: ResolvedCompilerInputs) -> bool:
    return False


def _resolve_imported_dimension_physical_sources(
    resolved_inputs: ResolvedCompilerInputs,
    *,
    semantic_repository: SemanticRuntimeRepository | None,
) -> tuple[list[dict[str, Any]], list[ValidationIssue]]:
    _ = (resolved_inputs, semantic_repository)
    return [], []


def _build_validation_trace(validation_result: Any) -> list[ValidationRecord]:
    failed_gates = {issue.gate for issue in validation_result.issues if issue.severity == "error"}
    warning_gates = {issue.gate for issue in validation_result.issues if issue.severity != "error"}
    trace: list[ValidationRecord] = []
    for gate in _VALIDATION_GATE_ORDER:
        if gate in failed_gates:
            continue
        record: ValidationRecord = {
            "validation_kind": gate,
            "status": "passed",
        }
        if gate in warning_gates:
            record["reason_code"] = "passed_with_warning"
        trace.append(record)
    return trace


def _build_validation_summary(
    validation_result: Any, validation_trace: list[ValidationRecord]
) -> ValidationSummary:
    summary: ValidationSummary = {
        "passed_gate_count": len(validation_trace),
        "warning_count": len(
            [issue for issue in validation_result.issues if issue.severity != "error"]
        ),
        "validated_dimension_refs": list(validation_result.validated_dimension_refs),
    }
    if validation_result.resolved_filter_time_ref is not None:
        summary["resolved_filter_time_ref"] = validation_result.resolved_filter_time_ref
    return summary


def _build_profile_usage_trace(profile_traces: list[Any]) -> list[ProfileUsageTrace]:
    trace_payload: list[ProfileUsageTrace] = []
    for trace in profile_traces:
        item: ProfileUsageTrace = {
            "subject_ref": trace.subject_ref,
            "applied": trace.applied,
            "reason": trace.reason,
        }
        if trace.profile_ref is not None:
            item["profile_ref"] = trace.profile_ref
        if trace.subject_revision is not None:
            item["subject_revision"] = trace.subject_revision
        if trace.resolved_subject_revision is not None:
            item["resolved_subject_revision"] = trace.resolved_subject_revision
        trace_payload.append(item)
    return trace_payload


def _build_lowering_requirements(
    *,
    step: AnalysisStepIR,
    normalized_request: NormalizedCompilerRequest,
    resolved_inputs: ResolvedCompilerInputs,
    intent_node_id: str,
) -> list[LoweringRequirement]:
    requirements: list[LoweringRequirement] = [
        {
            "requirement_kind": "engine_sql_execution",
            "source_node_id": intent_node_id,
        }
    ]
    if normalized_request.request_time_scope:
        requirements.append(
            {
                "requirement_kind": "time_window_filter",
                "source_node_id": intent_node_id,
            }
        )
    return requirements


def _resolve_calendar_alignment_plan(
    normalized_request: NormalizedCompilerRequest,
    *,
    semantic_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    semantic_context = semantic_context or {}
    policy_ref = normalized_request.request_calendar_policy_ref
    if policy_ref is None:
        return None
    request_time_scope = normalized_request.request_time_scope or {}
    if not request_time_scope:
        return None
    mode = str(request_time_scope.get("mode") or "").strip()
    if mode != "single_window":
        return None
    grain = str(request_time_scope.get("grain") or "").strip()
    if grain == "hour":
        raise SemanticRequestCompatibilityError(
            {
                "message": "Calendar alignment policies do not support hour-grain observe windows",
                "code": "calendar_policy_hour_grain_unsupported",
                "category": "compatibility",
                "issues": [
                    {
                        "code": "calendar_policy_hour_grain_unsupported",
                        "message": (
                            "calendar_policy_ref requires a day/week/month window; "
                            "hour-grain observe requests are not supported"
                        ),
                        "details": {
                            "policy_ref": policy_ref,
                            "request_grain": grain,
                        },
                    }
                ],
                "request_context": {
                    "intent_kind": normalized_request.intent_kind,
                    "calendar_policy_ref": policy_ref,
                    "request_grain": grain,
                },
            }
        )
    if grain not in _CALENDAR_ALIGNMENT_SUPPORTED_GRAINS:
        return None

    current_window = _date_window_from_time_scope(request_time_scope)
    policy = get_calendar_policy(policy_ref)
    baseline_window = resolve_calendar_baseline_window(
        current_window=current_window,
        rule=policy.resolved_baseline_generation_rule,
    )
    calendar_data = _read_calendar_alignment_data(
        current_window=current_window,
        baseline_window=baseline_window,
        semantic_context=semantic_context,
    )
    pairing_resolution = resolve_calendar_bucket_pairing(
        current_window=current_window,
        baseline_window=baseline_window,
        matching_strategy=policy.matching_strategy,
        fallback_strategy=policy.fallback_strategy,
        annotation_rows=calendar_data.annotation_rows,
    )
    bucket_pairing = pairing_resolution.bucket_pairing
    comparability_warnings = pairing_resolution.comparability_warnings
    coverage_summary = _build_calendar_alignment_coverage(bucket_pairing)
    return {
        "policy_ref": policy.policy_ref,
        "comparison_basis": policy.comparison_basis,
        "resolved_calendar_source": calendar_data.resolved_calendar_source,
        "resolved_calendar_version": calendar_data.resolved_calendar_version,
        "resolved_baseline_generation_rule": {
            "strategy": policy.resolved_baseline_generation_rule.strategy,
            "offset_value": policy.resolved_baseline_generation_rule.offset_value,
            "offset_unit": policy.resolved_baseline_generation_rule.offset_unit,
            "fixed_start": None,
            "fixed_end": None,
            "named_window_ref": None,
        },
        "current_window": _serialize_calendar_window(current_window),
        "baseline_window": _serialize_calendar_window(baseline_window),
        "bucket_pairing": bucket_pairing,
        "rollup_safe": pairing_resolution.rollup_safe,
        "coverage_summary": coverage_summary,
        "comparability_warnings": comparability_warnings,
        "source_lineage": calendar_data.source_lineage,
    }


def _date_window_from_time_scope(time_scope: Mapping[str, Any]) -> tuple[date, date]:
    current = dict(time_scope.get("current") or {})
    start = _parse_date_like(str(current.get("start") or ""))
    end = _parse_date_like(str(current.get("end") or ""))
    if start >= end:
        raise ValueError("calendar alignment requires time_scope.current.start < end")
    return start, end


def _parse_date_like(value: str) -> date:
    if not value:
        raise ValueError("calendar alignment requires date window boundaries")
    with_datetime = value.replace(" ", "T")
    try:
        return datetime.fromisoformat(with_datetime).date()
    except ValueError:
        return date.fromisoformat(value[:10])


def _read_calendar_alignment_data(
    *,
    current_window: tuple[date, date],
    baseline_window: tuple[date, date],
    semantic_context: Mapping[str, Any],
) -> CalendarDataReadResult:
    reader = semantic_context.get("calendar_data_reader")
    if not isinstance(reader, CalendarDataReaderLike):
        raise SemanticRequestCompatibilityError(
            {
                "message": "Calendar alignment requires a configured calendar data reader",
                "code": "calendar_data_missing",
                "category": "compatibility",
                "issues": [
                    {
                        "code": "calendar_data_missing",
                        "message": (
                            "calendar_policy_ref requires a configured calendar snapshot reader; "
                            "temporary annotation snapshot injection is no longer supported"
                        ),
                        "details": {},
                    }
                ],
                "request_context": {
                    "current_window": _serialize_calendar_window(current_window),
                    "baseline_window": _serialize_calendar_window(baseline_window),
                },
            }
        )
    try:
        return reader.read_for_alignment(
            current_window=current_window,
            baseline_window=baseline_window,
        )
    except CalendarDataResolutionError as error:
        raise SemanticRequestCompatibilityError(
            {
                "message": str(error),
                "code": "calendar_data_missing",
                "category": "compatibility",
                "issues": [
                    {
                        "code": "calendar_data_missing",
                        "message": str(error),
                        "details": dict(error.details),
                    }
                ],
                "request_context": {
                    "current_window": _serialize_calendar_window(current_window),
                    "baseline_window": _serialize_calendar_window(baseline_window),
                },
            }
        ) from error


def _build_calendar_alignment_coverage(bucket_pairing: list[dict[str, Any]]) -> dict[str, Any]:
    aligned_bucket_count = sum(
        1 for bucket in bucket_pairing if bucket.get("baseline_bucket_start") is not None
    )
    total_bucket_count = len(bucket_pairing)
    unpaired_bucket_count = total_bucket_count - aligned_bucket_count
    aligned_ratio = aligned_bucket_count / total_bucket_count if total_bucket_count else 0.0
    return {
        "aligned_bucket_count": aligned_bucket_count,
        "unpaired_bucket_count": unpaired_bucket_count,
        "aligned_ratio": aligned_ratio,
    }


def _serialize_calendar_window(window: tuple[date, date] | None) -> dict[str, str] | None:
    if window is None:
        return None
    return {
        "start": window[0].isoformat(),
        "end": window[1].isoformat(),
    }


def _stable_plan_id(step: AnalysisStepIR, normalized_request: NormalizedCompilerRequest) -> str:
    raw = "|".join(
        [
            step.step_type,
            str(step.index),
            normalized_request.metric_ref or "",
            normalized_request.process_ref or "",
            normalized_request.table_name or "",
            ",".join(normalized_request.request_dimensions),
            normalized_request.request_result_mode or "",
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"ir_plan.{step.step_type}.{step.index}.{digest}"


def _metric_snapshot(
    metric: ResolvedSemanticObject,
) -> MetricRefSnapshot:
    header = dict(metric.semantic_object.get("header") or {})
    snapshot: MetricRefSnapshot = {
        "metric_ref": metric.ref,
        "resolved_metric_revision": metric.revision,
        "resolved_metric_object_id": metric.object_id,
    }
    observation_grain_ref = _optional_str(header.get("observation_grain_ref"))
    if observation_grain_ref is not None:
        snapshot["resolved_observation_grain_ref"] = observation_grain_ref
    return snapshot


def _process_snapshot(process: ResolvedSemanticObject) -> ProcessRefSnapshot:
    interface_contract = dict(process.semantic_object.get("interface_contract") or {})
    snapshot: ProcessRefSnapshot = {
        "process_ref": process.ref,
    }
    anchor_time_ref = _optional_str(interface_contract.get("anchor_time_ref"))
    if anchor_time_ref is not None:
        snapshot["resolved_anchor_time_ref"] = anchor_time_ref
    return snapshot


def _relationship_snapshot(relationship: Any) -> RelationshipRefSnapshot:
    return {
        "relationship_ref": relationship.relationship_ref,
        "left_entity_ref": relationship.left_entity_ref,
        "right_entity_ref": relationship.right_entity_ref,
        "revision": relationship.revision,
        "key_alignment": relationship.key_alignment,
        "time_alignment": relationship.time_alignment,
        "cardinality": relationship.cardinality,
        "grain_compatibility": relationship.grain_compatibility,
        "snapshot_effective_window_alignment": (relationship.snapshot_effective_window_alignment),
    }


def _intent_request_snapshot(
    normalized_request: NormalizedCompilerRequest,
    resolved_inputs: ResolvedCompilerInputs,
) -> IntentRequestSnapshot:
    options: dict[str, str | int | float | bool | None] = {}
    for key, value in normalized_request.request_options.items():
        if isinstance(value, (bool, int, float, str)) or value is None:
            options[key] = value
    snapshot: IntentRequestSnapshot = {
        "intent_kind": normalized_request.intent_kind,
        "request_class": normalized_request.request_class,
    }
    if normalized_request.request_dimensions:
        snapshot["requested_dimensions"] = list(normalized_request.request_dimensions)
    if normalized_request.request_result_mode is not None:
        snapshot["requested_result_mode"] = normalized_request.request_result_mode
    if normalized_request.request_calendar_policy_ref is not None:
        snapshot["requested_calendar_policy_ref"] = normalized_request.request_calendar_policy_ref
    if resolved_inputs.resolved_filter_time is not None:
        snapshot["request_time_scope_ref"] = resolved_inputs.resolved_filter_time.ref
    if options:
        snapshot["request_options"] = options
    return snapshot


def _build_ir_inputs(
    normalized_request: NormalizedCompilerRequest,
    resolved_inputs: ResolvedCompilerInputs,
) -> IrInputSnapshot:
    input_snapshot: IrInputSnapshot = {
        "intent_request": _intent_request_snapshot(normalized_request, resolved_inputs),
    }
    if normalized_request.metric_ref is not None:
        input_snapshot["metric_ref"] = normalized_request.metric_ref
    process_refs = [
        process_ref
        for process_ref in (
            normalized_request.process_ref,
            normalized_request.left_process_ref,
            normalized_request.right_process_ref,
        )
        if process_ref is not None
    ]
    if process_refs:
        input_snapshot["process_refs"] = process_refs
    if resolved_inputs.resolved_relationships:
        input_snapshot["resolved_relationships"] = [
            _relationship_snapshot(relationship)
            for relationship in resolved_inputs.resolved_relationships.values()
        ]
    if resolved_inputs.resolved_metric is not None:
        input_snapshot["resolved_metric"] = _metric_snapshot(
            resolved_inputs.resolved_metric,
        )
    resolved_processes = [
        process
        for process in (
            resolved_inputs.resolved_process,
            resolved_inputs.resolved_left_process,
            resolved_inputs.resolved_right_process,
        )
        if process is not None
    ]
    if resolved_processes:
        input_snapshot["resolved_processes"] = [
            _process_snapshot(process) for process in resolved_processes
        ]
    return input_snapshot


def _measurement_node(
    *,
    step: AnalysisStepIR,
    resolved_metric: ResolvedSemanticObject,
    output_binding: OutputBinding,
    resolved_inputs: ResolvedCompilerInputs | None = None,
    semantic_repository: Any | None = None,
) -> tuple[MeasurementNode, NormalizedPredicateInput | None]:
    header = dict(resolved_metric.semantic_object.get("header") or {})
    sample_kind = cast(
        "Literal['numeric', 'rate', 'binary', 'survival']",
        _optional_str(header.get("sample_kind")) or "numeric",
    )
    additive_dimensions = header.get("additive_dimensions", [])
    node: MeasurementNode = {
        "node_id": f"measurement:{step.index}",
        "node_type": "measurement",
        "metric_ref": resolved_metric.ref,
        "observed_entity_ref": _optional_str(header.get("observed_entity_ref")) or "",
        "observation_grain_ref": _optional_str(header.get("observation_grain_ref")) or "",
        "sample_kind": sample_kind,
        "value_semantics": _optional_str(header.get("value_semantics")) or "",
        "additive_dimensions": additive_dimensions,
        "output_bindings": [output_binding],
    }
    inferential_summary_mode = _optional_str(header.get("inferential_summary_mode"))
    if inferential_summary_mode is not None:
        node["inferential_summary_mode"] = inferential_summary_mode
    normalized_predicate_input: NormalizedPredicateInput | None = None
    if semantic_repository is not None and resolved_inputs is not None:
        layered_refs = collect_layered_predicate_refs(resolved_inputs)
        component_fields = collect_component_fields(resolved_inputs)
        if layered_refs or component_fields:
            node["predicate_filter_lineage"] = build_predicate_filter_lineage(  # type: ignore[typeddict-item]
                layered_refs, component_fields=component_fields
            )
        normalized_predicate_input = build_normalized_predicate_input(
            layered_refs=layered_refs,
            resolver=semantic_repository,
            component_fields=component_fields or None,
        )
    return node, normalized_predicate_input


def _process_node(step: AnalysisStepIR, process: ResolvedSemanticObject) -> ProcessNode:
    interface_contract = dict(process.semantic_object.get("interface_contract") or {})
    node: ProcessNode = {
        "node_id": f"process:{step.index}:{process.ref}",
        "node_type": "process",
        "process_ref": process.ref,
        "process_type": _optional_str(process.semantic_object.get("process_type")) or "",
        "contract_mode": cast(
            "Literal['context_provider', 'entity_stream']",
            _optional_str(interface_contract.get("contract_mode")) or "context_provider",
        ),
        "population_subject_ref": _optional_str(interface_contract.get("population_subject_ref"))
        or "",
    }
    context_kind = _optional_str(interface_contract.get("context_kind"))
    entity_ref = _optional_str(interface_contract.get("entity_ref"))
    emitted_grain_ref = _optional_str(interface_contract.get("emitted_grain_ref"))
    membership_cardinality = _optional_str(interface_contract.get("membership_cardinality"))
    subject_cardinality = _optional_str(interface_contract.get("subject_cardinality"))
    if context_kind is not None:
        node["context_kind"] = context_kind
    if entity_ref is not None:
        node["entity_ref"] = entity_ref
    if emitted_grain_ref is not None:
        node["emitted_grain_ref"] = emitted_grain_ref
    if membership_cardinality in {"exclusive_one", "repeatable_many"}:
        node["membership_cardinality"] = cast(
            "Literal['exclusive_one', 'repeatable_many']", membership_cardinality
        )
    if subject_cardinality in {"one", "many"}:
        node["subject_cardinality"] = cast("Literal['one', 'many']", subject_cardinality)
    return node


def _intent_node(
    *,
    step: AnalysisStepIR,
    normalized_request: NormalizedCompilerRequest,
    output_binding: OutputBinding,
    depends_on: list[str],
) -> IntentNode:
    node: IntentNode = {
        "node_id": f"intent:{step.index}",
        "node_type": "intent",
        "intent_kind": step.step_type,
        "intent_level": "root",
        "depends_on": depends_on,
        "output_bindings": [output_binding],
    }
    if normalized_request.request_dimensions:
        node["requested_dimensions"] = list(normalized_request.request_dimensions)
    if normalized_request.request_result_mode is not None:
        node["requested_result_mode"] = normalized_request.request_result_mode
    return node


def _build_ir_bundle(
    *,
    step: AnalysisStepIR,
    normalized_request: NormalizedCompilerRequest,
    resolved_inputs: ResolvedCompilerInputs,
    validation_result: Any,
    derived_state: Any,
    semantic_context: Mapping[str, Any] | None = None,
) -> tuple[IrBundle, NormalizedPredicateInput | None]:
    plan_id = _stable_plan_id(step, normalized_request)
    artifact_id = f"artifact:{plan_id}:output"
    output_binding: OutputBinding = {
        "artifact_id": artifact_id,
        "artifact_kind": STEP_ARTIFACT_KINDS.get(step.step_type, "table"),
    }

    nodes: list[MeasurementNode | ProcessNode | IntentNode] = []
    depends_on: list[str] = []
    normalized_predicate_input: NormalizedPredicateInput | None = None
    if resolved_inputs.resolved_metric is not None:
        measurement_node, normalized_predicate_input = _measurement_node(
            step=step,
            resolved_metric=resolved_inputs.resolved_metric,
            output_binding=output_binding,
            resolved_inputs=resolved_inputs,
            semantic_repository=semantic_context.get("semantic_repository")
            if semantic_context
            else None,
        )
        nodes.append(measurement_node)
        depends_on.append(measurement_node["node_id"])
    for process in (
        resolved_inputs.resolved_process,
        resolved_inputs.resolved_left_process,
        resolved_inputs.resolved_right_process,
    ):
        if process is None:
            continue
        process_node = _process_node(step, process)
        nodes.append(process_node)
        depends_on.append(process_node["node_id"])
    intent_node = _intent_node(
        step=step,
        normalized_request=normalized_request,
        output_binding=output_binding,
        depends_on=depends_on,
    )
    nodes.append(intent_node)

    lineage = [
        {
            "source_artifact_id": upstream_ref,
            "relationship": "consumes",
        }
        for upstream_ref in normalized_request.upstream_refs
    ]
    artifact: IrArtifact = {
        "artifact_id": artifact_id,
        "artifact_kind": output_binding["artifact_kind"],
        "producer_node_id": intent_node["node_id"],
    }
    if normalized_request.metric_ref is not None:
        artifact["output_semantics_ref"] = normalized_request.metric_ref
    if normalized_request.request_result_mode is not None:
        artifact["result_mode"] = normalized_request.request_result_mode
    if lineage:
        artifact["lineage"] = cast("list[ArtifactLineageEntry]", lineage)

    header: IrPlanHeader = {
        "ir_version": "v1",
        "plan_id": plan_id,
        "plan_kind": "atomic",
        "root_intent_kind": step.step_type,
    }
    if normalized_request.request_result_mode is not None:
        header["result_mode"] = normalized_request.request_result_mode

    lowering_requirements = _build_lowering_requirements(
        step=step,
        normalized_request=normalized_request,
        resolved_inputs=resolved_inputs,
        intent_node_id=intent_node["node_id"],
    )
    validation_trace = _build_validation_trace(validation_result)
    resolved_calendar_alignment = _resolve_calendar_alignment_plan(
        normalized_request,
        semantic_context=semantic_context,
    )
    compile_report: CompileReport = {
        "validation_trace": validation_trace,
        "validation_summary": _build_validation_summary(validation_result, validation_trace),
        "lowering_requirements": lowering_requirements,
    }
    if resolved_calendar_alignment is not None:
        compile_report["resolved_calendar_alignment"] = resolved_calendar_alignment
    profile_usage_trace = _build_profile_usage_trace(derived_state.profile_traces)
    if profile_usage_trace:
        compile_report["profile_usage_trace"] = profile_usage_trace
    if derived_state.usage_trace:
        compile_report["compiler_usage_trace"] = list(derived_state.usage_trace)

    plan: IrPlan = {
        "header": header,
        "inputs": _build_ir_inputs(normalized_request, resolved_inputs),
        "artifacts": [artifact],
        "nodes": nodes,
    }
    return {
        "plan": plan,
        "compile_report": compile_report,
    }, normalized_predicate_input


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def compile_step(
    step: AnalysisStepIR,
    *,
    engine_type: str,
    semantic_context: dict[str, Any] | None = None,
) -> CompiledQuery:
    """Compile a step IR into an engine-agnostic query artifact."""

    semantic_context = semantic_context or {}
    semantic_repository = semantic_context.get("semantic_repository")
    compatibility_profile_reader = semantic_context.get("compatibility_profile_reader")
    if semantic_repository is not None and not isinstance(
        semantic_repository, SemanticRuntimeRepository
    ):
        raise ValueError("semantic_context.semantic_repository must be a SemanticRuntimeRepository")
    normalized_request = normalize_step_request(step, semantic_context=semantic_context)
    resolved_inputs = resolve_compiler_inputs(
        normalized_request,
        semantic_repository=semantic_repository,
    )
    derived_state = derive_compiler_state(
        intent_kind=step.step_type,
        resolved_metric=resolved_inputs.resolved_metric,
        resolved_process=resolved_inputs.resolved_process,
        profile_reader=compatibility_profile_reader,
    )
    validation_result = validate_compiler_inputs(
        step_type=step.step_type,
        resolved_inputs=resolved_inputs,
        derived_state=derived_state,
        semantic_repository=semantic_repository,
    )
    imported_dimension_sources, imported_dimension_issues = (
        _resolve_imported_dimension_physical_sources(
            resolved_inputs,
            semantic_repository=semantic_repository,
        )
    )
    if imported_dimension_issues:
        compatibility_issues = [
            issue for issue in imported_dimension_issues if issue.category == "compatibility"
        ]
        if compatibility_issues and len(compatibility_issues) == len(imported_dimension_issues):
            request_context = {
                "step_type": step.step_type,
                "intent_kind": normalized_request.intent_kind,
                "metric_ref": normalized_request.metric_ref,
                "process_ref": normalized_request.process_ref,
                "dimension_refs": list(normalized_request.request_dimensions),
            }
            request_context = {
                key: value for key, value in request_context.items() if value not in (None, [])
            }
            raise SemanticRequestCompatibilityError(
                {
                    "message": "Request is incompatible with resolved semantic objects",
                    "code": "semantic_request_incompatible",
                    "category": "compatibility",
                    "subject_ref": compatibility_issues[0].subject_ref,
                    "issues": [issue.to_dict() for issue in compatibility_issues],
                    "request_context": request_context,
                }
            )
        compile_error: dict[str, Any] = {
            "error_code": imported_dimension_issues[0].code,
            "failed_gate": imported_dimension_issues[0].gate,
            "message": imported_dimension_issues[0].message,
        }
        if imported_dimension_issues[0].subject_ref is not None:
            compile_error["subject_ref"] = imported_dimension_issues[0].subject_ref
        if imported_dimension_issues[0].details:
            compile_error["details"] = dict(imported_dimension_issues[0].details)
        raise SemanticCompilerError(cast("SemanticCompileError", compile_error))
    if not validation_result.ok:
        compatibility_issues = validation_result.issues_for_category("compatibility")
        non_compatibility_issues = [
            issue for issue in validation_result.error_issues() if issue.category != "compatibility"
        ]
        if compatibility_issues and not non_compatibility_issues:
            raise SemanticRequestCompatibilityError(
                _build_request_compatibility_error(
                    step_type=step.step_type,
                    normalized_request=normalized_request,
                    resolved_inputs=resolved_inputs,
                    validation_result=validation_result,
                )
            )
        raise SemanticCompilerError(
            _build_compile_error(validation_error_message(validation_result), validation_result)
        )
    ir_bundle, normalized_predicate_input = _build_ir_bundle(
        step=step,
        normalized_request=normalized_request,
        resolved_inputs=resolved_inputs,
        validation_result=validation_result,
        derived_state=derived_state,
        semantic_context=semantic_context,
    )
    assert_no_canonical_refs_in_semantic_payload(ir_bundle, surface="compiler_ir_bundle")
    params = dict(step.params)
    metadata: dict[str, Any] = {
        "engine_type": engine_type,
        "step_type": step.step_type,
        "ir_plan_id": ir_bundle["plan"]["header"]["plan_id"],
        "normalized_request_class": normalized_request.request_class,
        "resolved_metric_ref": resolved_inputs.resolved_metric.ref
        if resolved_inputs.resolved_metric is not None
        else None,
        "resolved_metric_revision": resolved_inputs.resolved_metric.revision
        if resolved_inputs.resolved_metric is not None
        else None,
        "resolved_metric_object_id": resolved_inputs.resolved_metric.object_id
        if resolved_inputs.resolved_metric is not None
        else None,
        "resolved_process_ref": resolved_inputs.resolved_process.ref
        if resolved_inputs.resolved_process is not None
        else None,
        "resolved_filter_time_ref": resolved_inputs.resolved_filter_time.ref
        if resolved_inputs.resolved_filter_time is not None
        else None,
        "resolved_dimension_refs": resolved_inputs.resolved_dimension_refs,
        "resolved_relationship_refs": sorted(resolved_inputs.resolved_relationships),
        "resolved_relationship_sources": [
            {
                "relationship_ref": relationship.relationship_ref,
                "left_entity_ref": relationship.left_entity_ref,
                "right_entity_ref": relationship.right_entity_ref,
                "revision": relationship.revision,
                "key_alignment": relationship.key_alignment,
                "time_alignment": relationship.time_alignment,
                "cardinality": relationship.cardinality,
                "grain_compatibility": relationship.grain_compatibility,
                "snapshot_effective_window_alignment": (
                    relationship.snapshot_effective_window_alignment
                ),
            }
            for relationship in resolved_inputs.resolved_relationships.values()
        ],
        "compiler_summary": ir_bundle["compile_report"]["validation_summary"],
        "resolved_calendar_alignment": ir_bundle["compile_report"].get(
            "resolved_calendar_alignment"
        ),
    }
    if normalized_predicate_input is not None:
        metadata["normalized_predicate_input"] = normalized_predicate_input
    assert_no_canonical_refs_in_semantic_payload(metadata, surface="compiler_metadata")
    table_name: str | None = None
    compiled_params: list[Any] = []

    if step.step_type == "sample_rows":
        table_name = _require_param(step, "table_name")
        limit = int(params.get("limit", 10))

        columns = params.get("columns")
        columns_clause = ", ".join(columns) if columns else "*"

        where_parts: list[str] = []
        if params.get("filter"):
            where_parts.append(str(params["filter"]))
        date_column = params.get("date_column")
        date_value = params.get("date_value")
        if date_column and date_value:
            where_parts.append(f"{date_column} = '{date_value}'")
        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""

        return CompiledQuery(
            sql=f"SELECT {columns_clause} FROM {table_name}{where_clause} LIMIT {limit}",
            metadata={**metadata, "table_name": table_name, "limit": limit},
            ir_bundle=ir_bundle,
        )

    if step.step_type == "profile_table_row_count":
        table_name = _require_param(step, "table_name")
        return CompiledQuery(
            sql=f"SELECT COUNT(*) AS row_count FROM {table_name}",
            metadata={**metadata, "table_name": table_name},
            ir_bundle=ir_bundle,
        )

    if step.step_type == "profile_table_columns":
        full_table = _require_param(step, "table_name")
        short_name = str(params.get("short_name") or full_table.split(".")[-1])
        parts = full_table.split(".")
        where_clauses = [f"table_name = '{short_name}'"]
        if len(parts) >= 3:
            where_clauses.append(f"table_catalog = '{parts[0]}'")
            where_clauses.append(f"table_schema = '{parts[1]}'")
        elif len(parts) == 2:
            where_clauses.append(f"table_schema = '{parts[0]}'")
        where_sql = " AND ".join(where_clauses)
        return CompiledQuery(
            sql=f"SELECT column_name FROM information_schema.columns WHERE {where_sql}",
            metadata={**metadata, "short_name": short_name},
            ir_bundle=ir_bundle,
        )

    if step.step_type == "profile_table_column_profile":
        table_name = _require_param(step, "table_name")
        column_name = _require_param(step, "column_name")
        date_column = params.get("date_column")
        date_value = params.get("date_value")
        where_clause = ""
        if date_column and date_value:
            where_clause = f" WHERE {date_column} = '{date_value}'"
        return CompiledQuery(
            sql=f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT({column_name}) AS non_null,
                    COUNT(DISTINCT {column_name}) AS distinct_count
                FROM {table_name}{where_clause}
            """,
            metadata={**metadata, "table_name": table_name, "column_name": column_name},
            ir_bundle=ir_bundle,
        )

    if step.step_type == "metric_query":
        table_name = step.table_name()
        if table_name is None:
            raise ValueError("metric_query requires 'table' or 'table_name' param")
        metric_name = (
            step.primary_metric_name()
            or params.get("metric")
            or _require_param(step, "metric_name")
        )
        metric_sql = semantic_context.get("metric_sql")
        dimensions = semantic_context.get("dimensions")
        if metric_sql is None or dimensions is None:
            raise ValueError(
                "metric_query compilation requires semantic_context with 'metric_sql' and 'dimensions'"
            )
        limit = int(params.get("limit", 10))
        order_param = params.get("order")
        scoped_query = params.get("scoped_query")
        mode = "compare"
        if isinstance(scoped_query, Mapping):
            mode = _require_scoped_query_mode(scoped_query)
        default_order = "CURRENT_VALUE DESC" if mode == "single_window" else "DELTA_PCT ASC"
        order = str(order_param or default_order).upper()
        _normalize_metric_query_order(order, mode=mode)
        sql = build_metric_query(
            metric_name=metric_name,
            table_name=table_name,
            metric_sql=str(metric_sql),
            dimensions=list(dimensions),
            dimension_sql_expressions=_metric_query_dimension_sql_expressions(
                list(dimensions),
                imported_dimension_sources,
            ),
            order=order,
            limit=limit,
            scoped_query=scoped_query if isinstance(scoped_query, Mapping) else None,
        )
        compiled_params = list(semantic_context.get("period_params", []))
        if isinstance(scoped_query, Mapping):
            compiled_params = _build_scoped_query_parts(
                table_name,
                scoped_query,
                include_period=True,
            ).params
        return CompiledQuery(
            sql=sql,
            params=compiled_params,
            metadata={
                **metadata,
                "table_name": table_name,
                "metric_name": metric_name,
                "dimensions": list(dimensions),
            },
            ir_bundle=ir_bundle,
        )

    if step.step_type == "aggregate_query":
        table_name = step.table_name()
        if table_name is None:
            raise ValueError("aggregate_query requires 'table' or 'table_name' param")
        group_by = params.get("group_by", [])
        if not isinstance(group_by, list):
            raise ValueError("aggregate_query requires 'group_by' param (list of columns)")
        limit = int(params.get("limit", 100))
        scoped_query = params.get("scoped_query")
        has_scoped_query = isinstance(scoped_query, Mapping)
        scoped_query_m: Mapping[str, Any] | None = scoped_query if has_scoped_query else None
        order_by = params.get("order_by") or params.get("order")
        typed_measures = params.get("measures")

        if typed_measures is not None:
            sql = build_windowed_aggregate_query(
                table_name=table_name,
                measures=typed_measures,
                group_by=list(group_by),
                order_by=str(order_by) if order_by else None,
                limit=limit,
                scoped_query=scoped_query if has_scoped_query else None,
            )
            compiled_params = []
            compare_period = (
                scoped_query_m is not None and str(scoped_query_m.get("mode") or "") == "compare"
            )
            if scoped_query_m is not None:
                compiled_params = _build_scoped_query_parts(
                    table_name,
                    scoped_query_m,
                    include_period=compare_period,
                ).params
            return CompiledQuery(
                sql=sql,
                params=compiled_params,
                metadata={
                    **metadata,
                    "table_name": table_name,
                    "limit": limit,
                    "compare_period": compare_period,
                },
                ir_bundle=ir_bundle,
            )

        select_exprs = params.get("select")
        if not select_exprs or not isinstance(select_exprs, list):
            raise ValueError("aggregate_query requires 'select' param (list of expressions)")
        where = params.get("where")

        if params.get("compare_period") or (
            scoped_query_m is not None and str(scoped_query_m.get("mode") or "") == "compare"
        ):
            date_column = str(params.get("date_column", "event_date"))
            sql = build_aggregate_comparison_query(
                table_name=table_name,
                select_exprs=list(select_exprs),
                group_by=list(group_by),
                date_column=date_column,
                order_by=order_by,
                limit=limit,
                filter_expr=str(where) if where else None,
                scoped_query=scoped_query_m,
            )
            compiled_params = list(semantic_context.get("period_params", []))
            if scoped_query_m is not None:
                compiled_params = _build_scoped_query_parts(
                    table_name,
                    scoped_query_m,
                    include_period=True,
                ).params
            return CompiledQuery(
                sql=sql,
                params=compiled_params,
                metadata={
                    **metadata,
                    "table_name": table_name,
                    "limit": limit,
                    "compare_period": True,
                },
                ir_bundle=ir_bundle,
            )

        select_clause = ", ".join(select_exprs)
        expanded_group_by = _expand_group_by_aliases(list(select_exprs), list(group_by))
        group_clause = f" GROUP BY {', '.join(expanded_group_by)}" if expanded_group_by else ""
        order_clause = f" ORDER BY {order_by}" if order_by else ""
        compiled_params = []

        if scoped_query_m is not None:
            scoped = _build_scoped_query_parts(
                table_name,
                scoped_query_m,
                include_period=False,
            )
            sql = f"WITH {scoped.cte_sql} SELECT {select_clause} FROM scoped{group_clause}{order_clause} LIMIT {limit}"
            compiled_params = scoped.params
        else:
            where_clause = f" WHERE {where}" if where else ""
            sql = f"SELECT {select_clause} FROM {table_name}{where_clause}{group_clause}{order_clause} LIMIT {limit}"
        return CompiledQuery(
            sql=sql,
            params=compiled_params,
            metadata={**metadata, "table_name": table_name, "limit": limit},
            ir_bundle=ir_bundle,
        )

    raise ValueError(f"Unsupported compilation step type: {step.step_type}")


def _require_param(step: AnalysisStepIR, name: str) -> str:
    value = step.params.get(name)
    if value in (None, ""):
        raise ValueError(f"{step.step_type} requires '{name}' param")
    return str(value)
