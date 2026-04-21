"""Shared additivity capability derivation for metrics.

Single source of truth for deriving analysis capabilities from a metric's
additivity properties.  All consumers (readiness evaluator, compiler
capability profiles, intent gates) must call ``derive_additivity_capabilities``
instead of implementing their own logic.

P1 design notes
~~~~~~~~~~~~~~~
- Reads from the existing ``header.additivity`` three-state field.
- ``semi_additive`` is treated fail-closed: we cannot express *which*
  dimensions are allowed, so we do not claim ``supports_decompose``.
- When P2 introduces ``header.additivity_constraints``, this module will
  be updated to read from constraints instead of the legacy field.
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
    dimension_policy: str  # "all" | "none"  (P2 adds "subset")
    time_axis_policy: str  # "additive" | "non_additive"
    additivity_basis: dict[str, Any]  # raw inputs for downstream consumers
    blocker: str | None  # e.g. "ADDITIVITY_MISSING"
    remediation_hint: str | None

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
            "dimension_policy": self.dimension_policy,
            "time_axis_policy": self.time_axis_policy,
            "additivity_basis": self.additivity_basis,
            "blocker": self.blocker,
            "remediation_hint": self.remediation_hint,
        }


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def derive_additivity_capabilities(
    *,
    header: dict[str, Any],
    process_anchor_time_ref: str | None = None,
) -> AdditivityCapabilityResult:
    """Derive analysis capabilities from a metric header.

    Parameters
    ----------
    header:
        The metric header dict.  Must contain at least ``additivity``,
        ``primary_time_ref``, and ``sample_kind`` keys (values may be
        missing/empty — the function handles that gracefully).
    process_anchor_time_ref:
        Optional anchor time ref from an associated process object.
        Affects ``supports_detect`` and ``supports_validate``.
    """
    additivity = _optional_str(header.get("additivity")) or ""
    primary_time_ref = _optional_str(header.get("primary_time_ref"))
    sample_kind = _optional_str(header.get("sample_kind")) or ""

    # ── Additivity → dimension / time policies ────────────────────────────
    if additivity == "additive":
        dimension_policy = "all"
        time_axis_policy = "additive"
        supports_decompose = True
        time_rollup_allowed = True
        blocker = None
        remediation_hint = None
    elif additivity == "semi_additive":
        # Fail-closed: without constraints we cannot express allowed dimensions.
        dimension_policy = "none"
        time_axis_policy = "non_additive"
        supports_decompose = False
        time_rollup_allowed = False
        blocker = "ADDITIVITY_SEMI_ADDITIVE_NO_CONSTRAINTS"
        remediation_hint = (
            "Metric is semi_additive but additivity_constraints are not yet "
            "declared. Declare additivity_constraints with an explicit "
            "dimension_policy and additive_dimensions to enable decompose/attribute."
        )
    elif additivity == "non_additive":
        dimension_policy = "none"
        time_axis_policy = "non_additive"
        supports_decompose = False
        time_rollup_allowed = False
        blocker = None
        remediation_hint = None
    else:
        # Missing or unrecognised additivity — fail-closed.
        dimension_policy = "none"
        time_axis_policy = "non_additive"
        supports_decompose = False
        time_rollup_allowed = False
        blocker = "ADDITIVITY_MISSING"
        remediation_hint = (
            "Metric header is missing a valid additivity value. "
            "Provide additivity ('additive', 'semi_additive', or "
            "'non_additive') in the metric header."
        )

    # ── Composite capabilities ────────────────────────────────────────────
    supports_compare = bool(additivity and primary_time_ref)
    supports_attribute = supports_compare and supports_decompose
    supports_test = sample_kind in {"numeric", "rate", "binary"}
    supports_detect = bool(primary_time_ref or process_anchor_time_ref)
    # supports_validate is metric-only; process_anchor_time_ref not required.
    # The validate intent runs on rate metrics without a process object.
    supports_validate = sample_kind == "rate"

    additivity_basis: dict[str, Any] = {
        "additivity": additivity,
        "primary_time_ref": primary_time_ref,
        "sample_kind": sample_kind,
        "process_anchor_time_ref": process_anchor_time_ref,
    }

    return AdditivityCapabilityResult(
        supports_observe=True,
        supports_compare=supports_compare,
        supports_decompose=supports_decompose,
        supports_attribute=supports_attribute,
        supports_test=supports_test,
        supports_detect=supports_detect,
        supports_validate=supports_validate,
        time_rollup_allowed=time_rollup_allowed,
        dimension_policy=dimension_policy,
        time_axis_policy=time_axis_policy,
        additivity_basis=additivity_basis,
        blocker=blocker,
        remediation_hint=remediation_hint,
    )
