"""Extract metric_value findings from a MetricFrame DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
import math
from datetime import datetime
from typing import Any, Literal

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import (
    DerivationRule,
    Finding,
    JsonScalar,
    MetricValueFindingValue,
    ObservationFindingValue,
    ObservationSegmentValue,
    ObservationValue,
    PanelObservationValue,
    ScalarObservationValue,
    SegmentedObservationValue,
    Subject,
    TimeSeriesObservationValue,
)

_TOP_SEGMENT_LIMIT = 5


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
    item_key_prefix: str | None = None,
    unit: str | None = None,
) -> list[Finding]:
    """Extract metric_value findings from an observation DataFrame.

    Supports scalar and time_series semantic kinds. When ``item_key_prefix``
    is provided (multi-measure frames), it is prepended to each finding's
    ``canonical_item_key`` so findings from different measures do not collide
    on the same artifact.
    """

    def _key(base: str) -> str:
        return f"{item_key_prefix}:{base}" if item_key_prefix else base

    if semantic_kind == "scalar":
        if df.empty:
            return []
        value = _to_float(df.iloc[0][measure_column])
        canonical_item_key = _key("value")
        return [
            Finding(
                finding_id=make_finding_id(artifact_id, "metric_value", canonical_item_key),
                finding_type="metric_value",
                epistemic_kind="observed",
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                canonical_item_key=canonical_item_key,
                value=MetricValueFindingValue(value=value, unit=unit),
                derivation=DerivationRule(
                    rule_id="extract.metric_value",
                    rule_version="v2",
                    operator="observe",
                    source_fields=(f"rows[0].{measure_column}",),
                    source_finding_refs=(),
                ),
                source_refs=(artifact_id,),
                committed_at=committed_at,
            )
        ]

    if semantic_kind == "time_series":
        if time_column is None:
            raise ValueError("time_series extraction requires time_column")
        findings: list[Finding] = []
        for _, row in df.iterrows():
            bucket_key = _bucket_key(row[time_column])
            canonical_item_key = _key(f"buckets:{bucket_key}")
            findings.append(
                Finding(
                    finding_id=make_finding_id(artifact_id, "metric_value", canonical_item_key),
                    finding_type="metric_value",
                    epistemic_kind="observed",
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=subject,
                    canonical_item_key=canonical_item_key,
                    value=MetricValueFindingValue(
                        value=_to_float(row[measure_column]),
                        unit=unit,
                        bucket=bucket_key,
                    ),
                    derivation=DerivationRule(
                        rule_id="extract.metric_value",
                        rule_version="v2",
                        operator="observe",
                        source_fields=(time_column, measure_column),
                        source_finding_refs=(),
                    ),
                    source_refs=(artifact_id,),
                    committed_at=committed_at,
                )
            )
        return findings

    if semantic_kind in {"segmented", "panel"}:
        key_columns = _segment_key_columns(
            df,
            measure_column=measure_column,
            time_column=time_column,
            dimension_columns=dimension_columns,
        )
        findings = []
        for row_index, row in df.iterrows():
            keys: dict[str, JsonScalar] = {column: str(row[column]) for column in key_columns}
            bucket = (
                _bucket_key(row[time_column])
                if semantic_kind == "panel" and time_column is not None
                else None
            )
            stable_parts = [f"{key}={keys[key]}" for key in sorted(keys)]
            if bucket is not None:
                stable_parts.append(f"bucket={bucket}")
            item_key = _key("rows:" + ("|".join(stable_parts) or str(row_index)))
            findings.append(
                Finding(
                    finding_id=make_finding_id(artifact_id, "metric_value", item_key),
                    finding_type="metric_value",
                    epistemic_kind="observed",
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=subject,
                    canonical_item_key=item_key,
                    value=MetricValueFindingValue(
                        value=_to_float(row[measure_column]),
                        unit=unit,
                        dimension_keys=keys,
                        bucket=bucket,
                    ),
                    derivation=DerivationRule(
                        rule_id="extract.metric_value",
                        rule_version="v2",
                        operator="observe",
                        source_fields=(*key_columns, measure_column),
                        source_finding_refs=(),
                    ),
                    source_refs=(artifact_id,),
                    committed_at=committed_at,
                )
            )
        return findings

    return []


def _clean_float(v: Any) -> float | None:
    value = _to_float(v)
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return value


def _direction(
    first: float | None, last: float | None
) -> Literal["increase", "decrease", "flat", "undefined"]:
    if first is None or last is None:
        return "undefined"
    if last > first:
        return "increase"
    if last < first:
        return "decrease"
    return "flat"


def _scalar_digest(df: pd.DataFrame, measure_column: str) -> ScalarObservationValue:
    if df.empty or measure_column not in df.columns:
        return ScalarObservationValue(value=None)
    return ScalarObservationValue(value=_clean_float(df.iloc[0][measure_column]))


def _time_series_digest(
    df: pd.DataFrame, measure_column: str, time_column: str | None
) -> TimeSeriesObservationValue:
    if df.empty or time_column is None or time_column not in df.columns:
        return TimeSeriesObservationValue(bucket_count=0)
    ordered = df.sort_values(time_column, kind="stable")
    values = (
        [_clean_float(v) for v in ordered[measure_column]]
        if measure_column in ordered.columns
        else []
    )
    present = [v for v in values if v is not None]
    first_value = values[0] if values else None
    last_value = values[-1] if values else None
    return TimeSeriesObservationValue(
        bucket_count=len(ordered),
        first_bucket=_bucket_key(ordered.iloc[0][time_column]),
        last_bucket=_bucket_key(ordered.iloc[-1][time_column]),
        first_value=first_value,
        last_value=last_value,
        min_value=min(present) if present else None,
        max_value=max(present) if present else None,
        mean_value=sum(present) / len(present) if present else None,
        endpoint_change_direction=_direction(first_value, last_value),
    )


def _segment_key_columns(
    df: pd.DataFrame,
    *,
    measure_column: str,
    time_column: str | None,
    dimension_columns: list[str] | None,
) -> list[str]:
    declared = [c for c in (dimension_columns or []) if c in df.columns]
    if declared:
        return declared
    excluded = {measure_column} | ({time_column} if time_column else set())
    return [c for c in df.columns if c not in excluded]


def _top_segments(
    items: list[tuple[dict[str, JsonScalar], float | None]], total: float | None
) -> list[ObservationSegmentValue]:
    def sort_key(item: tuple[dict[str, JsonScalar], float | None]) -> tuple[float, str]:
        keys, value = item
        magnitude = abs(value) if value is not None else 0.0
        return (-magnitude, json.dumps(keys, sort_keys=True))

    ranked = sorted(items, key=sort_key)[:_TOP_SEGMENT_LIMIT]
    shares: list[ObservationSegmentValue] = []
    for keys, value in ranked:
        share = value / total if value is not None and total else None
        shares.append(ObservationSegmentValue(keys=keys, value=value, share=share))
    return shares


def _segmented_digest(
    df: pd.DataFrame,
    measure_column: str,
    dimension_columns: list[str] | None,
    *,
    additive: bool,
) -> SegmentedObservationValue:
    if df.empty:
        return SegmentedObservationValue(segment_count=0)
    key_columns = _segment_key_columns(
        df, measure_column=measure_column, time_column=None, dimension_columns=dimension_columns
    )
    items: list[tuple[dict[str, JsonScalar], float | None]] = []
    for _, row in df.iterrows():
        keys: dict[str, JsonScalar] = {col: str(row[col]) for col in key_columns}
        value = _clean_float(row[measure_column]) if measure_column in df.columns else None
        items.append((keys, value))
    present = [v for _, v in items if v is not None]
    # total/share express composition; only additive metrics may sum across segments.
    total = sum(present) if additive and present else None
    return SegmentedObservationValue(
        segment_count=len(items),
        total_value=total,
        top_segments=tuple(_top_segments(items, total)),
    )


def _panel_digest(
    df: pd.DataFrame,
    measure_column: str,
    time_column: str | None,
    dimension_columns: list[str] | None,
    *,
    additive: bool,
) -> PanelObservationValue:
    if df.empty:
        return PanelObservationValue(bucket_count=0, segment_count=0)
    has_time = time_column is not None and time_column in df.columns
    key_columns = _segment_key_columns(
        df,
        measure_column=measure_column,
        time_column=time_column if has_time else None,
        dimension_columns=dimension_columns,
    )
    bucket_keys = sorted({_bucket_key(v) for v in df[time_column]}) if has_time else []
    totals: dict[str, tuple[dict[str, JsonScalar], float | None]] = {}
    for _, row in df.iterrows():
        keys: dict[str, JsonScalar] = {col: str(row[col]) for col in key_columns}
        key_json = json.dumps(keys, sort_keys=True)
        value = _clean_float(row[measure_column]) if measure_column in df.columns else None
        _, prior = totals.get(key_json, (keys, None))
        combined = prior if value is None else value if prior is None else prior + value
        totals[key_json] = (keys, combined)
    items = list(totals.values())
    # Panel top_segments require summing each segment across buckets; that and
    # the share denominator express composition, which only additive metrics
    # support. Non-additive panels keep counts and time span only.
    total: float | None = None
    if additive:
        present = [v for _, v in items if v is not None]
        total = sum(present) if present else None
        ranked = _top_segments(items, total)
    else:
        ranked = []
    return PanelObservationValue(
        bucket_count=len(bucket_keys),
        segment_count=len(items),
        first_bucket=bucket_keys[0] if bucket_keys else None,
        last_bucket=bucket_keys[-1] if bucket_keys else None,
        total_value=total,
        top_segments=tuple(ranked),
    )


def build_observation_digest(
    *,
    df: pd.DataFrame,
    semantic_kind: str,
    measure_column: str,
    time_column: str | None = None,
    dimension_columns: list[str] | None = None,
    additive: bool = False,
) -> ObservationValue:
    """Compute the bounded, shape-dispatched digest for an observation DataFrame.

    Payload size is independent of row count: segmented and panel shapes carry
    at most ``_TOP_SEGMENT_LIMIT`` top segments ranked by absolute value.
    ``additive`` gates composition semantics: ``total_value`` and ``share``
    (and panel ``top_segments``, which require cross-bucket sums) are only
    populated when the metric is declared additive.
    """
    if semantic_kind == "time_series":
        return _time_series_digest(df, measure_column, time_column)
    if semantic_kind == "segmented":
        return _segmented_digest(df, measure_column, dimension_columns, additive=additive)
    if semantic_kind == "panel":
        return _panel_digest(df, measure_column, time_column, dimension_columns, additive=additive)
    return _scalar_digest(df, measure_column)


def extract_observation_digest_finding(
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
    window: dict[str, Any] | None = None,
    analysis_purpose: str | None = None,
    additive: bool = False,
    item_key_prefix: str | None = None,
    unit: str | None = None,
) -> Finding:
    """Build the single observation digest finding for a metric_frame commit.

    Emitted for every shape (scalar / time_series / segmented / panel); it is
    the typed source for the bounded artifact digest. ``additive`` gates
    composition fields; see ``build_observation_digest``. When
    ``item_key_prefix`` is provided
    (multi-measure frames), it is prepended to the ``canonical_item_key`` so
    digest findings from different measures do not collide.
    """
    digest = build_observation_digest(
        df=df,
        semantic_kind=semantic_kind,
        measure_column=measure_column,
        time_column=time_column,
        dimension_columns=dimension_columns,
        additive=additive,
    )
    if unit is not None:
        digest = digest.model_copy(update={"unit": unit})
    canonical_item_key = f"{item_key_prefix}:digest" if item_key_prefix else "digest"
    return Finding(
        finding_id=make_finding_id(artifact_id, "observation", canonical_item_key),
        finding_type="observation",
        epistemic_kind="observed",
        artifact_id=artifact_id,
        session_id=session_id,
        subject=subject,
        canonical_item_key=canonical_item_key,
        value=ObservationFindingValue(row_count=len(df), value=digest),
        derivation=DerivationRule(
            rule_id="extract.observation_aggregate",
            rule_version="v2",
            operator="observe",
            source_fields=tuple(str(column) for column in df.columns),
            source_finding_refs=(),
        ),
        source_refs=(artifact_id,),
        committed_at=committed_at,
    )


__all__ = [
    "build_observation_digest",
    "extract_metric_value_findings",
    "extract_observation_digest_finding",
]
