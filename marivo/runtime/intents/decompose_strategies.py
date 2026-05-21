"""Decomposition strategy dispatcher and quality evaluator.

Maps decomposition_semantics to the appropriate decomposition method:
  sum -> delta_share
  ratio -> ratio_decomposition (stub, Task 5)
  weighted_average -> weighted_decomposition (stub, Task 6)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_FLAT_TOLERANCE_RELATIVE = 0.01  # 1% tolerance for sum reconciliation


@dataclass(slots=True, frozen=True)
class QualitySignals:
    reconciliation_status: str  # "reconciled" | "approximate" | "unreconcilable"
    reconciliation_gap: float | None
    confidence_grade: str  # "high" | "medium" | "low"
    confidence_rationale: str | None
    recommended_use: str  # "trusted" | "exploratory" | "reject"


@dataclass(slots=True)
class DecompositionResult:
    rows: list[dict[str, Any]]
    method: str
    quality: QualitySignals
    unexplained_absolute_delta: float | None = None
    unexplained_share: float | None = None
    unexplained_reason: str | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)


_STRATEGY_DISPATCH = {
    "sum": "delta_share_strategy",
    "ratio": "ratio_decomposition_strategy",
    "weighted_average": "weighted_decomposition_strategy",
}


def dispatch_decomposition_strategy(
    decomposition_semantics: str,
    left_map: dict[Any, float | None],
    right_map: dict[Any, float | None],
    scope_absolute_delta: float | None,
    dimension: str,
    *,
    numerator_left_map: dict[Any, float | None] | None = None,
    numerator_right_map: dict[Any, float | None] | None = None,
    denominator_left_map: dict[Any, float | None] | None = None,
    denominator_right_map: dict[Any, float | None] | None = None,
    weight_left_map: dict[Any, float | None] | None = None,
    weight_right_map: dict[Any, float | None] | None = None,
    scope_ratio_delta: float | None = None,
    scope_current_weighted_value: float | None = None,
    scope_baseline_weighted_value: float | None = None,
    scope_weighted_delta: float | None = None,
) -> DecompositionResult:
    strategy_name = _STRATEGY_DISPATCH.get(decomposition_semantics)
    if strategy_name is None:
        raise ValueError(f"unknown decomposition_semantics: {decomposition_semantics}")
    strategy_fn: Callable[..., DecompositionResult] = globals()[strategy_name]
    return strategy_fn(
        left_map=left_map,
        right_map=right_map,
        scope_absolute_delta=scope_absolute_delta,
        dimension=dimension,
        numerator_left_map=numerator_left_map,
        numerator_right_map=numerator_right_map,
        denominator_left_map=denominator_left_map,
        denominator_right_map=denominator_right_map,
        weight_left_map=weight_left_map,
        weight_right_map=weight_right_map,
        scope_ratio_delta=scope_ratio_delta,
        scope_current_weighted_value=scope_current_weighted_value,
        scope_baseline_weighted_value=scope_baseline_weighted_value,
        scope_weighted_delta=scope_weighted_delta,
    )


def _evaluate_quality(
    *,
    reconciliation_gap: float | None,
    decomposition_semantics: str,
    unexplained_share: float | None,
) -> QualitySignals:
    if decomposition_semantics == "sum":
        if reconciliation_gap is not None and abs(reconciliation_gap) <= _FLAT_TOLERANCE_RELATIVE:
            return QualitySignals(
                reconciliation_status="reconciled",
                reconciliation_gap=reconciliation_gap,
                confidence_grade="high",
                confidence_rationale="sum decomposition reconciled within 1% tolerance",
                recommended_use="trusted",
            )
        elif reconciliation_gap is not None and abs(reconciliation_gap) <= 0.05:
            return QualitySignals(
                reconciliation_status="approximate",
                reconciliation_gap=reconciliation_gap,
                confidence_grade="medium",
                confidence_rationale="sum decomposition gap exceeds 1% but within 5%",
                recommended_use="exploratory",
            )
        else:
            return QualitySignals(
                reconciliation_status="unreconcilable",
                reconciliation_gap=reconciliation_gap,
                confidence_grade="low",
                confidence_rationale="sum decomposition gap exceeds 5% or is null",
                recommended_use="reject",
            )
    elif decomposition_semantics == "ratio":
        return QualitySignals(
            reconciliation_status="approximate",
            reconciliation_gap=reconciliation_gap,
            confidence_grade="medium",
            confidence_rationale="ratio decomposition is approximate by nature",
            recommended_use="exploratory",
        )
    elif decomposition_semantics == "weighted_average":
        return QualitySignals(
            reconciliation_status="approximate",
            reconciliation_gap=reconciliation_gap,
            confidence_grade="medium",
            confidence_rationale="weighted decomposition produces within/mix effects; exact reconciliation is not expected",
            recommended_use="exploratory",
        )
    raise ValueError(
        f"unknown decomposition_semantics for quality evaluation: {decomposition_semantics}"
    )


def _delta(lv: float | None, rv: float | None) -> float | None:
    if lv is None or rv is None:
        return None
    return lv - rv


def _signed_share(contribution: float | None, scope_delta: float | None) -> float | None:
    if contribution is None or scope_delta is None or scope_delta == 0:
        return None
    return contribution / scope_delta


def _compute_direction(delta: float | None) -> str:
    if delta is None:
        return "undefined"
    if delta > 0:
        return "increase"
    if delta < 0:
        return "decrease"
    return "flat"


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def delta_share_strategy(
    *,
    left_map: dict[Any, float | None],
    right_map: dict[Any, float | None],
    scope_absolute_delta: float | None,
    dimension: str,
    **_kwargs: Any,
) -> DecompositionResult:
    all_keys = set(left_map) | set(right_map)
    rows: list[dict[str, Any]] = []
    for key in sorted(all_keys, key=lambda k: "" if k is None else str(k)):
        in_left = key in left_map
        in_right = key in right_map
        lv = left_map.get(key) if in_left else None
        rv = right_map.get(key) if in_right else None
        if in_left and in_right:
            presence = "both"
            abs_contribution = _delta(lv, rv)
        elif in_left:
            presence = "current_only"
            rv = None
            abs_contribution = lv
        else:
            presence = "baseline_only"
            lv = None
            abs_contribution = (-rv) if rv is not None else None
        contribution_share = _signed_share(abs_contribution, scope_absolute_delta)
        direction = _compute_direction(abs_contribution)
        rows.append(
            {
                "key": key,
                "current_value": lv,
                "baseline_value": rv,
                "absolute_contribution": abs_contribution,
                "contribution_share": contribution_share,
                "direction": direction,
                "presence": presence,
            }
        )

    if not rows:
        raise ValueError(
            "decompose: NOT_ATTRIBUTABLE - no contribution rows formed for the requested dimension"
        )

    rows.sort(
        key=lambda r: (
            -(abs(r["contribution_share"]) if r["contribution_share"] is not None else 0.0),
            -(abs(r["absolute_contribution"]) if r["absolute_contribution"] is not None else 0.0),
            "" if r["key"] is None else str(r["key"]),
        )
    )

    explained = sum(
        r["absolute_contribution"] for r in rows if r["absolute_contribution"] is not None
    )
    unexplained_absolute_delta = None
    unexplained_share = None
    unexplained_reason = None
    issues: list[dict[str, Any]] = []

    if scope_absolute_delta is not None:
        unexplained_absolute_delta = scope_absolute_delta - explained
        unexplained_share = (
            unexplained_absolute_delta / scope_absolute_delta if scope_absolute_delta != 0 else None
        )
        if abs(unexplained_absolute_delta) < 1e-9:
            unexplained_absolute_delta = 0.0
            unexplained_share = 0.0
    else:
        issues.append(
            {
                "code": "data_incomplete",
                "severity": "warning",
                "message": "scope_absolute_delta is null; contribution_share cannot be computed",
            }
        )

    reconciliation_gap = unexplained_share
    quality = _evaluate_quality(
        reconciliation_gap=reconciliation_gap,
        decomposition_semantics="sum",
        unexplained_share=unexplained_share,
    )

    if quality.reconciliation_status == "unreconcilable" and unexplained_absolute_delta is not None:
        unexplained_reason = "scope_recomputation_failed"
        issues.append(
            {
                "code": "attribution_not_reconcilable",
                "severity": "error",
                "message": (
                    f"Explained sum diverges from scope_absolute_delta by "
                    f"{abs(reconciliation_gap):.1%}; recomputed scope may differ from "
                    f"upstream compare due to grain or filter differences."
                    if reconciliation_gap is not None
                    else "reconciliation gap is null"
                ),
            }
        )
    elif quality.reconciliation_status == "reconciled" and unexplained_absolute_delta is not None:
        unexplained_reason = "rounding"
    elif quality.reconciliation_status == "approximate" and unexplained_absolute_delta is not None:
        unexplained_reason = "approximate_reconciliation"

    return DecompositionResult(
        rows=rows,
        method="delta_share",
        quality=quality,
        unexplained_absolute_delta=unexplained_absolute_delta,
        unexplained_share=unexplained_share,
        unexplained_reason=unexplained_reason,
        issues=issues,
    )


def ratio_decomposition_strategy(
    *,
    left_map: dict[Any, float | None],
    right_map: dict[Any, float | None],
    scope_absolute_delta: float | None,
    dimension: str,
    numerator_left_map: dict[Any, float | None] | None = None,
    numerator_right_map: dict[Any, float | None] | None = None,
    denominator_left_map: dict[Any, float | None] | None = None,
    denominator_right_map: dict[Any, float | None] | None = None,
    scope_ratio_delta: float | None = None,
    **_kwargs: Any,
) -> DecompositionResult:
    """Decompose a ratio metric change into numerator and denominator effects per segment.

    For each segment: ratio_delta = numerator_effect + denominator_effect + residual
    where numerator_effect = (n_curr - n_base) / d_base
    and   denominator_effect = -n_base * (d_curr - d_base) / (d_curr * d_base)

    Falls back to delta_share when component maps are not provided.
    """
    if numerator_left_map is None or denominator_left_map is None:
        # Component maps not available from upstream — fallback to delta_share
        fallback = delta_share_strategy(
            left_map=left_map,
            right_map=right_map,
            scope_absolute_delta=scope_absolute_delta,
            dimension=dimension,
        )
        return DecompositionResult(
            rows=fallback.rows,
            method="ratio_decomposition",
            quality=_evaluate_quality(
                reconciliation_gap=fallback.quality.reconciliation_gap,
                decomposition_semantics="ratio",
                unexplained_share=fallback.unexplained_share,
            ),
            unexplained_absolute_delta=fallback.unexplained_absolute_delta,
            unexplained_share=fallback.unexplained_share,
            unexplained_reason=fallback.unexplained_reason,
            issues=[
                *fallback.issues,
                {
                    "code": "strategy_fallback",
                    "severity": "warning",
                    "message": "ratio decomposition uses delta_share fallback; component maps not provided",
                },
            ],
        )

    numerator_left = numerator_left_map or {}
    numerator_right = numerator_right_map or {}
    denominator_left = denominator_left_map or {}
    denominator_right = denominator_right_map or {}
    all_keys = (
        set(numerator_left) | set(numerator_right) | set(denominator_left) | set(denominator_right)
    )
    rows: list[dict[str, Any]] = []
    for key in sorted(all_keys, key=lambda k: "" if k is None else str(k)):
        n_curr = numerator_left.get(key)
        n_base = numerator_right.get(key)
        d_curr = denominator_left.get(key)
        d_base = denominator_right.get(key)

        curr_ratio = (
            (n_curr / d_curr)
            if (n_curr is not None and d_curr is not None and d_curr != 0)
            else None
        )
        base_ratio = (
            (n_base / d_base)
            if (n_base is not None and d_base is not None and d_base != 0)
            else None
        )
        ratio_delta = _delta(curr_ratio, base_ratio)

        numerator_effect: float | None = None
        if n_curr is not None and n_base is not None and d_base is not None and d_base != 0:
            numerator_effect = (n_curr - n_base) / d_base

        denominator_effect: float | None = None
        if (
            n_base is not None
            and d_curr is not None
            and d_base is not None
            and d_curr != 0
            and d_base != 0
        ):
            denominator_effect = -n_base * (d_curr - d_base) / (d_curr * d_base)

        residual: float | None = None
        if (
            ratio_delta is not None
            and numerator_effect is not None
            and denominator_effect is not None
        ):
            residual = ratio_delta - numerator_effect - denominator_effect

        rows.append(
            {
                "key": key,
                "current_ratio": curr_ratio,
                "baseline_ratio": base_ratio,
                "segment_ratio_delta": ratio_delta,
                "numerator_contribution": numerator_effect,
                "denominator_contribution": denominator_effect,
                "residual": residual,
                "current_numerator": n_curr,
                "baseline_numerator": n_base,
                "current_denominator": d_curr,
                "baseline_denominator": d_base,
                "direction": _compute_direction(ratio_delta),
            }
        )

    if not rows:
        raise ValueError(
            "decompose: NOT_ATTRIBUTABLE - no contribution rows formed for the requested dimension"
        )

    rows.sort(
        key=lambda r: (
            -(abs(r["segment_ratio_delta"]) if r["segment_ratio_delta"] is not None else 0.0),
            "" if r["key"] is None else str(r["key"]),
        )
    )

    reconciliation_gap: float | None = None
    if scope_ratio_delta is not None:
        total_explained = sum(
            (r["numerator_contribution"] or 0.0) + (r["denominator_contribution"] or 0.0)
            for r in rows
        )
        reconciliation_gap = scope_ratio_delta - total_explained

    quality = _evaluate_quality(
        reconciliation_gap=reconciliation_gap,
        decomposition_semantics="ratio",
        unexplained_share=None,
    )

    return DecompositionResult(
        rows=rows,
        method="ratio_decomposition",
        quality=quality,
        unexplained_absolute_delta=reconciliation_gap,
        unexplained_share=None,
        unexplained_reason="ratio_decomposition_residual",
        issues=[],
    )


def weighted_decomposition_strategy(
    *,
    left_map: dict[Any, float | None],
    right_map: dict[Any, float | None],
    scope_absolute_delta: float | None,
    dimension: str,
    numerator_left_map: dict[Any, float | None] | None = None,
    numerator_right_map: dict[Any, float | None] | None = None,
    weight_left_map: dict[Any, float | None] | None = None,
    weight_right_map: dict[Any, float | None] | None = None,
    scope_weighted_delta: float | None = None,
    scope_current_weighted_value: float | None = None,
    scope_baseline_weighted_value: float | None = None,
    **_kwargs: Any,
) -> DecompositionResult:
    """Decompose a weighted_average metric change into within and mix effects per segment.

    For each segment:
      within_effect = (weighted_value_curr - weighted_value_base) * (w_curr / w_curr_total)
      mix_effect    = (w_curr/w_curr_total - w_base/w_base_total) * weighted_value_base
      residual      = segment_contribution - within_effect - mix_effect

    Falls back to delta_share when component maps are not provided.
    """
    if numerator_left_map is None or weight_left_map is None:
        # Component maps not available from upstream — fallback to delta_share
        fallback = delta_share_strategy(
            left_map=left_map,
            right_map=right_map,
            scope_absolute_delta=scope_absolute_delta,
            dimension=dimension,
        )
        return DecompositionResult(
            rows=fallback.rows,
            method="weighted_decomposition",
            quality=_evaluate_quality(
                reconciliation_gap=fallback.quality.reconciliation_gap,
                decomposition_semantics="weighted_average",
                unexplained_share=fallback.unexplained_share,
            ),
            unexplained_absolute_delta=fallback.unexplained_absolute_delta,
            unexplained_share=fallback.unexplained_share,
            unexplained_reason=fallback.unexplained_reason,
            issues=[
                *fallback.issues,
                {
                    "code": "strategy_fallback",
                    "severity": "warning",
                    "message": "weighted decomposition uses delta_share fallback; component maps not provided",
                },
            ],
        )

    n_left = numerator_left_map or {}
    n_right = numerator_right_map or {}
    w_left = weight_left_map or {}
    w_right = weight_right_map or {}

    # Total weights for composition proportions
    w_curr_total = sum(v for v in w_left.values() if v is not None)
    w_base_total = sum(v for v in w_right.values() if v is not None)

    all_keys = set(n_left) | set(n_right) | set(w_left) | set(w_right)
    rows: list[dict[str, Any]] = []
    for key in sorted(all_keys, key=lambda k: "" if k is None else str(k)):
        n_curr = n_left.get(key)
        n_base = n_right.get(key)
        w_curr = w_left.get(key)
        w_base = w_right.get(key)

        weighted_value_curr: float | None = None
        if n_curr is not None and w_curr is not None and w_curr != 0:
            weighted_value_curr = n_curr / w_curr

        weighted_value_base: float | None = None
        if n_base is not None and w_base is not None and w_base != 0:
            weighted_value_base = n_base / w_base

        # Within effect: value change within current composition
        within_effect: float | None = None
        if (
            weighted_value_curr is not None
            and weighted_value_base is not None
            and w_curr is not None
            and w_curr_total != 0
        ):
            within_effect = (weighted_value_curr - weighted_value_base) * (w_curr / w_curr_total)

        # Mix effect: composition change at baseline value
        mix_effect: float | None = None
        if (
            w_curr is not None
            and w_base is not None
            and w_curr_total != 0
            and w_base_total != 0
            and weighted_value_base is not None
        ):
            mix_effect = (w_curr / w_curr_total - w_base / w_base_total) * weighted_value_base

        residual: float | None = None
        # Residual is the difference between the segment's total weighted-average
        # contribution and the decomposition's explained effects; should be near zero.
        weighted_delta = _delta(weighted_value_curr, weighted_value_base)
        if (
            weighted_delta is not None
            and within_effect is not None
            and mix_effect is not None
            and w_curr is not None
            and w_curr_total != 0
        ):
            residual = (weighted_delta * w_curr / w_curr_total) - within_effect - mix_effect

        rows.append(
            {
                "key": key,
                "current_weighted_value": weighted_value_curr,
                "baseline_weighted_value": weighted_value_base,
                "within_effect": within_effect,
                "mix_effect": mix_effect,
                "residual": residual,
                "current_numerator": n_curr,
                "baseline_numerator": n_base,
                "current_weight": w_curr,
                "baseline_weight": w_base,
                "current_weight_share": (w_curr / w_curr_total)
                if (w_curr is not None and w_curr_total != 0)
                else None,
                "baseline_weight_share": (w_base / w_base_total)
                if (w_base is not None and w_base_total != 0)
                else None,
                "direction": _compute_direction(within_effect),
            }
        )

    if not rows:
        raise ValueError(
            "decompose: NOT_ATTRIBUTABLE - no contribution rows formed for the requested dimension"
        )

    rows.sort(
        key=lambda r: (
            -(abs(r["within_effect"]) if r["within_effect"] is not None else 0.0),
            -(abs(r["mix_effect"]) if r["mix_effect"] is not None else 0.0),
            "" if r["key"] is None else str(r["key"]),
        )
    )

    reconciliation_gap: float | None = None
    if scope_weighted_delta is not None:
        total_explained = sum((r["within_effect"] or 0.0) + (r["mix_effect"] or 0.0) for r in rows)
        reconciliation_gap = scope_weighted_delta - total_explained

    quality = _evaluate_quality(
        reconciliation_gap=reconciliation_gap,
        decomposition_semantics="weighted_average",
        unexplained_share=None,
    )

    return DecompositionResult(
        rows=rows,
        method="weighted_decomposition",
        quality=quality,
        unexplained_absolute_delta=reconciliation_gap,
        unexplained_share=None,
        unexplained_reason="weighted_decomposition_residual",
        issues=[],
    )
