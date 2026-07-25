"""Extract identity-safe observations from SubjectSet artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import pandas as pd

from marivo.analysis.evidence.identity import make_finding_id
from marivo.analysis.evidence.types import (
    DerivationRule,
    Finding,
    ObservationFindingValue,
    SubjectSetObservationValue,
    SubjectSetSubject,
)


def extract_subject_set_finding(
    *,
    df: pd.DataFrame,
    artifact_id: str,
    session_id: str,
    subject: SubjectSetSubject,
    committed_at: datetime,
    excluded_coverage_censored_count: int,
    coverage_status: Literal["ready", "coverage_censored"],
    source_refs: tuple[str, ...],
) -> Finding:
    """Summarize selection counts without retaining selected identities."""
    if tuple(df.columns) != ("subject_identity",):
        raise ValueError("SubjectSet evidence requires only subject_identity rows")
    if coverage_status not in {"ready", "coverage_censored"}:
        raise ValueError("SubjectSet evidence requires a closed coverage status")
    value = SubjectSetObservationValue(
        selected_count=len(df),
        excluded_coverage_censored_count=excluded_coverage_censored_count,
        coverage_status=coverage_status,
    )
    canonical_item_key = "subject_selection"
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
            rule_id="extract.subject_set",
            rule_version="v1",
            operator="select_subjects",
            source_fields=("subject_identity",),
            source_finding_refs=(),
        ),
        source_refs=source_refs,
        committed_at=committed_at,
    )


__all__ = ["extract_subject_set_finding"]
