"""Semantic readiness evaluators."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from app.analysis_core.additivity_capabilities import derive_additivity_capabilities
from app.metric_inputs import required_metric_input_slots
from app.time_contracts import normalize_timestamp_format

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
    """Placeholder evaluator that preserves simple status-derived readiness."""

    def __init__(self, object_kind: str) -> None:
        self.object_kind = object_kind

    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
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
            had_active_bindings = False
            if context.require_physical_grounding:
                grounding_blockers, grounding_trace, _grounded, had_active_bindings = (
                    _evaluate_subject_bindings(
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
                )
                blockers.extend(grounding_blockers)
                trace.extend(grounding_trace)
            readiness_status = _classify_active_readiness(
                blockers,
                stale_codes={"ENTITY_BINDING_IMPORT_MISSING", "ENTITY_CARRIER_SOURCE_MISSING"},
                allow_stale=had_active_bindings,
            )
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
            had_active_bindings = False
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
            grounding_blockers, grounding_trace, _grounded, had_active_bindings = (
                _evaluate_subject_bindings(
                    context=context,
                    expected_scope="metric",
                    required_targets=[
                        *[
                            ("metric_input", target_key, None)
                            for target_key in _required_metric_inputs(header, payload)
                        ],
                        *_optional_required_target("primary_time", header.get("primary_time_ref")),
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
            )
            blockers.extend(grounding_blockers)
            trace.extend(grounding_trace)
        readiness_status = (
            _classify_active_readiness(
                blockers,
                stale_codes={"METRIC_BINDING_IMPORT_MISSING", "METRIC_CARRIER_SOURCE_MISSING"},
                allow_stale=had_active_bindings,
            )
            if lifecycle_status == "active"
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
            grounding_blockers, grounding_trace, _grounded, had_active_bindings = (
                _evaluate_subject_bindings(
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
            )
            blockers.extend(grounding_blockers)
            trace.extend(grounding_trace)
        else:
            had_active_bindings = False

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

        if lifecycle_status != "active":
            readiness_status = "not_ready"
        elif not blockers:
            readiness_status = "ready"
        elif _all_blockers_in(blockers, {"PROCESS_PROFILE_MISMATCH"}) or (
            _all_blockers_in(
                blockers,
                {"PROCESS_BINDING_IMPORT_MISSING", "PROCESS_CARRIER_SOURCE_MISSING"},
            )
            and had_active_bindings
        ):
            readiness_status = "stale"
        elif not _contains_basic_process_blockers(blockers):
            readiness_status = "ready"
        else:
            readiness_status = "not_ready"
        return ReadinessResult(
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            capabilities=capabilities,
            blocking_requirements=blockers,
            trace=trace,
            had_ready_predecessor=context.previously_ready(),
        )


class DimensionReadinessEvaluator:
    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
        snapshot = context.snapshot
        lifecycle_status = derive_lifecycle_status(snapshot.status)
        header = dict(snapshot.semantic_object.get("header") or {})
        interface_contract = dict(snapshot.semantic_object.get("interface_contract") or {})
        value_domain = dict(interface_contract.get("value_domain") or {})
        time_requirement = dict(interface_contract.get("time_derived_requirement") or {})
        blockers: list[BlockingRequirementPayload] = []
        capabilities: dict[str, Any] = {
            "supports_grouping": bool(
                dict(interface_contract.get("grouping") or {}).get("supports_grouping")
            ),
            "requires_time_anchor": bool(time_requirement.get("required_time_anchor_ref")),
        }
        required_time_anchor_ref = _optional_str(time_requirement.get("required_time_anchor_ref"))
        if required_time_anchor_ref is not None:
            capabilities["required_time_anchor_ref"] = required_time_anchor_ref
        required_fields = ("structure_kind", "semantic_role", "value_type", "domain_kind")
        if not header.get("dimension_ref") or any(
            not value_domain.get(field) for field in required_fields
        ):
            blockers.append(
                _blocker(
                    code="DIMENSION_CONTRACT_INVALID",
                    message=(
                        "Dimension contract must define dimension_ref and value_domain fields "
                        "structure_kind, semantic_role, value_type, and domain_kind."
                    ),
                    subject_ref=snapshot.ref,
                )
            )
        if (
            value_domain.get("structure_kind") == "time_derived"
            and required_time_anchor_ref is None
        ):
            blockers.append(
                _blocker(
                    code="DIMENSION_TIME_DERIVED_REQUIREMENT_MISSING",
                    message=(
                        "Time-derived dimensions must define "
                        "time_derived_requirement.required_time_anchor_ref."
                    ),
                    subject_ref=snapshot.ref,
                )
            )
        if not capabilities["supports_grouping"]:
            blockers.append(
                _blocker(
                    code="DIMENSION_GROUPING_UNSUPPORTED",
                    message="Dimension does not support grouping.",
                    subject_ref=snapshot.ref,
                )
            )
        readiness_status = "ready" if lifecycle_status == "active" and not blockers else "not_ready"
        return ReadinessResult(
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            capabilities=capabilities,
            blocking_requirements=blockers,
            trace=[
                ReadinessTraceItem(
                    stage="lifecycle_gate",
                    detail=f"Derived lifecycle_status={lifecycle_status} from storage status={snapshot.status}.",
                    source="dimension_readiness_evaluator",
                    subject_ref=snapshot.ref,
                )
            ],
            had_ready_predecessor=context.previously_ready(),
        )


class TimeReadinessEvaluator:
    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
        snapshot = context.snapshot
        lifecycle_status = derive_lifecycle_status(snapshot.status)
        header = dict(snapshot.semantic_object.get("header") or {})
        semantic_roles = [
            str(role) for role in header.get("semantic_roles") or [] if str(role).strip()
        ]
        blockers: list[BlockingRequirementPayload] = []
        capabilities = {
            "semantic_roles": semantic_roles,
            "supports_business_anchor": "business_anchor" in semantic_roles,
            "supports_measurement": "measurement" in semantic_roles,
            "supports_operational_support": "operational_support" in semantic_roles,
        }
        if not header.get("time_ref") or not semantic_roles:
            blockers.append(
                _blocker(
                    code="TIME_CONTRACT_INVALID",
                    message="Time semantic must define time_ref and at least one semantic role.",
                    subject_ref=snapshot.ref,
                )
            )
        readiness_status = "ready" if lifecycle_status == "active" and not blockers else "not_ready"
        return ReadinessResult(
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            capabilities=capabilities,
            blocking_requirements=blockers,
            trace=[
                ReadinessTraceItem(
                    stage="lifecycle_gate",
                    detail=f"Derived lifecycle_status={lifecycle_status} from storage status={snapshot.status}.",
                    source="time_readiness_evaluator",
                    subject_ref=snapshot.ref,
                )
            ],
            had_ready_predecessor=context.previously_ready(),
        )


class EnumReadinessEvaluator:
    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
        snapshot = context.snapshot
        lifecycle_status = derive_lifecycle_status(snapshot.status)
        header = dict(snapshot.semantic_object.get("header") or {})
        versions = list(snapshot.semantic_object.get("versions") or [])
        blockers: list[BlockingRequirementPayload] = []
        capabilities = {
            "value_type": header.get("value_type"),
            "version_count": len(versions),
            "has_governed_values": True,
        }
        has_values = all(list(dict(version).get("values") or []) for version in versions)
        if (
            not header.get("enum_set_ref")
            or not header.get("value_type")
            or not versions
            or not has_values
        ):
            blockers.append(
                _blocker(
                    code="ENUM_CONTRACT_INVALID",
                    message="Enum set must define enum_set_ref, value_type, and at least one populated version.",
                    subject_ref=snapshot.ref,
                )
            )
        readiness_status = "ready" if lifecycle_status == "active" and not blockers else "not_ready"
        return ReadinessResult(
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            capabilities=capabilities,
            blocking_requirements=blockers,
            trace=[
                ReadinessTraceItem(
                    stage="lifecycle_gate",
                    detail=f"Derived lifecycle_status={lifecycle_status} from storage status={snapshot.status}.",
                    source="enum_readiness_evaluator",
                    subject_ref=snapshot.ref,
                )
            ],
            had_ready_predecessor=context.previously_ready(),
        )


class BindingReadinessEvaluator:
    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
        snapshot = context.snapshot
        lifecycle_status = derive_lifecycle_status(snapshot.status)
        header = dict(snapshot.semantic_object.get("header") or {})
        interface_contract = dict(snapshot.semantic_object.get("interface_contract") or {})
        binding_scope = _optional_str(header.get("binding_scope"))
        bound_object_ref = _optional_str(header.get("bound_object_ref"))
        blockers: list[BlockingRequirementPayload] = []
        carrier_bindings = list(interface_contract.get("carrier_bindings") or [])
        field_bindings = list(interface_contract.get("field_bindings") or [])
        capabilities = {
            "binding_scope": binding_scope,
            "has_imports": bool(interface_contract.get("imports")),
            "carrier_count": len(carrier_bindings),
            "field_binding_count": len(field_bindings),
            "resolves_synced_source": bool(carrier_bindings),
            "covers_required_targets": False,
        }

        if not header.get("binding_ref") or binding_scope is None or bound_object_ref is None:
            blockers.append(
                _blocker(
                    code="BINDING_SCOPE_UNSUPPORTED",
                    message="Binding must define binding_ref, binding_scope, and bound_object_ref.",
                    subject_ref=snapshot.ref,
                )
            )

        if lifecycle_status == "active":
            if bound_object_ref is not None:
                subject = context.load_dependency_snapshot(bound_object_ref)
                if subject is None or derive_lifecycle_status(subject.status) != "active":
                    blockers.append(
                        _blocker(
                            code="BINDING_SUBJECT_INACTIVE",
                            message="Binding subject must exist and be active.",
                            subject_ref=snapshot.ref,
                            dependency_ref=bound_object_ref,
                        )
                    )
            blockers.extend(
                _binding_import_blockers(
                    context=context,
                    binding=interface_contract,
                    subject_ref=snapshot.ref,
                    blocker_code="BINDING_IMPORT_INACTIVE",
                )
            )
            if not carrier_bindings:
                blockers.append(
                    _blocker(
                        code="BINDING_CARRIER_MISSING",
                        message="Binding must expose at least one carrier binding.",
                        subject_ref=snapshot.ref,
                    )
                )
            carrier_keys = {str(carrier.get("binding_key") or "") for carrier in carrier_bindings}
            carriers_resolve = True
            for carrier in carrier_bindings:
                resolved_carrier = context.load_carrier_source_object(carrier)
                carriers_resolve = carriers_resolve and resolved_carrier is not None
                if resolved_carrier is None:
                    blockers.append(
                        _blocker(
                            code="BINDING_CARRIER_SOURCE_MISSING",
                            message="Binding carrier must resolve to a synced source object.",
                            subject_ref=snapshot.ref,
                            dependency_ref=str(carrier.get("binding_key") or snapshot.ref),
                        )
                    )
            blockers.extend(
                _binding_time_binding_blockers(
                    subject_ref=snapshot.ref,
                    carrier_bindings=carrier_bindings,
                    time_bindings=list(interface_contract.get("time_bindings") or []),
                    context=context,
                )
            )
            capabilities["resolves_synced_source"] = carriers_resolve
            if not field_bindings:
                blockers.append(
                    _blocker(
                        code="BINDING_FIELD_MAPPING_MISSING",
                        message="Binding must define at least one field binding.",
                        subject_ref=snapshot.ref,
                    )
                )
            for field_binding in field_bindings:
                if str(field_binding.get("carrier_binding_key") or "") not in carrier_keys:
                    blockers.append(
                        _blocker(
                            code="BINDING_FIELD_MAPPING_MISSING",
                            message="Field binding must reference a declared carrier_binding_key.",
                            subject_ref=snapshot.ref,
                            dependency_ref=str(field_binding.get("carrier_binding_key") or ""),
                        )
                    )
            required_targets = _required_binding_targets(context, binding_scope, bound_object_ref)
            if required_targets is None:
                blockers.append(
                    _blocker(
                        code="BINDING_SCOPE_UNSUPPORTED",
                        message=f"Binding scope {binding_scope!r} is not supported for readiness evaluation.",
                        subject_ref=snapshot.ref,
                    )
                )
            else:
                coverage_blockers = _binding_target_coverage_blockers(
                    bindings=field_bindings + list(interface_contract.get("time_bindings") or []),
                    required_targets=required_targets,
                    subject_ref=snapshot.ref,
                )
                blockers.extend(coverage_blockers)
                capabilities["covers_required_targets"] = not coverage_blockers

        readiness_status = (
            _classify_active_readiness(
                blockers,
                stale_codes={
                    "BINDING_SUBJECT_INACTIVE",
                    "BINDING_IMPORT_INACTIVE",
                    "BINDING_CARRIER_SOURCE_MISSING",
                },
                allow_stale=True,
            )
            if lifecycle_status == "active"
            else "not_ready"
        )
        return ReadinessResult(
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            capabilities=capabilities,
            blocking_requirements=_dedupe_blockers(blockers),
            trace=[
                ReadinessTraceItem(
                    stage="lifecycle_gate",
                    detail=f"Derived lifecycle_status={lifecycle_status} from storage status={snapshot.status}.",
                    source="binding_readiness_evaluator",
                    subject_ref=snapshot.ref,
                )
            ],
            had_ready_predecessor=context.previously_ready(),
        )


class CompilerProfileReadinessEvaluator:
    def evaluate(self, context: ReadinessEvaluationContext) -> ReadinessResult:
        snapshot = context.snapshot
        lifecycle_status = derive_lifecycle_status(snapshot.status)
        semantic_object = snapshot.semantic_object
        profile_kind = _optional_str(semantic_object.get("profile_kind"))
        subject_kind = _optional_str(semantic_object.get("subject_kind"))
        subject_ref = _optional_str(semantic_object.get("subject_ref"))
        subject_revision = semantic_object.get("subject_revision")
        blockers: list[BlockingRequirementPayload] = []
        capabilities: dict[str, Any] = {
            "profile_kind": profile_kind,
            "subject_kind": subject_kind,
            "subject_ref": subject_ref,
            "matches_subject_revision": False,
        }
        capability_payload = dict(semantic_object.get("capability") or {})
        if "inferential_ready" in capability_payload:
            capabilities["inferential_ready"] = bool(capability_payload.get("inferential_ready"))
        if (
            not semantic_object.get("profile_ref")
            or profile_kind is None
            or subject_kind is None
            or subject_ref is None
            or (profile_kind == "requirement" and not semantic_object.get("requirement"))
            or (profile_kind == "capability" and not semantic_object.get("capability"))
        ):
            blockers.append(
                _blocker(
                    code="PROFILE_CONTRACT_INVALID",
                    message=(
                        "Compatibility profile must define profile_ref, profile_kind, subject_kind, "
                        "subject_ref, and the payload matching profile_kind."
                    ),
                    subject_ref=snapshot.ref,
                )
            )
        if lifecycle_status == "active" and subject_ref is not None:
            subject = context.load_dependency_snapshot(subject_ref)
            if subject is None or derive_lifecycle_status(subject.status) != "active":
                blockers.append(
                    _blocker(
                        code="PROFILE_SUBJECT_INACTIVE",
                        message="Compatibility profile subject must exist and be active.",
                        subject_ref=snapshot.ref,
                        dependency_ref=subject_ref,
                    )
                )
            elif subject_revision is None or int(subject_revision) != subject.revision:
                blockers.append(
                    _blocker(
                        code="PROFILE_SUBJECT_REVISION_MISMATCH",
                        message="Compatibility profile subject_revision does not match the active subject revision.",
                        subject_ref=snapshot.ref,
                        dependency_ref=subject_ref,
                    )
                )
            else:
                capabilities["matches_subject_revision"] = True
        if lifecycle_status != "active":
            readiness_status = "not_ready"
        else:
            readiness_status = _classify_active_readiness(
                blockers,
                stale_codes={"PROFILE_SUBJECT_REVISION_MISMATCH"},
                allow_stale=True,
            )
        return ReadinessResult(
            lifecycle_status=lifecycle_status,
            readiness_status=readiness_status,
            capabilities=capabilities,
            blocking_requirements=blockers,
            trace=[
                ReadinessTraceItem(
                    stage="lifecycle_gate",
                    detail=f"Derived lifecycle_status={lifecycle_status} from storage status={snapshot.status}.",
                    source="compiler_profile_readiness_evaluator",
                    subject_ref=snapshot.ref,
                )
            ],
            had_ready_predecessor=context.previously_ready(),
        )


def _contains_basic_process_blockers(blockers: Iterable[BlockingRequirementPayload]) -> bool:
    inferential_only_codes = {"PROCESS_PROFILE_MISSING", "PROCESS_PROFILE_MISMATCH"}
    return any(blocker.code not in inferential_only_codes for blocker in blockers)


def _metric_capabilities(header: dict[str, Any]) -> dict[str, Any]:
    result = derive_additivity_capabilities(header=header)
    return result.to_dict()


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
    return list(required_metric_input_slots(metric_family))


def _optional_required_target(
    target_kind: str,
    semantic_ref: Any,
) -> list[tuple[str, str, str | None]]:
    ref = _optional_str(semantic_ref)
    if ref is None:
        return []
    return [(target_kind, ref, ref)]


def _required_binding_targets(
    context: ReadinessEvaluationContext,
    binding_scope: str | None,
    bound_object_ref: str | None,
) -> list[tuple[str, str, str | None]] | None:
    if binding_scope is None or bound_object_ref is None:
        return None
    subject = context.load_dependency_snapshot(bound_object_ref)
    semantic_object = subject.semantic_object if subject is not None else {}
    if binding_scope == "entity":
        interface_contract = dict(semantic_object.get("interface_contract") or {})
        identity = dict(interface_contract.get("identity") or {})
        key_refs = [str(item) for item in identity.get("key_refs") or [] if str(item).strip()]
        return [
            *[("identity_key", key_ref, key_ref) for key_ref in key_refs],
            *_optional_required_target("primary_time", interface_contract.get("primary_time_ref")),
            *[
                ("stable_descriptor", descriptor["dimension_ref"], descriptor["dimension_ref"])
                for descriptor in interface_contract.get("stable_descriptors") or []
                if isinstance(descriptor, dict) and descriptor.get("dimension_ref")
            ],
        ]
    if binding_scope == "metric":
        header = dict(semantic_object.get("header") or {})
        payload = dict(semantic_object.get("payload") or {})
        return [
            *[
                ("metric_input", target_key, None)
                for target_key in _required_metric_inputs(header, payload)
            ],
            *_optional_required_target("primary_time", header.get("primary_time_ref")),
            *_optional_required_target("population_subject", header.get("population_subject_ref")),
        ]
    if binding_scope == "process_object":
        interface_contract = dict(semantic_object.get("interface_contract") or {})
        return [
            *_optional_required_target(
                "population_subject", interface_contract.get("population_subject_ref")
            ),
            *_optional_required_target(
                "analysis_window_anchor", interface_contract.get("anchor_time_ref")
            ),
        ]
    return None


def _binding_import_blockers(
    *,
    context: ReadinessEvaluationContext,
    binding: dict[str, Any],
    subject_ref: str,
    blocker_code: str,
) -> list[BlockingRequirementPayload]:
    blockers: list[BlockingRequirementPayload] = []
    for binding_import in binding.get("imports") or []:
        imported_binding_ref = str(
            binding_import.get("imported_binding_ref") or binding_import.get("binding_ref") or ""
        )
        imported = context.load_dependency_snapshot(imported_binding_ref)
        if imported is None or derive_lifecycle_status(imported.status) != "active":
            blockers.append(
                _blocker(
                    code=blocker_code,
                    message="Required imported binding must exist and be active.",
                    subject_ref=subject_ref,
                    dependency_ref=imported_binding_ref,
                )
            )
    return blockers


def _binding_target_coverage_blockers(
    *,
    bindings: list[dict[str, Any]],
    required_targets: list[tuple[str, str, str | None]],
    subject_ref: str,
) -> list[BlockingRequirementPayload]:
    blockers: list[BlockingRequirementPayload] = []
    for target_kind, target_key, semantic_ref in required_targets:
        if binding_contract_target_exists(
            bindings,
            target_kind=target_kind,
            target_key=target_key,
            semantic_ref=semantic_ref,
        ):
            continue
        blockers.append(
            _blocker(
                code="BINDING_TARGET_COVERAGE_MISSING",
                message=f"Binding is missing required {target_kind} coverage for {target_key}.",
                subject_ref=subject_ref,
            )
        )
    return blockers


def _binding_time_binding_blockers(
    *,
    subject_ref: str,
    carrier_bindings: list[dict[str, Any]],
    time_bindings: list[dict[str, Any]],
    context: ReadinessEvaluationContext,
) -> list[BlockingRequirementPayload]:
    carrier_index = {str(carrier.get("binding_key") or ""): carrier for carrier in carrier_bindings}
    blockers: list[BlockingRequirementPayload] = []
    for time_binding in time_bindings:
        if str(time_binding.get("resolution_kind") or "") != "timestamp_column":
            continue
        carrier_binding_key = str(time_binding.get("carrier_binding_key") or "")
        carrier = carrier_index.get(carrier_binding_key)
        if carrier is None:
            continue
        source_object = context.load_carrier_source_object(carrier)
        if source_object is None:
            continue
        timestamp_surface_ref = _optional_str(time_binding.get("timestamp_surface_ref"))
        timestamp_format = _optional_str(time_binding.get("timestamp_format")) or "native"
        if timestamp_surface_ref is None:
            continue
        physical_name = _carrier_surface_physical_name(carrier, timestamp_surface_ref)
        if physical_name is None:
            continue
        column_type = _source_object_column_type(source_object, physical_name)
        if timestamp_format == "native":
            if column_type is None:
                blockers.append(
                    _blocker(
                        code="TIME_BINDING_TIMESTAMP_FORMAT_MISSING",
                        message=(
                            "Native timestamp_column bindings require a source column type or an "
                            "explicit timestamp_format for string-backed columns."
                        ),
                        subject_ref=subject_ref,
                        dependency_ref=physical_name,
                        details={
                            "carrier_binding_key": carrier_binding_key,
                            "timestamp_surface_ref": timestamp_surface_ref,
                            "physical_name": physical_name,
                        },
                    )
                )
            elif not _is_native_timestamp_type(column_type):
                blockers.append(
                    _blocker(
                        code="TIME_BINDING_TIMESTAMP_NATIVE_TYPE_MISMATCH",
                        message=(
                            "timestamp_column with timestamp_format='native' must bind to a "
                            "timestamp-like physical column."
                        ),
                        subject_ref=subject_ref,
                        dependency_ref=physical_name,
                        details={
                            "carrier_binding_key": carrier_binding_key,
                            "timestamp_surface_ref": timestamp_surface_ref,
                            "physical_name": physical_name,
                            "column_type": column_type,
                        },
                    )
                )
        else:
            try:
                normalize_timestamp_format(timestamp_format)
            except ValueError as exc:
                blockers.append(
                    _blocker(
                        code="TIME_BINDING_TIMESTAMP_FORMAT_INVALID",
                        message=str(exc),
                        subject_ref=subject_ref,
                        dependency_ref=physical_name,
                        details={
                            "carrier_binding_key": carrier_binding_key,
                            "timestamp_surface_ref": timestamp_surface_ref,
                            "physical_name": physical_name,
                            "timestamp_format": timestamp_format,
                        },
                    )
                )
    return blockers


def _evaluate_subject_bindings(
    *,
    context: ReadinessEvaluationContext,
    expected_scope: str,
    required_targets: list[tuple[str, str, str | None]],
    missing_binding_code: str,
    coverage_code: str | None = None,
    coverage_code_map: dict[str, str] | None = None,
) -> tuple[list[BlockingRequirementPayload], list[ReadinessTraceItem], bool, bool]:
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
        blocker_details: dict[str, Any] = {
            "required_binding_scope": expected_scope,
            "bound_object_ref": snapshot.ref,
            "missing_targets": [
                {"target_kind": target_kind, "target_key": target_key}
                for target_kind, target_key, _semantic_ref in required_targets
            ],
        }
        if expected_scope == "metric":
            blocker_details["remediation"] = {
                "tool": "create_binding",
                "message": "Create a typed binding with header.binding_scope='metric' for this metric.",
            }
        blockers.append(
            _blocker(
                code=missing_binding_code,
                message=f"{snapshot.ref} requires at least one active {expected_scope} binding.",
                subject_ref=snapshot.ref,
                details=blocker_details,
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
        return blockers, trace, False, False

    ready_binding_found = False
    for binding in bindings:
        binding_ref = str(binding.get("binding_ref") or "")
        binding_trace, binding_blockers = _check_binding_readiness(
            context=context,
            binding=binding,
            subject_ref=snapshot.ref,
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
    return _dedupe_blockers(blockers), trace, ready_binding_found, True


def _check_binding_readiness(
    *,
    context: ReadinessEvaluationContext,
    binding: dict[str, Any],
    subject_ref: str,
    required_targets: list[tuple[str, str, str | None]],
    coverage_code: str | None,
    coverage_code_map: dict[str, str] | None,
) -> tuple[list[ReadinessTraceItem], list[BlockingRequirementPayload]]:
    binding_ref = str(binding.get("binding_ref") or "")
    interface_contract = dict(binding.get("interface_contract") or {})
    trace: list[ReadinessTraceItem] = []
    blockers: list[BlockingRequirementPayload] = []

    blockers.extend(
        _binding_import_blockers(
            context=context,
            binding={
                "imports": interface_contract.get("imports")
                or context.load_binding_imports(binding_ref)
            },
            subject_ref=subject_ref,
            blocker_code=f"{subject_ref.split('.', 1)[0].upper()}_BINDING_IMPORT_MISSING",
        )
    )
    carriers = list(interface_contract.get("carrier_bindings") or [])
    if not carriers:
        blockers.append(
            _blocker(
                code=f"{subject_ref.split('.', 1)[0].upper()}_CARRIER_MISSING",
                message="Binding must expose at least one carrier binding.",
                subject_ref=subject_ref,
                dependency_ref=binding_ref,
            )
        )
    for carrier in carriers:
        if context.load_carrier_source_object(carrier) is None:
            blockers.append(
                _blocker(
                    code=f"{subject_ref.split('.', 1)[0].upper()}_CARRIER_SOURCE_MISSING",
                    message="Binding carrier must resolve to a synced source object.",
                    subject_ref=subject_ref,
                    dependency_ref=binding_ref,
                )
            )
    time_target_bindings = list(interface_contract.get("field_bindings") or []) + list(
        interface_contract.get("time_bindings") or []
    )
    for target_kind, target_key, semantic_ref in required_targets:
        if binding_contract_target_exists(
            time_target_bindings,
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
                details={
                    "required_binding_scope": expected_scope_from_subject_ref(subject_ref),
                    "missing_targets": [{"target_kind": target_kind, "target_key": target_key}],
                },
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
    details: dict[str, Any] | None = None,
) -> BlockingRequirementPayload:
    return BlockingRequirementPayload(
        code=code,
        message=message,
        subject_ref=subject_ref,
        dependency_ref=dependency_ref,
        details=details,
    )


def expected_scope_from_subject_ref(subject_ref: str) -> str:
    if subject_ref.startswith("entity."):
        return "entity"
    if subject_ref.startswith("metric."):
        return "metric"
    if subject_ref.startswith("process."):
        return "process_object"
    return "unknown"


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _carrier_surface_physical_name(
    carrier_binding: dict[str, Any],
    surface_ref: str,
) -> str | None:
    for field_surface in carrier_binding.get("field_surfaces") or []:
        if str(field_surface.get("surface_ref") or "") == surface_ref:
            return _optional_str(field_surface.get("physical_name"))
    return None


def _source_object_column_type(source_object: dict[str, Any], physical_name: str) -> str | None:
    raw_properties = source_object.get("properties_json")
    properties: dict[str, Any] = {}
    if isinstance(raw_properties, str) and raw_properties:
        try:
            decoded = json.loads(raw_properties)
        except json.JSONDecodeError:
            decoded = {}
        if isinstance(decoded, dict):
            properties = decoded
    elif isinstance(raw_properties, dict):
        properties = raw_properties
    for column in properties.get("columns") or []:
        if str(column.get("name") or "") == physical_name:
            return _optional_str(column.get("type"))
    return None


def _is_native_timestamp_type(column_type: str) -> bool:
    normalized = column_type.strip().lower()
    return "timestamp" in normalized or normalized == "datetime"


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


def _all_blockers_in(
    blockers: Iterable[BlockingRequirementPayload],
    allowed_codes: set[str],
) -> bool:
    blocker_list = list(blockers)
    return bool(blocker_list) and all(blocker.code in allowed_codes for blocker in blocker_list)


def _classify_active_readiness(
    blockers: list[BlockingRequirementPayload],
    *,
    stale_codes: set[str],
    allow_stale: bool,
) -> str:
    if not blockers:
        return "ready"
    if allow_stale and _all_blockers_in(blockers, stale_codes):
        return "stale"
    return "not_ready"
