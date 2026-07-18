"""Extract forecast_point findings from a ForecastFrame DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from typing import Any

import pandas as pd

from marivo.analysis.errors import FindingExtractionFailedError
from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import (
    AnalysisScope,
    DerivationRule,
    Finding,
    ForecastPointFindingValue,
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


def _bucket_key(start: Any, end: Any) -> str:
    return f"{start}|{end}"


_VALUE_COLUMNS = {
    "actual",
    "error",
    "horizon_index",
    "lower",
    "predicted_value",
    "upper",
}


def _item_key(row: pd.Series[Any], columns: pd.Index) -> str:
    """Return a stable bucket key that also separates panel members."""
    bucket = _bucket_key(row.get("bucket_start"), row.get("bucket_end"))
    dimensions = [
        f"{column}={row.get(column)}"
        for column in columns
        if column not in _VALUE_COLUMNS and column not in {"bucket_start", "bucket_end"}
    ]
    return "|".join([bucket, *dimensions])


def extract_forecast_point_findings(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: Subject,
    committed_at: datetime,
    model: str,
    training_scope: AnalysisScope,
    evaluation_scope: AnalysisScope | None = None,
) -> list[Finding]:
    """Extract one finding per future bucket."""
    if df.empty:
        raise FindingExtractionFailedError(
            message="forecast extraction requires at least one bucket",
            context={"artifact_id": artifact_id},
        )

    findings: list[Finding] = []
    for _, row in df.iterrows():
        bucket_start = row.get("bucket_start")
        bucket_end = row.get("bucket_end")
        if _is_missing(bucket_start) or _is_missing(bucket_end):
            raise FindingExtractionFailedError(
                message="forecast bucket boundaries must be defined",
                context={"artifact_id": artifact_id},
            )
        item_key = _item_key(row, df.columns)
        lower = _to_float(row.get("lower"))
        upper = _to_float(row.get("upper"))
        prediction_interval = (lower, upper) if lower is not None and upper is not None else None
        horizon_index = _to_int(row.get("horizon_index"))
        if horizon_index is None:
            horizon_index = len(findings) + 1
        findings.append(
            Finding(
                finding_id=make_finding_id(
                    artifact_id=artifact_id,
                    finding_type="forecast_point",
                    canonical_item_key=item_key,
                ),
                finding_type="forecast_point",
                epistemic_kind="predicted",
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                canonical_item_key=item_key,
                value=ForecastPointFindingValue(
                    bucket_start=str(bucket_start),
                    bucket_end=str(bucket_end),
                    predicted_value=_to_float(row.get("predicted_value")),
                    prediction_interval=prediction_interval,
                    horizon_index=horizon_index,
                    model=model,
                    training_scope=training_scope,
                    evaluation_scope=evaluation_scope,
                    observed_actual=_to_float(row.get("actual")),
                    accuracy_metric=_to_float(row.get("error")),
                ),
                derivation=DerivationRule(
                    rule_id="extract.forecast_point",
                    rule_version="v2",
                    operator="forecast",
                    source_fields=tuple(str(column) for column in df.columns),
                    source_finding_refs=(),
                ),
                source_refs=(artifact_id,),
                committed_at=committed_at,
            )
        )
    return findings


__all__ = ["extract_forecast_point_findings"]
