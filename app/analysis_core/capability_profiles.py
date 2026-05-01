"""Legacy analysis_core.capability_profiles stubs — preserved for import compatibility.

Removed during OSI v2 migration.  See Task 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DerivedMetricCapabilities:
    supports_observe: bool = True
    supports_compare: bool = False
    supports_decompose: bool = False
    supports_attribute: bool = False
    supports_test: bool = False
    supports_detect: bool = False
    supports_validate: bool = False
    time_rollup_allowed: bool = False
    dimension_policy: str = "none"
    time_axis_policy: str = "non_additive"
    additive_dimensions: list[str] | None = None
    capability_condition: str | None = None


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
