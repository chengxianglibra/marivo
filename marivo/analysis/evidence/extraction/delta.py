"""Extract delta findings from a DeltaFrame DataFrame."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from datetime import datetime
from typing import Any, Literal, cast

import pandas as pd

from marivo.analysis._cumulative import (
    ALL_HISTORY_LEVEL_CHANGE_SCHEMA,
    BASELINE_EVALUATION_END_COLUMN,
    CURRENT_EVALUATION_END_COLUMN,
    AllHistoryLevelChangeSchema,
    AllHistoryLevelChangeV1,
    AllHistoryPairAlignmentV1,
    CumulativePairSummaryV1,
)
from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import DeltaFindingValue, DerivationRule, Finding, Subject


def _to_float(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _classify_direction(
    delta: float | None, current: float | None, baseline: float | None
) -> Literal["increase", "decrease", "flat", "undefined"]:
    if delta is None:
        return "undefined"
    if delta > 0:
        return "increase"
    if delta < 0:
        return "decrease"
    return "flat"


def _presence(
    current: float | None, baseline: float | None
) -> Literal["current_only", "baseline_only"] | None:
    if current is not None and baseline is None:
        return "current_only"
    if current is None and baseline is not None:
        return "baseline_only"
    return None


def _timestamp_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat()


def _row_presence(
    row: pd.Series,
    current: float | None,
    baseline: float | None,
) -> Literal["current_only", "baseline_only"] | None:
    if (
        CURRENT_EVALUATION_END_COLUMN in row.index
        and BASELINE_EVALUATION_END_COLUMN in row.index
        and pd.notna(row[CURRENT_EVALUATION_END_COLUMN])
        and pd.notna(row[BASELINE_EVALUATION_END_COLUMN])
    ):
        return None
    return _presence(current, baseline)


def _cumulative_pair_evidence(
    pairs: AllHistoryPairAlignmentV1 | CumulativePairSummaryV1 | None,
) -> dict[str, Any]:
    """Project the canonical alignment counters into persisted evidence."""

    if pairs is None:
        return {}
    payload: dict[str, Any] = {
        "matched_rows": pairs.matched_rows,
        "matched_null_rows": pairs.matched_null_rows,
        "current_unpaired_rows": pairs.current_unpaired_rows,
        "baseline_unpaired_rows": pairs.baseline_unpaired_rows,
        "unpaired_action": pairs.unpaired_action,
    }
    if isinstance(pairs, CumulativePairSummaryV1):
        payload["fallback_rows"] = pairs.fallback_rows
    return payload


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


def _delta_kind(
    semantic_kind: str,
) -> Literal["scalar_delta", "segmented_delta", "time_series_delta", "panel_delta"]:
    return cast(
        "Literal['scalar_delta', 'segmented_delta', 'time_series_delta', 'panel_delta']",
        {
            "scalar": "scalar_delta",
            "segmented": "segmented_delta",
            "time_series": "time_series_delta",
            "panel": "panel_delta",
        }[semantic_kind],
    )


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
    cumulative_pairs: AllHistoryPairAlignmentV1 | CumulativePairSummaryV1 | None = None,
    cumulative_change: AllHistoryLevelChangeV1 | None = None,
) -> list[Finding]:
    """Extract delta findings from a comparison DataFrame.

    Supports scalar, segmented, time-series, and panel semantic kinds.
    """
    if df.empty:
        return []
    delta_kind = _delta_kind(semantic_kind)
    pair_evidence = _cumulative_pair_evidence(cumulative_pairs)
    cumulative_change_schema: AllHistoryLevelChangeSchema | None = (
        ALL_HISTORY_LEVEL_CHANGE_SCHEMA if cumulative_change is not None else None
    )
    source_revision: Literal["unverified"] | None = (
        "unverified" if cumulative_change is not None else None
    )
    interval_flow_equivalence: Literal["not_asserted"] | None = (
        "not_asserted" if cumulative_change is not None else None
    )

    if semantic_kind == "scalar":
        row = df.iloc[0]
        current = _to_float(row.get("current"))
        baseline = _to_float(row.get("baseline"))
        delta_val = _to_float(row.get("delta"))
        pct = _to_float(row.get("pct_change"))
        canonical_item_key = "value"
        return [
            Finding(
                finding_id=make_finding_id(artifact_id, "delta", canonical_item_key),
                finding_type="delta",
                epistemic_kind="algebraic",
                artifact_id=artifact_id,
                session_id=session_id,
                subject=subject,
                canonical_item_key=canonical_item_key,
                value=DeltaFindingValue(
                    delta_kind=delta_kind,
                    current=current,
                    baseline=baseline,
                    magnitude=delta_val,
                    relative_delta=pct,
                    relative_delta_undefined_reason=(
                        "baseline_zero_or_missing"
                        if pct is None and delta_val is not None
                        else None
                    ),
                    direction=_classify_direction(delta_val, current, baseline),
                    presence=_row_presence(row, current, baseline),
                    unit=unit,
                    current_evaluation_end=_timestamp_text(row.get(CURRENT_EVALUATION_END_COLUMN)),
                    baseline_evaluation_end=_timestamp_text(
                        row.get(BASELINE_EVALUATION_END_COLUMN)
                    ),
                    **pair_evidence,
                    cumulative_change=cumulative_change_schema,
                    source_revision=source_revision,
                    interval_flow_equivalence=interval_flow_equivalence,
                ),
                derivation=DerivationRule(
                    rule_id="extract.delta",
                    rule_version="v2",
                    operator="compare",
                    source_fields=tuple(
                        field
                        for field in (
                            "current",
                            "baseline",
                            "delta",
                            "pct_change",
                            CURRENT_EVALUATION_END_COLUMN,
                            BASELINE_EVALUATION_END_COLUMN,
                        )
                        if field in row.index
                    ),
                    source_finding_refs=(),
                ),
                source_refs=(artifact_id,),
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
                    epistemic_kind="algebraic",
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=subject,
                    canonical_item_key=canonical_item_key,
                    value=DeltaFindingValue(
                        delta_kind=delta_kind,
                        current=current,
                        baseline=baseline,
                        magnitude=delta_val,
                        relative_delta=pct,
                        relative_delta_undefined_reason=(
                            "baseline_zero_or_missing"
                            if pct is None and delta_val is not None
                            else None
                        ),
                        direction=_classify_direction(delta_val, current, baseline),
                        presence=_row_presence(row, current, baseline),
                        unit=unit,
                        dimension_keys={k: str(v) for k, v in keys.items()},
                        current_evaluation_end=_timestamp_text(
                            row.get(CURRENT_EVALUATION_END_COLUMN)
                        ),
                        baseline_evaluation_end=_timestamp_text(
                            row.get(BASELINE_EVALUATION_END_COLUMN)
                        ),
                        **pair_evidence,
                        cumulative_change=cumulative_change_schema,
                        source_revision=source_revision,
                        interval_flow_equivalence=interval_flow_equivalence,
                    ),
                    derivation=DerivationRule(
                        rule_id="extract.delta",
                        rule_version="v2",
                        operator="compare",
                        source_fields=(
                            *dimension_columns,
                            "current",
                            "baseline",
                            "delta",
                            "pct_change",
                            *(
                                (CURRENT_EVALUATION_END_COLUMN, BASELINE_EVALUATION_END_COLUMN)
                                if CURRENT_EVALUATION_END_COLUMN in row.index
                                and BASELINE_EVALUATION_END_COLUMN in row.index
                                else ()
                            ),
                        ),
                        source_finding_refs=(),
                    ),
                    source_refs=(artifact_id,),
                    committed_at=committed_at,
                )
            )
        return findings

    if semantic_kind in {"time_series", "panel"}:
        if time_column is None:
            raise ValueError(f"{semantic_kind} delta extraction requires time_column")
        if semantic_kind == "panel" and not dimension_columns:
            raise ValueError("panel delta extraction requires dimension_columns")
        findings = []
        for _, row in df.iterrows():
            keys = {column: row[column] for column in (dimension_columns or [])}
            bucket = _timestamp_text(row.get(time_column)) or str(row.get(time_column))
            stable_parts = [f"{key}={_escape_seg_component(keys[key])}" for key in sorted(keys)]
            stable_parts.append(f"bucket={_escape_seg_component(bucket)}")
            canonical_item_key = "rows:" + "|".join(stable_parts)
            current = _to_float(row.get("current"))
            baseline = _to_float(row.get("baseline"))
            delta_val = _to_float(row.get("delta"))
            pct = _to_float(row.get("pct_change"))
            findings.append(
                Finding(
                    finding_id=make_finding_id(artifact_id, "delta", canonical_item_key),
                    finding_type="delta",
                    epistemic_kind="algebraic",
                    artifact_id=artifact_id,
                    session_id=session_id,
                    subject=subject,
                    canonical_item_key=canonical_item_key,
                    value=DeltaFindingValue(
                        delta_kind=delta_kind,
                        current=current,
                        baseline=baseline,
                        magnitude=delta_val,
                        relative_delta=pct,
                        relative_delta_undefined_reason=(
                            "baseline_zero_or_missing"
                            if pct is None and delta_val is not None
                            else None
                        ),
                        direction=_classify_direction(delta_val, current, baseline),
                        presence=_row_presence(row, current, baseline),
                        unit=unit,
                        dimension_keys={key: str(value) for key, value in keys.items()},
                        bucket=bucket,
                        current_evaluation_end=_timestamp_text(
                            row.get(CURRENT_EVALUATION_END_COLUMN)
                        ),
                        baseline_evaluation_end=_timestamp_text(
                            row.get(BASELINE_EVALUATION_END_COLUMN)
                        ),
                        **pair_evidence,
                        cumulative_change=cumulative_change_schema,
                        source_revision=source_revision,
                        interval_flow_equivalence=interval_flow_equivalence,
                    ),
                    derivation=DerivationRule(
                        rule_id="extract.delta",
                        rule_version="v3",
                        operator="compare",
                        source_fields=(
                            *(dimension_columns or []),
                            time_column,
                            "current",
                            "baseline",
                            "delta",
                            "pct_change",
                            *(
                                (CURRENT_EVALUATION_END_COLUMN, BASELINE_EVALUATION_END_COLUMN)
                                if CURRENT_EVALUATION_END_COLUMN in row.index
                                and BASELINE_EVALUATION_END_COLUMN in row.index
                                else ()
                            ),
                        ),
                        source_finding_refs=(),
                    ),
                    source_refs=(artifact_id,),
                    committed_at=committed_at,
                )
            )
        return findings

    return []


__all__ = ["extract_delta_findings"]
