"""Identity-safe evidence for funnel comparison and attribution artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import (
    DerivationRule,
    EventSubject,
    Finding,
    FunnelAttributionObservationValue,
    FunnelDeltaObservationValue,
    ObservationFindingValue,
)


def extract_funnel_delta_finding(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: EventSubject,
    committed_at: datetime,
    step_count: int,
    axis_count: int,
    zero_filled_tuple_count: int,
    current_coverage_basis: str,
    baseline_coverage_basis: str,
    source_refs: tuple[str, str],
) -> Finding:
    """Summarize exact funnel alignment without retaining driver or subject values."""
    required = {
        "step_key",
        "current_lost_count",
        "baseline_lost_count",
        "loss_rate_from_previous_delta",
    }
    if missing := required - set(df.columns):
        raise ValueError(f"funnel delta evidence requires columns {sorted(missing)}")
    value = FunnelDeltaObservationValue(
        step_count=step_count,
        axis_count=axis_count,
        zero_filled_tuple_count=zero_filled_tuple_count,
        current_coverage_basis=current_coverage_basis,
        baseline_coverage_basis=baseline_coverage_basis,
    )
    key = "funnel_delta_alignment"
    return Finding(
        finding_id=make_finding_id(artifact_id, "observation", key),
        finding_type="observation",
        epistemic_kind="observed",
        artifact_id=artifact_id,
        session_id=session_id,
        subject=subject,
        canonical_item_key=key,
        value=ObservationFindingValue(row_count=len(df), value=value),
        derivation=DerivationRule(
            rule_id="extract.funnel_delta",
            rule_version="v1",
            operator="compare.funnel",
            source_fields=tuple(sorted(required)),
            source_finding_refs=(),
        ),
        source_refs=source_refs,
        committed_at=committed_at,
    )


def extract_funnel_attribution_finding(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: EventSubject,
    committed_at: datetime,
    target_step_key: str,
    positive_pool: float,
    negative_pool: float,
    residual: float,
    reconciliation_status: Literal["reconciled"],
    source_delta_ref: str,
) -> Finding:
    """Summarize arithmetic contribution pools without retaining driver values."""
    required = {"contribution_kind", "contribution"}
    if missing := required - set(df.columns):
        raise ValueError(f"funnel attribution evidence requires columns {sorted(missing)}")
    value = FunnelAttributionObservationValue(
        target_step_key=target_step_key,
        contribution_count=len(df),
        positive_pool=positive_pool,
        negative_pool=negative_pool,
        residual=residual,
        reconciliation_status=reconciliation_status,
    )
    key = "funnel_loss_rate_contributions"
    return Finding(
        finding_id=make_finding_id(artifact_id, "observation", key),
        finding_type="observation",
        epistemic_kind="observed",
        artifact_id=artifact_id,
        session_id=session_id,
        subject=subject,
        canonical_item_key=key,
        value=ObservationFindingValue(row_count=len(df), value=value),
        derivation=DerivationRule(
            rule_id="extract.funnel_attribution",
            rule_version="v1",
            operator="attribute.funnel_loss_rate",
            source_fields=tuple(sorted(required)),
            source_finding_refs=(),
        ),
        source_refs=(source_delta_ref,),
        committed_at=committed_at,
    )


__all__ = [
    "extract_funnel_attribution_finding",
    "extract_funnel_delta_finding",
]
