"""Pure rendering for persisted typed artifact digests."""

from __future__ import annotations

from marivo.analysis.evidence.types import (
    AnomalyCandidate,
    ArtifactDigest,
    ArtifactIssue,
    AssociationFact,
    ChangeFact,
    ComparabilityIssue,
    ContributionFact,
    DataQualityIssue,
    DigestItem,
    EventJourneyObservationValue,
    EventSubject,
    EvidenceAvailabilityIssue,
    ForecastOutput,
    ObservationFact,
    PanelObservationValue,
    QualityCheckResult,
    ScalarObservationValue,
    SegmentedObservationValue,
    TestDecision,
    TimeSeriesObservationValue,
)
from marivo.render import Card


def _number(value: float | None) -> str:
    return "not_computed" if value is None else f"{value:g}"


def _interval(value: tuple[float, float] | None) -> str:
    return "not_computed" if value is None else f"[{value[0]:g},{value[1]:g}]"


def _segments(value: SegmentedObservationValue | PanelObservationValue) -> str:
    if not value.top_segments:
        return "none"
    return ";".join(
        (
            f"{','.join(f'{key}={segment.keys[key]}' for key in sorted(segment.keys)) or 'all'}"
            f":value={_number(segment.value)},share={_number(segment.share)}"
        )
        for segment in value.top_segments
    )


def _subject(digest: ArtifactDigest) -> str:
    subject = digest.subject
    if isinstance(subject, EventSubject):
        return f"{subject.subject_entity_ref.path}[journey]"
    base = subject.metric or subject.entity or "subject"
    if not subject.slice:
        return base
    suffix = ",".join(f"{key}={subject.slice[key]}" for key in sorted(subject.slice))
    return f"{base}[{suffix}]"


def _quality(digest: ArtifactDigest) -> str | None:
    quality = digest.quality
    if quality is None:
        return None
    fields = (
        ("coverage", quality.coverage),
        ("null_rate", quality.null_rate),
        ("sample_size", quality.sample_size),
        ("metric_definition_compatibility", quality.metric_definition_compatibility),
        ("sample_coverage_min", quality.sample_coverage_min),
        ("sample_coverage_avg", quality.sample_coverage_avg),
        ("sample_coverage_partial_buckets", quality.sample_coverage_partial_buckets),
        ("zero_denominator_rows", quality.zero_denominator_rows),
        ("evaluated_check_count", quality.evaluated_check_count),
        ("failed_check_count", quality.failed_check_count),
        ("warning_check_count", quality.warning_check_count),
    )
    rendered = [f"{name}={value}" for name, value in fields if value is not None]
    return " ".join(rendered) if rendered else None


def render_artifact_issue(issue: ArtifactIssue) -> str:
    """Render one closed issue variant; prose is never canonical issue data."""
    if isinstance(issue, DataQualityIssue):
        return (
            f"check={issue.check_id} observed={issue.observed_value!r} "
            f"expectation={issue.expectation}"
        )
    if isinstance(issue, ComparabilityIssue):
        details = ", ".join(issue.approximation_details or issue.incompatible_fields)
        return details or "artifact scopes are not exactly comparable"
    if isinstance(issue, EvidenceAvailabilityIssue):
        return (
            f"stage={issue.failed_stage} error={issue.stable_error_category} "
            f"findings_available={str(issue.findings_available).lower()}"
        )
    raise TypeError(f"unsupported artifact issue type {type(issue).__name__}")


