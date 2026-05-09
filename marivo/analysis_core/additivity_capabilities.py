# DEPRECATED: Pure computation extracted to app.core.semantic.additivity.

"""Shared additivity capability derivation for metrics.

Single source of truth for deriving analysis capabilities from a metric's
additivity_constraints.  All consumers (readiness evaluator, compiler
capability profiles, intent gates) must call ``derive_additivity_capabilities``
instead of implementing their own logic.

Design notes
~~~~~~~~~~~~
- Reads from ``header.additivity_constraints`` (structured object).
- ``additivity_constraints`` is the single source of truth for metric
  decomposability and time-axis rollup behavior.
- Missing or empty constraints are fail-closed: we cannot express allowed
  dimensions, so we do not claim ``supports_decompose``.
- ``dimension_policy = "subset"`` with non-empty ``additive_dimensions``
  enables decompose/attribute on those specific dimensions only.
- ``capability_condition`` indicates when a capability is conditional:
  ``"dimension_must_be_allowed"`` means decompose/attribute are only valid
  on dimensions listed in ``additive_dimensions``.
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
    dimension_policy: str  # "all" | "subset" | "none"
    time_axis_policy: str  # "additive" | "non_additive"
    additive_dimensions: list[str] | None
    additivity_basis: dict[str, Any]  # raw inputs for downstream consumers
    blocker: str | None  # e.g. "ADDITIVITY_CONSTRAINTS_MISSING"
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
            "dimension_policy": self.dimension_policy,
            "time_axis_policy": self.time_axis_policy,
            "additive_dimensions": self.additive_dimensions,
            "additivity_basis": self.additivity_basis,
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
    header: dict[str, Any],
    process_anchor_time_ref: str | None = None,
) -> AdditivityCapabilityResult:
    """Derive analysis capabilities from a metric header.

    Parameters
    ----------
    header:
        The metric header dict.  Must contain at least
        ``additivity_constraints``, ``primary_time_ref``, and ``sample_kind``
        keys (values may be missing/empty — the function handles that
        gracefully).
    process_anchor_time_ref:
        Optional anchor time ref from an associated process object.
        Affects ``supports_detect`` and ``supports_validate``.
    """
    constraints_raw = header.get("additivity_constraints")
    primary_time_ref = _optional_str(header.get("primary_time_ref"))
    sample_kind = _optional_str(header.get("sample_kind")) or ""

    # ── Parse constraints ──────────────────────────────────────────────────
    if constraints_raw is None:
        dimension_policy = "none"
        time_axis_policy = "non_additive"
        additive_dimensions: list[str] | None = None
        supports_decompose = False
        time_rollup_allowed = False
        blocker = "ADDITIVITY_CONSTRAINTS_MISSING"
        remediation_hint = (
            "Metric header is missing additivity_constraints. "
            "Provide additivity_constraints with dimension_policy and "
            "time_axis_policy."
        )
    elif isinstance(constraints_raw, dict):
        dp = constraints_raw.get("dimension_policy")
        tap = constraints_raw.get("time_axis_policy")
        ad = constraints_raw.get("additive_dimensions")

        if dp is None or (isinstance(dp, str) and not dp.strip()):
            # dimension_policy field is missing or empty
            dimension_policy = "none"
            time_axis_policy = "non_additive"
            additive_dimensions = None
            supports_decompose = False
            time_rollup_allowed = False
            blocker = "ADDITIVITY_CONSTRAINTS_DIMENSION_POLICY_MISSING"
            remediation_hint = (
                "additivity_constraints is missing dimension_policy. "
                "Must be 'all', 'subset', or 'none'."
            )
        elif dp not in ("all", "subset", "none"):
            # dimension_policy has an unrecognized value
            dimension_policy = "none"
            time_axis_policy = "non_additive"
            additive_dimensions = None
            supports_decompose = False
            time_rollup_allowed = False
            blocker = "ADDITIVITY_CONSTRAINTS_INVALID"
            remediation_hint = (
                f"Unrecognized dimension_policy: {dp!r}. Must be 'all', 'subset', or 'none'."
            )
        else:
            # dimension_policy is valid
            dimension_policy = str(dp)
            additive_dimensions = ad if isinstance(ad, list) else None

            if tap is None or (isinstance(tap, str) and not tap.strip()):
                # time_axis_policy field is missing or empty
                time_axis_policy = "non_additive"
                supports_decompose = False
                time_rollup_allowed = False
                blocker = "ADDITIVITY_CONSTRAINTS_TIME_AXIS_POLICY_MISSING"
                remediation_hint = (
                    "additivity_constraints is missing time_axis_policy. "
                    "Must be 'additive' or 'non_additive'."
                )
            elif tap not in ("additive", "non_additive"):
                # time_axis_policy has an unrecognized value
                time_axis_policy = "non_additive"
                supports_decompose = False
                time_rollup_allowed = False
                blocker = "ADDITIVITY_CONSTRAINTS_INVALID"
                remediation_hint = (
                    f"Unrecognized time_axis_policy: {tap!r}. Must be 'additive' or 'non_additive'."
                )
            else:
                # Both dimension_policy and time_axis_policy are valid
                time_axis_policy = str(tap)
                blocker = None
                remediation_hint = None

                if dimension_policy == "all":
                    supports_decompose = True
                    time_rollup_allowed = time_axis_policy == "additive"
                elif dimension_policy == "subset":
                    if additive_dimensions and len(additive_dimensions) > 0:
                        supports_decompose = True
                        time_rollup_allowed = time_axis_policy == "additive"
                    else:
                        supports_decompose = False
                        time_rollup_allowed = False
                        blocker = "ADDITIVITY_SUBSET_NO_DIMENSIONS"
                        remediation_hint = (
                            "dimension_policy='subset' but additive_dimensions is "
                            "empty. Provide at least one additive dimension."
                        )
                else:  # "none"
                    supports_decompose = False
                    time_rollup_allowed = False
    else:
        dimension_policy = "none"
        time_axis_policy = "non_additive"
        additive_dimensions = None
        supports_decompose = False
        time_rollup_allowed = False
        blocker = "ADDITIVITY_CONSTRAINTS_INVALID"
        remediation_hint = "additivity_constraints must be a structured object."

    # ── Composite capabilities ────────────────────────────────────────────
    supports_compare = bool(constraints_raw is not None and primary_time_ref)
    supports_attribute = supports_compare and supports_decompose
    supports_test = sample_kind in {"numeric", "rate", "binary"}
    supports_detect = bool(primary_time_ref or process_anchor_time_ref)
    supports_validate = sample_kind == "rate"

    # ── capability_condition ──────────────────────────────────────────────
    capability_condition: str | None = None
    if blocker is None and dimension_policy == "subset":
        capability_condition = "dimension_must_be_allowed"

    additivity_basis: dict[str, Any] = {
        "dimension_policy": dimension_policy,
        "time_axis_policy": time_axis_policy,
        "additive_dimensions": additive_dimensions,
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
        additive_dimensions=additive_dimensions,
        additivity_basis=additivity_basis,
        blocker=blocker,
        remediation_hint=remediation_hint,
        capability_condition=capability_condition,
    )
