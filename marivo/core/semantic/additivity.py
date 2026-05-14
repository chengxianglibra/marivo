"""Pure additivity capability derivation for metrics.

Single source of truth for deriving analysis capabilities from a metric's
``additive_dimensions``.  All consumers (readiness evaluator, compiler
capability profiles, intent gates) must call
``derive_additivity_capabilities`` instead of implementing their own logic.

Design notes
~~~~~~~~~~~~
- Reads ``additive_dimensions: list[str]`` directly — the OSI schema field.
- ``additive_dimensions = []`` means the metric is not additive on any
  dimension (equivalent to old ``dimension_policy: "none"``).
- ``additive_dimensions = ["dim1", ...]`` means additive only on listed
  dimensions.  Decompose/attribute are gated on dimension membership.
- Time-axis additivity is derived: ``primary_time_ref in additive_dimensions``.
- ``capability_condition`` is ``"dimension_must_be_allowed"`` when
  ``additive_dimensions`` is non-empty, signalling that runtime dimension
  membership checks are required before decompose/attribute.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class AdditivityCapabilityResult:
    """Complete capability derivation for a metric based on additivity."""

    supports_observe: bool
    supports_compare: bool
    supports_decompose: bool
    supports_attribute: bool
    supports_test: bool
    supports_detect: bool
    supports_validate: bool
    time_rollup_allowed: bool
    additive_dimensions: list[str]
    blocker: str | None
    remediation_hint: str | None
    capability_condition: str | None  # "dimension_must_be_allowed" | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "supports_observe": self.supports_observe,
            "supports_compare": self.supports_compare,
            "supports_decompose": self.supports_decompose,
            "supports_attribute": self.supports_attribute,
            "supports_test": self.supports_test,
            "supports_detect": self.supports_detect,
            "supports_validate": self.supports_validate,
            "time_rollup_allowed": self.time_rollup_allowed,
            "additive_dimensions": self.additive_dimensions,
            "blocker": self.blocker,
            "remediation_hint": self.remediation_hint,
            "capability_condition": self.capability_condition,
        }


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def derive_additivity_capabilities(
    *,
    additive_dimensions: list[str],
    primary_time_ref: str | None = None,
    sample_kind: str | None = None,
    process_anchor_time_ref: str | None = None,
) -> AdditivityCapabilityResult:
    """Derive analysis capabilities from additive_dimensions.

    Parameters
    ----------
    additive_dimensions:
        List of dimension field names across which the metric is additive.
        Empty list means the metric is not additive on any dimension.
    primary_time_ref:
        The primary time field for the metric.  Affects supports_compare,
        supports_detect, and time_rollup_allowed.
    sample_kind:
        The metric sample kind.  Affects supports_test and supports_validate.
    process_anchor_time_ref:
        Optional anchor time ref from an associated process object.
        Affects supports_detect.
    """
    primary_time_ref = _optional_str(primary_time_ref)
    sample_kind = _optional_str(sample_kind) or ""

    if len(additive_dimensions) == 0:
        supports_decompose = False
        time_rollup_allowed = False
        blocker = "ADDITIVITY_NONE"
        remediation_hint = (
            "additive_dimensions is empty — metric is not additive "
            "on any dimension.  Add dimension names to enable "
            "decompose/attribute."
        )
        capability_condition: str | None = None
    else:
        supports_decompose = True
        time_rollup_allowed = primary_time_ref in additive_dimensions if primary_time_ref else False
        blocker = None
        remediation_hint = None
        capability_condition = "dimension_must_be_allowed"

    # Composite capabilities
    supports_compare = bool(primary_time_ref)
    supports_attribute = supports_compare and supports_decompose
    supports_test = sample_kind in {"numeric", "rate", "binary"}
    supports_detect = bool(primary_time_ref or process_anchor_time_ref)
    supports_validate = sample_kind == "rate"

    return AdditivityCapabilityResult(
        supports_observe=True,
        supports_compare=supports_compare,
        supports_decompose=supports_decompose,
        supports_attribute=supports_attribute,
        supports_test=supports_test,
        supports_detect=supports_detect,
        supports_validate=supports_validate,
        time_rollup_allowed=time_rollup_allowed,
        additive_dimensions=additive_dimensions,
        blocker=blocker,
        remediation_hint=remediation_hint,
        capability_condition=capability_condition,
    )
