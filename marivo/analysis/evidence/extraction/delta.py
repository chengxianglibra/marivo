"""Extract delta findings from a DeltaFrame DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from typing import Any

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import Finding, Subject


def _to_float(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _classify_direction(delta: float | None, current: float | None, baseline: float | None) -> str:
    if delta is None:
        return "undefined"
    if delta > 0:
        return "increase"
    if delta < 0:
        return "decrease"
    return "flat"


def _presence(current: float | None, baseline: float | None) -> str | None:
    if current is not None and baseline is None:
        return "current_only"
    if current is None and baseline is not None:
        return "baseline_only"
    return None


_ESCAPE_CHARS = (("%", "%25"), ("=", "%3D"), ("|", "%7C"))


def _escape_seg_component(value: Any) -> str:
    text = "" if value is None else str(value)
    for raw, encoded in _ESCAPE_CHARS:
        text = text.replace(raw, encoded)
    return text


def _segment_stable_key(keys: dict[str, Any]) -> str:
    parts = [
        f"{_escape_seg_component(k)}={_escape_seg_component(v)}" for k, v in sorted(keys.items())
    ]
    return "|".join(parts)


def _delta_kind(semantic_kind: str) -> str:
    return {
        "scalar": "scalar_delta",
        "segmented": "segmented_delta",
        "time_series": "time_series_delta",
        "panel": "panel_delta",
    }[semantic_kind]


def extract_delta_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    semantic_kind: str,
    committed_at: datetime,
    dimension_columns: list[str] | None = None,
    time_column: str | None = None,
    unit: str | None = None,
) -> list[Finding]:
    """Extract delta findings from a comparison DataFrame.

    Supports scalar and segmented semantic kinds.
    """
    if df.empty:
        return []
    delta_kind = _delta_kind(semantic_kind)

    if semantic_kind == "scalar":
        row = df.iloc[0]
        current = _to_float(row.get("current"))
        baseline = _to_float(row.get("baseline"))
        delta_val = _to_float(row.get("delta"))
        pct = _to_float(row.get("pct_change"))
        canonical_item_key = "value"
        payload: dict[str, Any] = {
            "delta_kind": delta_kind,
            "current": current,
            "baseline": baseline,
            "magnitude": delta_val,
            "pct_change": pct,
            "direction": _classify_direction(delta_val, current, baseline),
            "presence": _presence(current, baseline),
            "unit": unit,
        }
        return [
            Finding(
                finding_id=make_finding_id(artifact_id, "delta", canonical_item_key),
                finding_type="delta",
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                canonical_item_key=canonical_item_key,
                payload=payload,
                committed_at=committed_at,
            )
        ]

    if semantic_kind == "segmented":
        if not dimension_columns:
            raise ValueError("segmented delta extraction requires dimension_columns")
        findings: list[Finding] = []
        for _, row in df.iterrows():
            keys = {col: row[col] for col in dimension_columns}
            seg_key = _segment_stable_key(keys)
            canonical_item_key = f"rows:{seg_key}"
            current = _to_float(row.get("current"))
            baseline = _to_float(row.get("baseline"))
            delta_val = _to_float(row.get("delta"))
            pct = _to_float(row.get("pct_change"))
            findings.append(
                Finding(
                    finding_id=make_finding_id(artifact_id, "delta", canonical_item_key),
                    finding_type="delta",
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=subject,
                    canonical_item_key=canonical_item_key,
                    payload={
                        "delta_kind": delta_kind,
                        "current": current,
                        "baseline": baseline,
                        "magnitude": delta_val,
                        "pct_change": pct,
                        "direction": _classify_direction(delta_val, current, baseline),
                        "presence": _presence(current, baseline),
                        "unit": unit,
                        "dimension_keys": {k: str(v) for k, v in keys.items()},
                    },
                    committed_at=committed_at,
                )
            )
        return findings

    return []


__all__ = ["extract_delta_findings"]
