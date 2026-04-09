from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.semantic_runtime.resolution import ResolvedSemanticObject

ProfileReader = Callable[[str], list[dict[str, Any]]]


@dataclass(slots=True)
class DerivedMetricCapabilities:
    supports_observe: bool = True
    supports_compare: bool = False
    supports_decompose: bool = False
    supports_test: bool = False
    supports_detect: bool = False
    supports_validate: bool = False


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


@dataclass(slots=True)
class ProfileTrace:
    subject_ref: str
    profile_ref: str | None
    applied: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_ref": self.subject_ref,
            "profile_ref": self.profile_ref,
            "applied": self.applied,
            "reason": self.reason,
        }


@dataclass(slots=True)
class DerivedCompilerState:
    metric_capabilities: DerivedMetricCapabilities | None = None
    process_capabilities: DerivedProcessCapabilities | None = None
    binding_capabilities: dict[str, DerivedBindingCapabilities] = field(default_factory=dict)
    metric_requirements: MetricProcessRequirements = field(
        default_factory=MetricProcessRequirements
    )
    profile_traces: list[ProfileTrace] = field(default_factory=list)
    usage_trace: list[dict[str, Any]] = field(default_factory=list)

    def capabilities_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.metric_capabilities is not None:
            payload["metric"] = {
                "supports_observe": self.metric_capabilities.supports_observe,
                "supports_compare": self.metric_capabilities.supports_compare,
                "supports_decompose": self.metric_capabilities.supports_decompose,
                "supports_test": self.metric_capabilities.supports_test,
                "supports_detect": self.metric_capabilities.supports_detect,
                "supports_validate": self.metric_capabilities.supports_validate,
            }
        if self.process_capabilities is not None:
            payload["process"] = {
                "supports_time_projection": self.process_capabilities.supports_time_projection,
                "supports_experiment_inference": (
                    self.process_capabilities.supports_experiment_inference
                ),
                "supports_cohort_inference": self.process_capabilities.supports_cohort_inference,
                "inferential_ready": self.process_capabilities.inferential_ready,
                "supported_sample_summaries": list(
                    self.process_capabilities.supported_sample_summaries
                ),
            }
        if self.binding_capabilities:
            payload["bindings"] = {
                binding_ref: {
                    "inferential_ready": capability.inferential_ready,
                }
                for binding_ref, capability in self.binding_capabilities.items()
            }
        payload["metric_requirements"] = {
            "contract_modes": list(self.metric_requirements.contract_modes),
            "context_kinds": list(self.metric_requirements.context_kinds),
            "entity_refs": list(self.metric_requirements.entity_refs),
            "population_subject_refs": list(self.metric_requirements.population_subject_refs),
        }
        return payload


def derive_compiler_state(
    *,
    intent_kind: str,
    resolved_metric: ResolvedSemanticObject | None,
    resolved_process: ResolvedSemanticObject | None,
    resolved_bindings: list[ResolvedSemanticObject],
    profile_reader: ProfileReader | None,
) -> DerivedCompilerState:
    state = DerivedCompilerState()
    if resolved_metric is not None:
        state.metric_capabilities = _derive_metric_capabilities(
            resolved_metric=resolved_metric,
            resolved_process=resolved_process,
        )
        state.usage_trace.append(
            {
                "subject_ref": resolved_metric.ref,
                "source": "metric_core_fields",
                "intent_kind": intent_kind,
                "derived_keys": [
                    "supports_compare",
                    "supports_decompose",
                    "supports_detect",
                    "supports_observe",
                    "supports_test",
                    "supports_validate",
                ],
            }
        )
        requirement_profile = _load_profile(
            resolved_metric.ref,
            expected_kind="requirement",
            profile_reader=profile_reader,
            traces=state.profile_traces,
        )
        if requirement_profile is not None:
            requirement = dict(requirement_profile.get("requirement") or {})
            state.metric_requirements = MetricProcessRequirements(
                contract_modes=list(requirement.get("contract_modes") or []),
                context_kinds=list(requirement.get("context_kinds") or []),
                entity_refs=list(requirement.get("entity_refs") or []),
                population_subject_refs=list(requirement.get("population_subject_refs") or []),
            )
            state.usage_trace.append(
                {
                    "subject_ref": resolved_metric.ref,
                    "profile_ref": requirement_profile.get("profile_ref"),
                    "source": "compatibility_profile",
                    "applies_to": "metric_requirements",
                }
            )
    if resolved_process is not None:
        state.process_capabilities = _derive_process_capabilities(resolved_process)
        state.usage_trace.append(
            {
                "subject_ref": resolved_process.ref,
                "source": "process_core_fields",
                "intent_kind": intent_kind,
                "derived_keys": [
                    "supports_time_projection",
                    "supports_experiment_inference",
                    "supports_cohort_inference",
                ],
            }
        )
        capability_profile = _load_profile(
            resolved_process.ref,
            expected_kind="capability",
            profile_reader=profile_reader,
            traces=state.profile_traces,
        )
        if capability_profile is not None:
            capability = dict(capability_profile.get("capability") or {})
            state.process_capabilities.inferential_ready = capability.get("inferential_ready")
            state.process_capabilities.supported_sample_summaries = list(
                capability.get("supported_sample_summaries") or []
            )
            state.usage_trace.append(
                {
                    "subject_ref": resolved_process.ref,
                    "profile_ref": capability_profile.get("profile_ref"),
                    "source": "compatibility_profile",
                    "applies_to": "process_capabilities",
                }
            )
    for binding in resolved_bindings:
        binding_capabilities = DerivedBindingCapabilities()
        capability_profile = _load_profile(
            binding.ref,
            expected_kind="capability",
            profile_reader=profile_reader,
            traces=state.profile_traces,
        )
        if capability_profile is not None:
            capability = dict(capability_profile.get("capability") or {})
            binding_capabilities.inferential_ready = capability.get("inferential_ready")
            state.usage_trace.append(
                {
                    "subject_ref": binding.ref,
                    "profile_ref": capability_profile.get("profile_ref"),
                    "source": "compatibility_profile",
                    "applies_to": "binding_capabilities",
                }
            )
        state.binding_capabilities[binding.ref] = binding_capabilities
    return state


