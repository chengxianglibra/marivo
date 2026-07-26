"""Extract one bounded identity-safe observation from a Lifecycle artifact."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import (
    DerivationRule,
    Finding,
    LifecycleDistributionObservationValue,
    LifecycleDwellObservationValue,
    LifecycleHistoryObservationValue,
    LifecycleSubject,
    LifecycleTransitionsObservationValue,
    LifecycleViolationsObservationValue,
    ObservationFindingValue,
)
from marivo.analysis.frames.lifecycle import (
    LifecycleDistributionFrameMeta,
    LifecycleDwellFrameMeta,
    LifecycleFrameMetaVariant,
    LifecycleHistoryFrameMeta,
    LifecycleTransitionsFrameMeta,
    LifecycleViolationsFrameMeta,
)


def _distribution_reconciles(
    df: pd.DataFrame,
    meta: LifecycleDistributionFrameMeta,
) -> bool:
    required = {
        *(axis.output_column for axis in meta.axes),
        "as_of",
        "model_state",
        "subject_count",
        "share",
    }
    if not required.issubset(df.columns):
        return False
    states = tuple(item.state.name for item in meta.states)
    for instant in meta.at:
        current = df.loc[df["as_of"] == instant]
        counts = pd.to_numeric(current["subject_count"], errors="coerce")
        if counts.isna().any() or bool((counts < 0).any()):
            return False
        if int(counts.sum()) != meta.known_subject_counts[instant]:
            return False
        if set(current["model_state"]) != set(states):
            return False
    return True


def extract_lifecycle_finding(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: LifecycleSubject,
    committed_at: datetime,
    meta: LifecycleFrameMetaVariant,
) -> Finding:
    """Summarize one closed Lifecycle shape without retaining identity values."""

    source_refs: tuple[str, ...]
    source_fields: tuple[str, ...]
    if isinstance(meta, LifecycleHistoryFrameMeta):
        shape = "history"
        counts = df["interval_status"].astype(str).value_counts()
        value: (
            LifecycleHistoryObservationValue
            | LifecycleDistributionObservationValue
            | LifecycleTransitionsObservationValue
            | LifecycleDwellObservationValue
            | LifecycleViolationsObservationValue
        ) = LifecycleHistoryObservationValue(
            population_count=meta.population_count,
            seeded_subject_count=meta.seeded_subject_count,
            coverage_censored_subject_count=meta.coverage_censored_subject_count,
            interval_count=len(df),
            completed_interval_count=int(counts.get("completed", 0)),
            right_censored_interval_count=int(counts.get("right_censored", 0)),
            coverage_censored_interval_count=int(counts.get("coverage_censored", 0)),
            violation_count=meta.violation_count,
        )
        source_fields = ("model_state", "interval_status")
        source_refs = tuple(sorted(meta.event_fingerprints))
    elif isinstance(meta, LifecycleDistributionFrameMeta):
        shape = "distribution"
        value = LifecycleDistributionObservationValue(
            instant_count=len(meta.at),
            state_count=len(meta.states),
            row_count=len(df),
            grouped=bool(meta.axes),
            reconciliation_passed=_distribution_reconciles(df, meta),
        )
        source_fields = ("as_of", "model_state", "subject_count", "share")
        source_refs = (meta.source_history_ref,)
    elif isinstance(meta, LifecycleTransitionsFrameMeta):
        shape = "transitions"
        counts = pd.to_numeric(df["transition_count"], errors="raise")
        value = LifecycleTransitionsObservationValue(
            modeled_pair_count=len(df),
            transition_count=int(counts.sum()),
            nonzero_pair_count=int((counts > 0).sum()),
        )
        source_fields = (
            "from_model_state",
            "to_model_state",
            "transition_count",
        )
        source_refs = (meta.source_history_ref,)
    elif isinstance(meta, LifecycleDwellFrameMeta):
        shape = "dwell"
        value = LifecycleDwellObservationValue(
            state_count=len(df),
            interval_count=int(df["interval_count"].sum()),
            completed_count=int(df["completed_count"].sum()),
            right_censored_count=int(df["right_censored_count"].sum()),
            coverage_censored_count=int(df["coverage_censored_count"].sum()),
        )
        source_fields = (
            "model_state",
            "interval_count",
            "completed_count",
            "right_censored_count",
            "coverage_censored_count",
        )
        source_refs = (meta.source_history_ref,)
    elif isinstance(meta, LifecycleViolationsFrameMeta):
        shape = "violations"
        counts = df["violation_kind"].astype(str).value_counts()
        value = LifecycleViolationsObservationValue(
            violation_count=len(df),
            illegal_transition_count=int(counts.get("illegal_transition", 0)),
            transition_from_terminal_count=int(counts.get("transition_from_terminal", 0)),
        )
        source_fields = ("model_state_at_event", "violation_kind")
        source_refs = (meta.source_history_ref,)
    else:
        raise TypeError(f"unsupported Lifecycle evidence metadata {type(meta).__name__!r}")

    canonical_item_key = f"lifecycle_{shape}"
    return Finding(
        finding_id=make_finding_id(artifact_id, "observation", canonical_item_key),
        finding_type="observation",
        epistemic_kind="observed",
        artifact_id=artifact_id,
        session_id=session_id,
        subject=subject,
        canonical_item_key=canonical_item_key,
        value=ObservationFindingValue(row_count=len(df), value=value),
        derivation=DerivationRule(
            rule_id=f"extract.lifecycle_{shape}",
            rule_version="v1",
            operator=f"lifecycle.{shape if shape != 'history' else 'replay'}",
            source_fields=source_fields,
            source_finding_refs=(),
        ),
        source_refs=source_refs,
        committed_at=committed_at,
    )


__all__ = ["extract_lifecycle_finding"]
