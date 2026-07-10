"""Bounded display projection for evidence already typed by knowledge.py."""

from __future__ import annotations

from marivo.analysis.evidence.knowledge import (
    ArtifactEvidenceProjection,
    ArtifactEvidenceProjectionItem,
)
from marivo.analysis.evidence.types import (
    ArtifactEvidenceItem,
    ArtifactEvidenceSummary,
    AssociationSummary,
    AttributedDriver,
    ChangeFact,
    ForecastSummary,
    ObservationSummary,
    OpenAnomaly,
    OpenQuestion,
    PanelObservationDigest,
    ScalarObservationDigest,
    SegmentedObservationDigest,
    Subject,
    TestedHypothesis,
    TimeSeriesObservationDigest,
)

_MAX_ITEMS = 5


def _number(value: float | None) -> str:
    return "unknown" if value is None else f"{value:g}"


def _subject(subject: Subject) -> str:
    base = subject.metric or subject.entity or "subject"
    if not subject.slice:
        return base
    suffix = ",".join(f"{key}={subject.slice[key]}" for key in sorted(subject.slice))
    return f"{base}[{suffix}]"


def _statement(value: object) -> str:
    if isinstance(value, ObservationSummary):
        label = _subject(value.subject)
        digest = value.digest
        if isinstance(digest, ScalarObservationDigest):
            return f"{label}: value={_number(digest.value)} rows={value.row_count}"
        if isinstance(digest, TimeSeriesObservationDigest):
            return (
                f"{label}: buckets={digest.bucket_count} "
                f"{_number(digest.first_value)} -> {_number(digest.last_value)} "
                f"direction={digest.direction}"
            )
        if isinstance(digest, SegmentedObservationDigest):
            return f"{label}: segments={digest.segment_count} total={_number(digest.total_value)}"
        if isinstance(digest, PanelObservationDigest):
            return f"{label}: buckets={digest.bucket_count} segments={digest.segment_count}"
    if isinstance(value, ChangeFact):
        magnitude = abs(value.magnitude) if value.magnitude is not None else None
        return (
            f"{_subject(value.subject)}: direction={value.direction} "
            f"magnitude={_number(magnitude)} comparison={value.comparison_basis}"
        )
    if isinstance(value, AttributedDriver):
        keys = ",".join(
            f"{key}={value.dimension_keys[key]}" for key in sorted(value.dimension_keys)
        )
        return (
            f"{_subject(value.subject)}: {value.dimension}={keys or 'all'} "
            f"contribution={_number(value.contribution_value)} "
            f"share={_number(value.contribution_share)} role={value.contribution_role}"
        )
    if isinstance(value, TestedHypothesis):
        return (
            f"{_subject(value.subject)}: method={value.method_family} "
            f"p_value={_number(value.p_value)} reject_null={value.reject_null}"
        )
    if isinstance(value, ForecastSummary):
        interval = value.prediction_interval or []
        return (
            f"{_subject(value.subject)}: horizon={value.horizon_index} "
            f"window={value.forecast_window.start}..{value.forecast_window.end} "
            f"interval={interval}"
        )
    if isinstance(value, AssociationSummary):
        lag = value.lag_sweep.selected_lag if value.lag_sweep is not None else value.lag
        return (
            f"{_subject(value.subject)}: method={value.method_family} "
            f"coefficient={_number(value.coefficient)} lag={_number(lag)} "
            f"join={value.join_basis}"
        )
    if isinstance(value, OpenAnomaly):
        return f"{_subject(value.subject)}: anomaly status={value.status}"
    if isinstance(value, OpenQuestion):
        return f"{_subject(value.subject)}: question={value.reason} status={value.status}"
    raise TypeError(f"unsupported artifact evidence value: {type(value).__name__}")


def _order_key(
    indexed: tuple[int, ArtifactEvidenceProjectionItem],
) -> tuple[int, float, str]:
    index, item = indexed
    value = item.value
    if isinstance(value, ChangeFact):
        return (0, -abs(value.magnitude or 0.0), item.canonical_item_key)
    if isinstance(value, AttributedDriver):
        return (1, -abs(value.contribution_value or 0.0), item.canonical_item_key)
    if isinstance(value, ForecastSummary):
        return (2, float(value.horizon_index), item.canonical_item_key)
    if isinstance(value, OpenAnomaly):
        return (3, float(index), item.canonical_item_key)
    return (4, 0.0, item.canonical_item_key)


def _display_item(item: ArtifactEvidenceProjectionItem) -> ArtifactEvidenceItem:
    value = item.value
    if isinstance(value, ObservationSummary):
        return ArtifactEvidenceItem(kind="observation", statement=_statement(value))
    return ArtifactEvidenceItem(
        kind=value.kind,
        statement=_statement(value),
        status=value.status,
        confidence=value.confidence,
    )


def build_artifact_evidence_summary(
    projection: ArtifactEvidenceProjection,
) -> ArtifactEvidenceSummary:
    """Rank typed evidence and return its immutable five-item display snapshot."""
    ordered = [item for _, item in sorted(enumerate(projection.items), key=_order_key)]
    retained = ordered[:_MAX_ITEMS]
    return ArtifactEvidenceSummary(
        finding_count=projection.finding_count,
        items=tuple(_display_item(item) for item in retained),
        omitted_count=max(0, len(ordered) - len(retained)),
    )


__all__ = ["build_artifact_evidence_summary"]