def _derive_metric_capabilities(
    *,
    resolved_metric: ResolvedSemanticObject,
    resolved_process: ResolvedSemanticObject | None,
) -> DerivedMetricCapabilities:
    header = dict(resolved_metric.semantic_object.get("header") or {})
    sample_kind = str(header.get("sample_kind") or "").strip()
    additivity = str(header.get("additivity") or "").strip()
    primary_time_ref = _optional_str(header.get("primary_time_ref"))
    process_anchor_time_ref = None
    if resolved_process is not None:
        process_contract = dict(resolved_process.semantic_object.get("interface_contract") or {})
        process_anchor_time_ref = _optional_str(process_contract.get("anchor_time_ref"))
    return DerivedMetricCapabilities(
        supports_observe=True,
        supports_compare=bool(additivity and primary_time_ref),
        supports_decompose=additivity in {"additive", "semi_additive"},
        supports_test=sample_kind in {"numeric", "rate", "binary"},
        supports_detect=bool(primary_time_ref or process_anchor_time_ref),
        supports_validate=(sample_kind == "rate" and bool(process_anchor_time_ref)),
    )


def _derive_process_capabilities(
    resolved_process: ResolvedSemanticObject,
) -> DerivedProcessCapabilities:
    interface_contract = dict(resolved_process.semantic_object.get("interface_contract") or {})
    anchor_time_ref = _optional_str(interface_contract.get("anchor_time_ref"))
    context_kind = str(interface_contract.get("context_kind") or "").strip()
    return DerivedProcessCapabilities(
        supports_time_projection=bool(anchor_time_ref),
        supports_experiment_inference=context_kind == "experiment_split",
        supports_cohort_inference=context_kind == "cohort_membership",
    )


def _load_profile(
    subject_ref: str,
    *,
    expected_kind: str,
    profile_reader: ProfileReader | None,
    traces: list[ProfileTrace],
) -> dict[str, Any] | None:
    if profile_reader is None:
        traces.append(
            ProfileTrace(subject_ref=subject_ref, profile_ref=None, applied=False, reason="missing")
        )
        return None
    profiles = profile_reader(subject_ref)
    if not profiles:
        traces.append(
            ProfileTrace(subject_ref=subject_ref, profile_ref=None, applied=False, reason="missing")
        )
        return None
    matching = [p for p in profiles if str(p.get("profile_kind") or "") == expected_kind]
    if not matching:
        traces.append(
            ProfileTrace(
                subject_ref=subject_ref,
                profile_ref=str(profiles[0].get("profile_ref") or ""),
                applied=False,
                reason="not_required",
            )
        )
        return None
    profile = matching[0]
    traces.append(
        ProfileTrace(
            subject_ref=subject_ref,
            profile_ref=str(profile.get("profile_ref") or ""),
            applied=True,
            reason="satisfied",
        )
    )
    return profile


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
