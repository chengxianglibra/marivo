"""Extract metric_value findings from a MetricFrame DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from typing import Any

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import Finding, Subject


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bucket_key(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return str(value.isoformat())
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def extract_metric_value_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    semantic_kind: str,
    measure_column: str,
    committed_at: datetime,
    time_column: str | None = None,
    dimension_columns: list[str] | None = None,
) -> list[Finding]:
    """Extract metric_value findings from an observation DataFrame.

    Supports scalar and time_series semantic kinds.
    """
    if semantic_kind == "scalar":
        if df.empty:
            return []
        value = _to_float(df.iloc[0][measure_column])
        canonical_item_key = "value"
        return [
            Finding(
                finding_id=make_finding_id(artifact_id, "metric_value", canonical_item_key),
                finding_type="metric_value",
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                canonical_item_key=canonical_item_key,
                payload={"value": value, "value_kind": "scalar"},
                committed_at=committed_at,
            )
        ]

    if semantic_kind == "time_series":
        if time_column is None:
            raise ValueError("time_series extraction requires time_column")
        findings: list[Finding] = []
        for _, row in df.iterrows():
            bucket_key = _bucket_key(row[time_column])
            canonical_item_key = f"buckets:{bucket_key}"
            findings.append(
                Finding(
                    finding_id=make_finding_id(artifact_id, "metric_value", canonical_item_key),
                    finding_type="metric_value",
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=subject,
                    canonical_item_key=canonical_item_key,
                    payload={
                        "value": _to_float(row[measure_column]),
                        "value_kind": "time_series_bucket",
                        "bucket_start": bucket_key,
                    },
                    committed_at=committed_at,
                )
            )
        return findings

    return []


__all__ = ["extract_metric_value_findings"]
