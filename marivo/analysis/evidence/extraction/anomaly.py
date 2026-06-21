"""Extract anomaly_candidate findings from a CandidateSet DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from typing import Any

import pandas as pd

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


def _json_value(value: Any) -> Any:
    if _is_missing(value):
        return None
    return value


def extract_anomaly_candidate_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    committed_at: datetime,
) -> list[Finding]:
    """Extract one finding per anomaly candidate row. Empty input is legal."""
    if df.empty:
        return []

    findings: list[Finding] = []
    for idx, row in df.iterrows():
        candidate_ref = _json_value(row["candidate_ref"]) if "candidate_ref" in row.index else None
        item_key = str(candidate_ref) if candidate_ref else f"row:{idx}"
        payload: dict[str, Any] = {
            "candidate_ref": candidate_ref,
            "score": _to_float(row.get("score")),
            "flag_level": _json_value(row.get("flag_level")),
            "current_value": _to_float(row.get("observed_value")),
            "baseline_value": _to_float(row.get("baseline_value")),
            "deviation_absolute": _to_float(row.get("delta")),
            "deviation_relative": _to_float(row.get("deviation_relative")),
        }
        findings.append(
            Finding(
                finding_id=make_finding_id(
                    artifact_id=artifact_id,
                    finding_type="anomaly_candidate",
                    canonical_item_key=item_key,
                ),
                finding_type="anomaly_candidate",
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                canonical_item_key=item_key,
                payload=payload,
                committed_at=committed_at,
            )
        )
    return findings


__all__ = ["extract_anomaly_candidate_findings"]
