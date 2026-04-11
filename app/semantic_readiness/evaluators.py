"""Placeholder readiness evaluators for Phase A.

These evaluators preserve the simple status-to-readiness mapping used
in Phase A: published → active + ready, draft → draft + not_ready,
deprecated → deprecated + not_ready.

Phase B will replace these with object-specific evaluators that compute
blocking_requirements and capabilities based on dependencies, bindings,
and physical grounding requirements.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .binding_utils import binding_contract_target_exists
from .context import ReadinessEvaluationContext
from .types import (
    BlockingRequirementPayload,
    ReadinessResult,
    ReadinessTraceItem,
    derive_lifecycle_status,
    derive_readiness_status,
)


class PlaceholderSemanticReadinessEvaluator:
    """Placeholder evaluator that preserves Phase A readiness semantics.

    This evaluator is used for all object kinds in Phase A. It simply
    derives lifecycle_status and readiness_status from the storage status,
    with empty blocking_requirements and capabilities.

    The trace entry identifies the placeholder source for debugging and
    helps distinguish Phase A behavior from Phase B object-specific rules.
    """

    def __init__(self, object_kind: str) -> None:
        """Initialize placeholder evaluator for a specific object kind.

        Args:
            object_kind: The semantic object type this evaluator handles.
        """
        self.object_kind = object_kind

    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
        """Evaluate readiness using Phase A simple mapping.

        Returns lifecycle_status and readiness_status derived from storage
        status, with empty blockers/capabilities and a trace entry.
        """
        snapshot = context.snapshot
        return ReadinessResult(
            lifecycle_status=derive_lifecycle_status(snapshot.status),
            readiness_status=derive_readiness_status(snapshot.status),
            capabilities={},
            blocking_requirements=[],
            trace=[
                ReadinessTraceItem(
                    stage="compat_placeholder",
                    detail=(
                        "Placeholder evaluator preserves Phase A readiness semantics until "
                        "object-specific rules are implemented."
                    ),
                    source=f"{self.object_kind}_placeholder_evaluator",
                    subject_ref=snapshot.ref,
                )
            ],
            had_ready_predecessor=context.previously_ready(),
        )


class EntityReadinessEvaluator:
    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
        snapshot = context.snapshot
        lifecycle_status = derive_lifecycle_status(snapshot.status)
        blockers: list[BlockingRequirementPayload] = []
        trace = [
            ReadinessTraceItem(
                stage="lifecycle_gate",
                detail=f"Derived lifecycle_status={lifecycle_status} from storage status={snapshot.status}.",
                source="entity_readiness_evaluator",
                subject_ref=snapshot.ref,
            )
        ]
        interface_contract = dict(snapshot.semantic_object.get("interface_contract") or {})
        identity = dict(interface_contract.get("identity") or {})
        key_refs = [str(item) for item in identity.get("key_refs") or [] if str(item).strip()]
        if not key_refs or not identity.get("uniqueness_scope") or not identity.get("id_stability"):
            blockers.append(
                _blocker(
                    code="ENTITY_CONTRACT_INVALID",
                    message="Entity identity contract must define key_refs, uniqueness_scope, and id_stability.",
                    subject_ref=snapshot.ref,
                )
            )
            trace.append(
                ReadinessTraceItem(
                    stage="contract_validation",
                    detail="Entity identity contract is incomplete.",
                    source="entity_readiness_evaluator",
                    subject_ref=snapshot.ref,
                )
            )
        if lifecycle_status != "active":
            readiness_status = "not_ready"
        else:
            if context.require_physical_grounding:
                grounding_blockers, grounding_trace, _grounded = _evaluate_subject_bindings(
                    context=context,
                    expected_scope="entity",
                    required_targets=[
                        *[("identity_key", key_ref, key_ref) for key_ref in key_refs],
                        *_optional_required_target(
                            "primary_time",
                            interface_contract.get("primary_time_ref"),
                        ),
                        *[
                            (
                                "stable_descriptor",
                                descriptor["dimension_ref"],
                                descriptor["dimension_ref"],
                            )
                            for descriptor in interface_contract.get("stable_descriptors") or []
                            if isinstance(descriptor, dict) and descriptor.get("dimension_ref")
                        ],
                    ],
                    missing_binding_code="ENTITY_GROUNDING_MISSING",
                    coverage_code="ENTITY_BINDING_COVERAGE_MISSING",
                )
                blockers.extend(grounding_blockers)
                trace.extend(grounding_trace)
            readiness_status = "ready" if not blockers else "not_ready"
        return ReadinessResult(
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            capabilities={},
            blocking_requirements=blockers,
            trace=trace,
            had_ready_predecessor=context.previously_ready(),
        )


class MetricReadinessEvaluator:
    _REQUIRED_HEADER_FIELDS = ("metric_ref", "metric_family", "observed_entity_ref")

    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
        snapshot = context.snapshot
        lifecycle_status = derive_lifecycle_status(snapshot.status)
        header = dict(snapshot.semantic_object.get("header") or {})
        payload = dict(snapshot.semantic_object.get("payload") or {})
        blockers: list[BlockingRequirementPayload] = []
        trace = [
            ReadinessTraceItem(
                stage="lifecycle_gate",
                detail=f"Derived lifecycle_status={lifecycle_status} from storage status={snapshot.status}.",
                source="metric_readiness_evaluator",
                subject_ref=snapshot.ref,
            )
        ]
        capabilities = _metric_capabilities(header)

        missing_fields = [field for field in self._REQUIRED_HEADER_FIELDS if not header.get(field)]
        if missing_fields:
            blockers.append(
                _blocker(
                    code="METRIC_CONTRACT_INVALID",
                    message=f"Metric contract is missing required header fields: {', '.join(missing_fields)}.",
                    subject_ref=snapshot.ref,
                )
            )
            trace.append(
                ReadinessTraceItem(
                    stage="contract_validation",
                    detail=f"Metric contract missing required fields: {', '.join(missing_fields)}.",
                    source="metric_readiness_evaluator",
                    subject_ref=snapshot.ref,
                )
            )

        if lifecycle_status == "active":
            for dependency_ref in _metric_dependency_refs(header, payload):
                dependency = context.load_dependency_snapshot(dependency_ref)
                if dependency is None or derive_lifecycle_status(dependency.status) != "active":
                    blockers.append(
                        _blocker(
                            code="METRIC_DEPENDENCY_INACTIVE",
                            message="Metric dependency must exist and be active before the metric is ready.",
                            subject_ref=snapshot.ref,
                            dependency_ref=dependency_ref,
                        )
                    )
                    trace.append(
                        ReadinessTraceItem(
                            stage="dependency_check",
                            detail=f"Dependency {dependency_ref} is missing or not active.",
                            source="metric_readiness_evaluator",
                            subject_ref=snapshot.ref,
                            dependency_ref=dependency_ref,
                        )
                    )
            grounding_blockers, grounding_trace, _grounded = _evaluate_subject_bindings(
                context=context,
                expected_scope="metric",
                required_targets=[
                    *[
                        ("metric_input", target_key, None)
                        for target_key in _required_metric_inputs(header, payload)
                    ],
                    *_optional_required_target(
                        "primary_time",
                        header.get("primary_time_ref"),
                    ),
                    *_optional_required_target(
                        "population_subject",
                        header.get("population_subject_ref"),
                    ),
                ],
                missing_binding_code="METRIC_BINDING_MISSING",
                coverage_code_map={
                    "metric_input": "METRIC_INPUT_COVERAGE_MISSING",
                    "primary_time": "METRIC_REQUIREMENT_COVERAGE_MISSING",
                    "population_subject": "METRIC_REQUIREMENT_COVERAGE_MISSING",
                },
            )
            blockers.extend(grounding_blockers)
            trace.extend(grounding_trace)

        readiness_status = "ready" if lifecycle_status == "active" and not blockers else "not_ready"
        return ReadinessResult(
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            capabilities=capabilities,
            blocking_requirements=blockers,
            trace=trace,
            had_ready_predecessor=context.previously_ready(),
        )


class ProcessReadinessEvaluator:
    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
        snapshot = context.snapshot
        lifecycle_status = derive_lifecycle_status(snapshot.status)
        header = dict(snapshot.semantic_object.get("header") or {})
        interface_contract = dict(snapshot.semantic_object.get("interface_contract") or {})
        blockers: list[BlockingRequirementPayload] = []
        trace = [
            ReadinessTraceItem(
                stage="lifecycle_gate",
                detail=f"Derived lifecycle_status={lifecycle_status} from storage status={snapshot.status}.",
                source="process_readiness_evaluator",
                subject_ref=snapshot.ref,
            )
        ]
        capabilities = _process_capabilities(interface_contract)

        if not header.get("process_ref") or not header.get("process_type"):
            blockers.append(
                _blocker(
                    code="PROCESS_CONTRACT_INVALID",
                    message="Process contract must define process_ref and process_type.",
                    subject_ref=snapshot.ref,
                )
            )
            trace.append(
                ReadinessTraceItem(
                    stage="contract_validation",
                    detail="Process contract is missing required header fields.",
                    source="process_readiness_evaluator",
                    subject_ref=snapshot.ref,
                )
            )

        if lifecycle_status == "active" and context.require_physical_grounding:
            grounding_blockers, grounding_trace, _grounded = _evaluate_subject_bindings(
                context=context,
                expected_scope="process_object",
                required_targets=[
                    *_optional_required_target(
                        "population_subject",
                        interface_contract.get("population_subject_ref"),
                    ),
                    *_optional_required_target(
                        "analysis_window_anchor",
                        interface_contract.get("anchor_time_ref"),
                    ),
                ],
                missing_binding_code="PROCESS_BINDING_MISSING",
                coverage_code="PROCESS_BINDING_COVERAGE_MISSING",
            )
            blockers.extend(grounding_blockers)
            trace.extend(grounding_trace)

        profiles = context.load_profiles("process", snapshot.ref)
        capability_profile = next(
            (
                profile
                for profile in profiles
                if str(profile.get("profile_kind") or "") == "capability"
            ),
            None,
        )
        if capability_profile is None:
            capabilities["inferential_ready"] = False
            blockers.append(
                _blocker(
                    code="PROCESS_PROFILE_MISSING",
                    message="Process inferential capability profile is missing.",
                    subject_ref=snapshot.ref,
                )
            )
            trace.append(
                ReadinessTraceItem(
                    stage="profile_check",
                    detail="No published capability profile is available for this process.",
                    source="process_readiness_evaluator",
                    subject_ref=snapshot.ref,
                )
            )
        else:
            subject_revision = capability_profile.get("subject_revision")
            if subject_revision is None or int(subject_revision) != snapshot.revision:
                capabilities["inferential_ready"] = False
                blockers.append(
                    _blocker(
                        code="PROCESS_PROFILE_MISMATCH",
                        message="Process capability profile revision does not match the active process revision.",
                        subject_ref=snapshot.ref,
                        dependency_ref=str(capability_profile.get("profile_ref") or ""),
                    )
                )
                trace.append(
                    ReadinessTraceItem(
                        stage="profile_check",
                        detail="Capability profile revision does not match the process revision.",
                        source="process_readiness_evaluator",
                        subject_ref=snapshot.ref,
                        dependency_ref=str(capability_profile.get("profile_ref") or ""),
                    )
                )
            else:
                capability_payload = dict(capability_profile.get("capability") or {})
                capabilities["inferential_ready"] = bool(
                    capability_payload.get("inferential_ready")
                )
                trace.append(
                    ReadinessTraceItem(
                        stage="profile_check",
                        detail="Capability profile matches the active process revision.",
                        source="process_readiness_evaluator",
                        subject_ref=snapshot.ref,
                        dependency_ref=str(capability_profile.get("profile_ref") or ""),
                    )
                )

        readiness_status = (
            "ready"
            if lifecycle_status == "active" and not _contains_basic_process_blockers(blockers)
            else "not_ready"
        )
        return ReadinessResult(
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            capabilities=capabilities,
            blocking_requirements=blockers,
            trace=trace,
            had_ready_predecessor=context.previously_ready(),
        )


def _contains_basic_process_blockers(blockers: Iterable[BlockingRequirementPayload]) -> bool:
    """Check if blockers include any "basic" readiness blockers.

    Process objects have a two-tier blocker system:

    **Basic blockers** (affect readiness_status):
    - PROCESS_CONTRACT_INVALID: Missing required header fields
    - PROCESS_BINDING_MISSING: No binding when grounding required
    - PROCESS_BINDING_COVERAGE_MISSING: Binding missing required targets

    **Inferential-only blockers** (do NOT affect readiness_status):
    - PROCESS_PROFILE_MISSING: No capability profile published
    - PROCESS_PROFILE_MISMATCH: Profile revision doesn't match process

    Inferential-only blockers are recorded in blocking_requirements for visibility
    but don't prevent the process from being "ready" for non-inferential use cases.
    The `inferential_ready` capability flag indicates whether the process can
    support inferential analysis (requires matching capability profile).

    Args:
        blockers: Iterable of blocking requirements from process evaluation.

    Returns:
        True if any blocker is a basic (non-inferential) blocker,
        False if blockers are only inferential or empty.
    """
    inferential_only_codes = {"PROCESS_PROFILE_MISSING", "PROCESS_PROFILE_MISMATCH"}
    return any(blocker.code not in inferential_only_codes for blocker in blockers)


def _metric_capabilities(header: dict[str, Any]) -> dict[str, Any]:
    primary_time_ref = _optional_str(header.get("primary_time_ref"))
    additivity = _optional_str(header.get("additivity"))
    sample_kind = _optional_str(header.get("sample_kind"))
    return {
        "supports_observe": True,
        "supports_attribute": bool(additivity and primary_time_ref),
        "supports_diagnose": sample_kind in {"numeric", "rate", "binary"},
        "supports_detect": bool(primary_time_ref),
        "supports_validate": sample_kind == "rate",
        "supports_decompose": additivity in {"additive", "semi_additive"},
    }


def _process_capabilities(interface_contract: dict[str, Any]) -> dict[str, Any]:
    anchor_time_ref = _optional_str(interface_contract.get("anchor_time_ref"))
    context_kind = _optional_str(interface_contract.get("context_kind"))
    return {
        "supports_time_projection": bool(anchor_time_ref),
        "supports_experiment_inference": context_kind == "experiment_split",
        "supports_cohort_inference": context_kind == "cohort_membership",
        "inferential_ready": False,
    }


def _metric_dependency_refs(header: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    refs = {
        ref
        for ref in _collect_semantic_refs([header, payload])
        if ref.startswith(("entity.", "time.", "dimension.", "process."))
    }
    return sorted(refs)


def _collect_semantic_refs(values: list[Any]) -> set[str]:
    refs: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            refs.update(_collect_semantic_refs(list(value.values())))
        elif isinstance(value, list):
            refs.update(_collect_semantic_refs(value))
        elif isinstance(value, str) and value.startswith(
            ("entity.", "time.", "dimension.", "process.", "metric.")
        ):
            refs.add(value)
    return refs


def _required_metric_inputs(header: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    metric_family = str(header.get("metric_family") or payload.get("metric_family") or "").strip()
    payload_key_map: dict[str, list[str]] = {
        "count_metric": ["count_target"],
        "sum_metric": ["measure"],
        "rate_metric": ["numerator", "denominator"],
        "average_metric": ["numerator", "denominator"],
        "distribution_metric": ["value_component"],
        "score_metric": ["score_source"],
        "survival_metric": [],
    }
    return payload_key_map.get(metric_family, [])


def _optional_required_target(
    target_kind: str,
    semantic_ref: Any,
) -> list[tuple[str, str, str | None]]:
    ref = _optional_str(semantic_ref)
    if ref is None:
        return []
    return [(target_kind, ref, ref)]


def _evaluate_subject_bindings(
    *,
    context: ReadinessEvaluationContext,
    expected_scope: str,
    required_targets: list[tuple[str, str, str | None]],
    missing_binding_code: str,
    coverage_code: str | None = None,
    coverage_code_map: dict[str, str] | None = None,
) -> tuple[list[BlockingRequirementPayload], list[ReadinessTraceItem], bool]:
    snapshot = context.snapshot
    blockers: list[BlockingRequirementPayload] = []
    trace: list[ReadinessTraceItem] = []
    bindings = [
        binding
        for binding in context.load_subject_bindings(snapshot.ref)
        if str(binding.get("binding_scope") or "") == expected_scope
        and str(binding.get("bound_object_ref") or "") == snapshot.ref
        and derive_lifecycle_status(str(binding.get("status") or "draft")) == "active"
    ]
    if not bindings:
        blockers.append(
            _blocker(
                code=missing_binding_code,
                message=f"{snapshot.ref} requires at least one active {expected_scope} binding.",
                subject_ref=snapshot.ref,
            )
        )
        trace.append(
            ReadinessTraceItem(
                stage="binding_check",
                detail=f"No active {expected_scope} bindings are attached to {snapshot.ref}.",
                source=f"{snapshot.object_kind}_readiness_evaluator",
                subject_ref=snapshot.ref,
            )
        )
        return blockers, trace, False

    ready_binding_found = False
    for binding in bindings:
        binding_ref = str(binding.get("binding_ref") or "")
        binding_trace, binding_blockers = _check_binding_readiness(
            context=context,
            binding=binding,
            subject_ref=snapshot.ref,
            object_kind=snapshot.object_kind,
            required_targets=required_targets,
            coverage_code=coverage_code,
            coverage_code_map=coverage_code_map,
        )
        trace.extend(binding_trace)
        if not binding_blockers:
            ready_binding_found = True
            break
        blockers.extend(binding_blockers)
        trace.append(
            ReadinessTraceItem(
                stage="binding_check",
                detail=f"Binding {binding_ref} does not satisfy readiness coverage requirements.",
                source=f"{snapshot.object_kind}_readiness_evaluator",
                subject_ref=snapshot.ref,
                dependency_ref=binding_ref,
            )
        )
    return _dedupe_blockers(blockers), trace, ready_binding_found


def _check_binding_readiness(
    *,
    context: ReadinessEvaluationContext,
    binding: dict[str, Any],
    subject_ref: str,
    object_kind: str,
    required_targets: list[tuple[str, str, str | None]],
    coverage_code: str | None,
    coverage_code_map: dict[str, str] | None,
) -> tuple[list[ReadinessTraceItem], list[BlockingRequirementPayload]]:
    binding_ref = str(binding.get("binding_ref") or "")
    interface_contract = dict(binding.get("interface_contract") or {})
    trace: list[ReadinessTraceItem] = []
    blockers: list[BlockingRequirementPayload] = []

    for binding_import in interface_contract.get("imports") or context.load_binding_imports(
        binding_ref
    ):
        imported_binding_ref = str(
            binding_import.get("imported_binding_ref") or binding_import.get("binding_ref") or ""
        )
        imported = context.load_dependency_snapshot(imported_binding_ref)
        if imported is None or derive_lifecycle_status(imported.status) != "active":
            blockers.append(
                _blocker(
                    code=f"{object_kind.upper()}_BINDING_IMPORT_MISSING",
                    message="Required imported binding must exist and be active.",
                    subject_ref=subject_ref,
                    dependency_ref=imported_binding_ref,
                )
            )
    carriers = list(interface_contract.get("carrier_bindings") or [])
    if not carriers:
        blockers.append(
            _blocker(
                code=f"{object_kind.upper()}_CARRIER_SOURCE_MISSING",
                message="Binding must expose at least one carrier binding with a synced source object.",
                subject_ref=subject_ref,
                dependency_ref=binding_ref,
            )
        )
    for carrier in carriers:
        if context.load_carrier_source_object(carrier) is None:
            blockers.append(
                _blocker(
                    code=f"{object_kind.upper()}_CARRIER_SOURCE_MISSING",
                    message="Binding carrier must resolve to a synced source object.",
                    subject_ref=subject_ref,
                    dependency_ref=binding_ref,
                )
            )
    field_bindings = list(interface_contract.get("field_bindings") or [])
    for target_kind, target_key, semantic_ref in required_targets:
        if binding_contract_target_exists(
            field_bindings,
            target_kind=target_kind,
            target_key=target_key,
            semantic_ref=semantic_ref,
        ):
            continue
        blockers.append(
            _blocker(
                code=(coverage_code_map or {}).get(target_kind)
                or coverage_code
                or "BINDING_COVERAGE_MISSING",
                message=f"Binding is missing required {target_kind} coverage for {target_key}.",
                subject_ref=subject_ref,
                dependency_ref=binding_ref,
            )
        )
    trace.append(
        ReadinessTraceItem(
            stage="binding_check",
            detail=f"Checked binding {binding_ref} against {len(required_targets)} required targets.",
            source="binding_readiness_helper",
            subject_ref=subject_ref,
            dependency_ref=binding_ref,
        )
    )
    return trace, blockers


def _blocker(
    *,
    code: str,
    message: str,
    subject_ref: str,
    dependency_ref: str | None = None,
) -> BlockingRequirementPayload:
    return BlockingRequirementPayload(
        code=code,
        message=message,
        subject_ref=subject_ref,
        dependency_ref=dependency_ref,
    )


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _dedupe_blockers(
    blockers: list[BlockingRequirementPayload],
) -> list[BlockingRequirementPayload]:
    seen: set[tuple[str, str | None, str | None]] = set()
    deduped: list[BlockingRequirementPayload] = []
    for blocker in blockers:
        key = (blocker.code, blocker.subject_ref, blocker.dependency_ref)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(blocker)
    return deduped
