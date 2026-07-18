"""Extract correlation_result findings from an AssociationResult DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from typing import Any

import pandas as pd

from marivo.analysis.errors import FindingExtractionFailedError
from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import (
    AssociationFindingValue,
    DerivationRule,
    Finding,
    Subject,
)


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


def _interval(lower: Any, upper: Any) -> tuple[float, float] | None:
    parsed_lower = _to_float(lower)
    parsed_upper = _to_float(upper)
    return (
        (parsed_lower, parsed_upper)
        if parsed_lower is not None and parsed_upper is not None
        else None
    )


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
            context={"artifact_id": artifact_id},
        )

    row = df.iloc[0]
    return [
        Finding(
            finding_id=make_finding_id(
                artifact_id=artifact_id,
                finding_type="correlation_result",
                canonical_item_key="result",
            ),
            finding_type="correlation_result",
            epistemic_kind="estimated",
            artifact_id=artifact_id,
            session_id=session_id,
            subject=subject,
            canonical_item_key="result",
            value=AssociationFindingValue(
                left_ref=str(row.get("left_ref") or "unknown_left"),
                right_ref=str(row.get("right_ref") or "unknown_right"),
                method=str(row.get("method") or "unknown_method"),
                coefficient=_to_float(row.get("coefficient")),
                p_value=_to_float(row.get("p_value")),
                confidence_interval=_interval(row.get("interval_lower"), row.get("interval_upper")),
                sample_size=_to_int(row.get("n")),
                join_basis=str(row.get("join_basis") or "unknown_alignment"),
                lag=_to_float(row.get("lag")),
            ),
            derivation=DerivationRule(
                rule_id="extract.association",
                rule_version="v2",
                operator="correlate",
                source_fields=tuple(str(column) for column in df.columns),
                source_finding_refs=(),
            ),
            source_refs=tuple(
                str(ref)
                for ref in (row.get("left_ref"), row.get("right_ref"))
                if ref is not None and not _is_missing(ref)
            ),
            committed_at=committed_at,
        )
    ]


__all__ = ["extract_correlation_findings"]
