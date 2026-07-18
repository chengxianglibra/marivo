"""Extract test_result findings from a HypothesisTestResult DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from typing import Any, Literal, cast

import pandas as pd

from marivo.analysis.errors import FindingExtractionFailedError
from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import DerivationRule, Finding, Subject, TestFindingValue


def _is_missing(value: Any) -> bool:
    return bool(pd.isna(value)) if not isinstance(value, (list, tuple, dict)) else False


def _to_float(value: Any) -> float | None:
    if value is None or _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None or _is_missing(value):
        return None
    return bool(value)


def _json_value(value: Any) -> Any:
    if _is_missing(value):
        return None
    return value


def _interval(lower: Any, upper: Any) -> tuple[float, float] | None:
    parsed_lower = _to_float(lower)
    parsed_upper = _to_float(upper)
    return (
        (parsed_lower, parsed_upper)
        if parsed_lower is not None and parsed_upper is not None
        else None
    )


def extract_test_result_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    committed_at: datetime,
    alternative: str = "two_sided",
) -> list[Finding]:
    """Extract one finding per hypothesis-test artifact."""
    if df.empty:
        raise FindingExtractionFailedError(
            message="hypothesis_test extraction requires at least one row",
            context={"artifact_id": artifact_id},
        )

    row = df.iloc[0]
    alpha = _to_float(row.get("alpha"))
    if alpha is None:
        raise FindingExtractionFailedError(
            message="hypothesis_test extraction requires alpha",
            context={"artifact_id": artifact_id},
        )
    normalized_alternative = cast(
        "Literal['two_sided', 'greater', 'less']",
        alternative if alternative in {"two_sided", "greater", "less"} else "two_sided",
    )
    current_ref = _json_value(row.get("current_ref"))
    baseline_ref = _json_value(row.get("baseline_ref"))
    return [
        Finding(
            finding_id=make_finding_id(
                artifact_id=artifact_id,
                finding_type="test_result",
                canonical_item_key="result",
            ),
            finding_type="test_result",
            epistemic_kind="tested",
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            canonical_item_key="result",
            value=TestFindingValue(
                null_predicate="current_minus_baseline_equals_zero",
                alternative=normalized_alternative,
                method=str(row.get("method") or "unknown_method"),
                alpha=alpha,
                statistic=_to_float(row.get("statistic_value")),
                p_value=_to_float(row.get("p_value")),
                effect_estimate=_to_float(row.get("estimate_value")),
                confidence_interval=_interval(row.get("interval_lower"), row.get("interval_upper")),
                reject_null=_to_bool(row.get("reject_null")),
                sample_size=(
                    int(value)
                    if (value := _to_float(row.get("n"))) is not None and value >= 0
                    else None
                ),
            ),
            derivation=DerivationRule(
                rule_id="extract.test_decision",
                rule_version="v2",
                operator="hypothesis_test",
                source_fields=tuple(str(column) for column in df.columns),
                source_finding_refs=(),
            ),
            source_refs=tuple(
                str(ref) for ref in (current_ref, baseline_ref) if isinstance(ref, str)
            ),
            committed_at=committed_at,
        )
    ]


__all__ = ["extract_test_result_findings"]
