"""Extract decomposition_item findings from an AttributionFrame DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from typing import Any

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import (
    ContributionFindingValue,
    DerivationRule,
    Finding,
    Subject,
)

_ESCAPE_CHARS = (("%", "%25"), ("=", "%3D"), ("|", "%7C"))
_RESERVED_COLUMNS = {
    "dimension",
    "contribution_value",
    "contribution_share",
    "contribution",
    "pct_contribution",
    "rank",
    "direction",
    "method",
    "reconciliation_residual",
}


def _escape(value: Any) -> str:
    text = "" if value is None else str(value)
    for raw, encoded in _ESCAPE_CHARS:
        text = text.replace(raw, encoded)
    return text


def _is_missing(value: Any) -> bool:
    return bool(pd.isna(value)) if not isinstance(value, (list, tuple, dict)) else False


def _json_value(value: Any) -> Any:
    if _is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    return value


def _key_tuple(dimension: str, keys: dict[str, Any]) -> str:
    parts = [f"{_escape(k)}={_escape(v)}" for k, v in sorted(keys.items())]
    return f"{_escape(dimension)}|" + "|".join(parts)


def _to_float(value: Any) -> float | None:
    if value is None or _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_decomposition_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    committed_at: datetime,
    scope_delta_ref: str,
) -> list[Finding]:
    """Extract one finding per contribution row."""
    if df.empty:
        return []

    findings: list[Finding] = []
    ranked_rows = list(df.iterrows())
    ranked_rows.sort(key=lambda entry: -abs(_to_float(entry[1].get("contribution_value")) or 0.0))
    for rank, (_, row) in enumerate(ranked_rows, start=1):
        dimension = str(row.get("dimension", ""))
        keys: dict[str, Any] = {}
        for column in df.columns:
            if column not in _RESERVED_COLUMNS:
                keys[column] = _json_value(row[column])
        item_key = _key_tuple(dimension, keys)
        findings.append(
            Finding(
                finding_id=make_finding_id(
                    artifact_id=artifact_id,
                    finding_type="decomposition_item",
                    canonical_item_key=item_key,
                ),
                finding_type="decomposition_item",
                epistemic_kind="algebraic",
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                canonical_item_key=item_key,
                value=ContributionFindingValue(
                    dimension=dimension,
                    dimension_keys=keys,
                    contribution_value=_to_float(row.get("contribution_value")),
                    contribution_share=_to_float(row.get("contribution_share")),
                    contribution_rank=rank,
                    direction=row.get("direction") or "undefined",
                    decomposition_method=str(row.get("method") or "algebraic_decomposition"),
                    reconciliation_residual=_to_float(row.get("reconciliation_residual")),
                    scope_delta_ref=scope_delta_ref,
                ),
                derivation=DerivationRule(
                    rule_id="extract.contribution",
                    rule_version="v2",
                    operator="attribute",
                    source_fields=tuple(str(column) for column in df.columns),
                    source_finding_refs=(),
                ),
                source_refs=(scope_delta_ref,),
                committed_at=committed_at,
            )
        )
    return findings


__all__ = ["extract_decomposition_findings"]