def render_digest_item(item: DigestItem) -> str:
    """Render one closed item variant without interpreting beyond its fields."""
    if isinstance(item, ObservationFact):
        value = item.value
        if isinstance(value, ScalarObservationValue):
            return f"observation value={_number(value.value)} unit={value.unit or 'unknown'} rows={item.row_count}"
        if isinstance(value, TimeSeriesObservationValue):
            return (
                f"observation buckets={value.bucket_count} first={_number(value.first_value)} "
                f"last={_number(value.last_value)} "
                f"endpoint_change_direction={value.endpoint_change_direction}"
            )
        if isinstance(value, SegmentedObservationValue):
            return (
                f"observation rows={item.row_count} segments={value.segment_count} "
                f"total={_number(value.total_value)} top_segments={_segments(value)}"
            )
        if isinstance(value, PanelObservationValue):
            return (
                f"observation rows={item.row_count} buckets={value.bucket_count} "
                f"segments={value.segment_count} total={_number(value.total_value)} "
                f"top_segments={_segments(value)}"
            )
        if isinstance(value, EventJourneyObservationValue):
            return (
                f"event_journey attempts={value.attempt_count} "
                f"complete={value.complete_count} incomplete={value.incomplete_count} "
                f"coverage_censored={value.coverage_censored_count} "
                f"unused_events={value.unused_event_count}"
            )
    if isinstance(item, ChangeFact):
        return (
            f"change current={_number(item.current)} baseline={_number(item.baseline)} "
            f"delta={_number(item.delta)} relative_delta={_number(item.relative_delta)} "
            f"direction={item.direction}"
        )
    if isinstance(item, ContributionFact):
        keys = ",".join(f"{key}={item.dimension_keys[key]}" for key in sorted(item.dimension_keys))
        return (
            f"contribution dimension={item.dimension} keys={keys or 'all'} "
            f"value={_number(item.contribution_value)} share={_number(item.contribution_share)} "
            f"rank={item.contribution_rank or 'not_computed'} "
            f"method={item.decomposition_method}"
        )
    if isinstance(item, AssociationFact):
        return (
            f"association method={item.method} coefficient={_number(item.coefficient)} "
            f"p_value={_number(item.p_value)} n={item.sample_size or 'not_computed'} "
            f"interval={_interval(item.confidence_interval)} "
            f"lag={_number(item.lag)} join={item.join_basis}"
        )
    if isinstance(item, TestDecision):
        return (
            f"test null={item.null_predicate} alternative={item.alternative} "
            f"method={item.method} statistic={_number(item.statistic)} "
            f"p_value={_number(item.p_value)} alpha={item.alpha:g} "
            f"effect={_number(item.effect_estimate)} interval={_interval(item.confidence_interval)} "
            f"n={item.sample_size or 'not_computed'} reject_null={item.reject_null}"
        )
    if isinstance(item, ForecastOutput):
        return (
            f"forecast bucket={item.bucket_start}..{item.bucket_end} "
            f"point={_number(item.predicted_value)} interval={_interval(item.prediction_interval)} "
            f"horizon={item.horizon_index} model={item.model}"
        )
    if isinstance(item, AnomalyCandidate):
        return (
            f"anomaly_candidate ref={item.candidate_ref} score={_number(item.score)} "
            f"detector={item.detector} threshold={_number(item.threshold)} rank={item.rank} "
            f"current={_number(item.current_value)} baseline={_number(item.baseline_value)} "
            f"deviation={_number(item.deviation_absolute)} "
            f"relative_deviation={_number(item.deviation_relative)} "
            f"flag_level={item.flag_level or 'not_computed'}"
        )
    if isinstance(item, QualityCheckResult):
        return (
            f"quality_check id={item.check_id} measured={item.measured_value} "
            f"predicate={item.expectation_predicate} "
            f"expectation_condition_passed={item.expectation_condition_passed}"
        )
    raise TypeError(f"unsupported digest item: {type(item).__name__}")


def render_artifact_digest(digest: ArtifactDigest, *, max_output_bytes: int | None = 8_000) -> str:
    """Render a digest using only its persisted typed content."""
    card = Card(
        identity=(
            f"ArtifactDigest ref={digest.artifact_ref} operator={digest.operator.operator} "
            f"subject={_subject(digest)}"
        ),
        available=(".contract()",),
    )
    card.field(
        "evidence",
        (
            f"items={len(digest.items)} omitted={digest.omissions.omitted_items} "
            f"fingerprint={digest.fingerprint}"
        ),
    )
    if digest.items:
        card.listing("items", (render_digest_item(item) for item in digest.items))
    else:
        card.field("items", "no typed findings emitted")
    if digest.boundaries:
        card.listing(
            "inference boundaries",
            (
                f"{boundary.kind}: reason={boundary.reason} "
                f"required={','.join(boundary.required_evidence)}"
                for boundary in digest.boundaries
            ),
        )
    quality = _quality(digest)
    if quality is not None:
        card.field("quality context", quality)
    card.field(
        "fallback",
        (
            f"findings_available={digest.fallback.findings_available} "
            f"rows_available={digest.fallback.rows_available} "
            f"when={','.join(digest.fallback.recommended_when) or 'none'}"
        ),
    )
    return card.render(max_output_bytes=max_output_bytes)


__all__ = ["render_artifact_digest", "render_digest_item"]
