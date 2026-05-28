"""Extract test_result findings from a HypothesisTestResult DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from typing import Any

import pandas as pd

from marivo.analysis.errors import FindingExtractionFailedError
from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import Finding, Subject


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


def extract_test_result_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    committed_at: datetime,
) -> list[Finding]:
    """Extract one finding per hypothesis-test artifact."""
    if df.empty:
        raise FindingExtractionFailedError(
            message="test extraction requires at least one row",
            details={"artifact_id": artifact_id},
        )

    row = df.iloc[0]
    payload: dict[str, Any] = {
        "current_ref": _json_value(row.get("current_ref")),
        "baseline_ref": _json_value(row.get("baseline_ref")),
        "method": _json_value(row.get("method")),
        "estimate_value": _to_float(row.get("estimate_value")),
        "statistic_name": _json_value(row.get("statistic_name")),
        "statistic_value": _to_float(row.get("statistic_value")),
        "p_value": _to_float(row.get("p_value")),
        "reject_null": _to_bool(row.get("reject_null")),
        "alpha": _to_float(row.get("alpha")),
    }
    return [
        Finding(
            finding_id=make_finding_id(
                artifact_id=artifact_id,
                finding_type="test_result",
                canonical_item_key="result",
            ),
            finding_type="test_result",
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            canonical_item_key="result",
            payload=payload,
            committed_at=committed_at,
        )
    ]


__all__ = ["extract_test_result_findings"]
