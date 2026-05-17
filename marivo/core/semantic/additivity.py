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
- ``additive_dimensions = ["__all"]`` means additive on every declared
  dimension.
- Time-axis rollup (is the time field itself additive?) is checked at
  request level using the same dimension allowance helper.
- ``capability_condition`` is ``"dimension_must_be_allowed"`` when
  ``additive_dimensions`` is non-empty, signalling that runtime dimension
  membership checks are required before decompose/attribute.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

ADDITIVE_DIMENSIONS_ALL: Final = "__all"


@dataclass(slots=True, frozen=True)
class AdditivityCapabilityResult:
    """Complete capability derivation for a metric based on additivity."""

    supports_observe: bool
    supports_compare: bool
    supports_decompose: bool
    supports_attribute: bool
    supports_detect: bool
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
            "supports_detect": self.supports_detect,
            "additive_dimensions": self.additive_dimensions,
            "blocker": self.blocker,
            "remediation_hint": self.remediation_hint,
            "capability_condition": self.capability_condition,
        }


def derive_additivity_capabilities(
    *,
    additive_dimensions: list[str],
    process_anchor_time_ref: str | None = None,
) -> AdditivityCapabilityResult:
    """Derive analysis capabilities from additive_dimensions.

    Parameters
    ----------
    additive_dimensions:
        List of dimension field names across which the metric is additive.
        Empty list means the metric is not additive on any dimension.
    process_anchor_time_ref:
        Optional anchor time ref from an associated process object.
        Affects supports_detect.
    """
    if len(additive_dimensions) == 0:
        supports_decompose = False
        blocker = "ADDITIVITY_NONE"
        remediation_hint = (
            "additive_dimensions is empty — metric is not additive "
            "on any dimension.  Add dimension names to enable "
            "decompose/attribute."
        )
        capability_condition: str | None = None
    else:
        supports_decompose = True
        blocker = None
        remediation_hint = None
        capability_condition = "dimension_must_be_allowed"

    # Composite capabilities
    supports_compare = True
    supports_attribute = supports_compare and supports_decompose
    supports_detect = bool(process_anchor_time_ref)

    return AdditivityCapabilityResult(
        supports_observe=True,
        supports_compare=supports_compare,
        supports_decompose=supports_decompose,
        supports_attribute=supports_attribute,
        supports_detect=supports_detect,
        additive_dimensions=additive_dimensions,
        blocker=blocker,
        remediation_hint=remediation_hint,
        capability_condition=capability_condition,
    )


def is_all_additive_dimensions(additive_dimensions: Sequence[str]) -> bool:
    """Return whether additive_dimensions uses the all-dimensions sentinel."""
    return len(additive_dimensions) == 1 and additive_dimensions[0] == ADDITIVE_DIMENSIONS_ALL


def additive_dimensions_mix_all(additive_dimensions: Sequence[str]) -> bool:
    """Return whether ``__all`` is mixed with explicit dimension names."""
    return ADDITIVE_DIMENSIONS_ALL in additive_dimensions and not is_all_additive_dimensions(
        additive_dimensions
    )


def additive_dimension_allows(additive_dimensions: Sequence[str], dimension: str) -> bool:
    """Return whether a requested dimension is allowed by additive_dimensions."""
    return is_all_additive_dimensions(additive_dimensions) or dimension in additive_dimensions


def additive_time_rollup_allowed(
    additive_dimensions: Sequence[str], time_scope_field: str | None
) -> bool:
    """Return whether the request time field is additive for rollup metadata."""
    return bool(time_scope_field) and additive_dimension_allows(
        additive_dimensions, time_scope_field or ""
    )
