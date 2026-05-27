"""Extract correlation_result findings from an AssociationResult DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from typing import Any

import pandas as pd

from marivo.analysis_py.errors import FindingExtractionFailedError
from marivo.analysis_py.evidence.identity import make_finding_id
from marivo.analysis_py.evidence.types import Finding, Subject


def _is_missing(value: Any) -> bool:
    return bool(pd.isna(value)) if not isinstance(value, (list, tuple, dict)) else False


def _to_float(value: Any) -> float | None:
    if value is None or _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None or _is_missing(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_value(value: Any) -> Any:
    if _is_missing(value):
        return None
    return value


def extract_correlation_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    committed_at: datetime,
) -> list[Finding]:
    """Extract one finding per pairwise association artifact."""
    if df.empty:
        raise FindingExtractionFailedError(
            message="correlation extraction requires at least one row",
            details={"artifact_id": artifact_id},
        )

    row = df.iloc[0]
    payload: dict[str, Any] = {
        "left_ref": _json_value(row.get("left_ref")),
        "right_ref": _json_value(row.get("right_ref")),
        "method": _json_value(row.get("method")),
        "coefficient": _to_float(row.get("coefficient")),
        "p_value": _to_float(row.get("p_value")),
        "n": _to_int(row.get("n")),
        "join_basis": _json_value(row.get("join_basis")),
    }
    return [
        Finding(
            finding_id=make_finding_id(
                artifact_id=artifact_id,
                finding_type="correlation_result",
                canonical_item_key="result",
            ),
            finding_type="correlation_result",
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            canonical_item_key="result",
            payload=payload,
            committed_at=committed_at,
        )
    ]


__all__ = ["extract_correlation_findings"]
