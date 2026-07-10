"""Bounded commit-time evidence summaries attached to analysis artifacts."""

from __future__ import annotations

from typing import Literal, get_args, get_origin

import pytest

from marivo.analysis.evidence.knowledge import (
    ArtifactEvidenceProjection,
    ArtifactEvidenceProjectionItem,
)
from marivo.analysis.evidence.summary import build_artifact_evidence_summary
from marivo.analysis.evidence.types import (
    ArtifactEvidenceItem,
    ArtifactEvidenceItemKind,
    ArtifactEvidenceSummary,
    AssociationSummary,
    AttributedDriver,
    ChangeFact,
    ForecastSummary,
    ObservationSummary,
    OpenAnomaly,
    OpenQuestion,
    ScalarObservationDigest,
    Subject,
    TestedHypothesis,
    TimeWindow,
)


def _literal_values(annotation: object) -> set[str]:
    values: set[str] = set()
    for member in get_args(annotation):
        if get_origin(member) is Literal:
            values.update(str(value) for value in get_args(member))
        else:
            values.add(str(member))
    return values


def test_artifact_evidence_item_kind_reuses_evidence_vocabularies() -> None:
    assert _literal_values(ArtifactEvidenceItemKind) == {
        "observation",
        "change",
        "driver",
        "tested_hypothesis",
        "forecast",
        "association",
        "anomaly",
        "question",
    }


def test_artifact_evidence_summary_is_frozen_and_json_round_trips() -> None:
    summary = ArtifactEvidenceSummary(
        finding_count=8,
        items=(
            ArtifactEvidenceItem(
                kind="change",
                statement="sales.revenue: direction=increase magnitude=20",
                status="validated",
                confidence=0.9,
            ),
        ),
        omitted_count=0,
    )

    assert ArtifactEvidenceSummary.model_validate_json(summary.model_dump_json()) == summary


def _change(*, item_id: str, magnitude: float, key: str) -> ArtifactEvidenceProjectionItem:
    return ArtifactEvidenceProjectionItem(
        canonical_item_key=key,
        value=ChangeFact(
            id=item_id,
            subject=Subject(metric="sales.revenue", analysis_axis="change"),
            status="validated",
            confidence=0.9,
            confidence_basis="seed_delta_direction_matches",
            latest_assessment_id=f"ass_{item_id}",
            direction="increase",
            magnitude=magnitude,
            comparison_basis="left_vs_right",
        ),
    )


def test_summary_keeps_raw_finding_count_independent_from_digest_items() -> None:
    observation = ObservationSummary(
        id="fnd_digest",
        subject=Subject(metric="sales.revenue", analysis_axis="scalar"),
        semantic_kind="scalar",
        row_count=100,
        digest=ScalarObservationDigest(value=118.7),
    )
    summary = build_artifact_evidence_summary(
        ArtifactEvidenceProjection(
            finding_count=100,
            items=(
                ArtifactEvidenceProjectionItem(
                    canonical_item_key="digest",
                    value=observation,
                ),
            ),
        )
    )

    assert summary.finding_count == 100
    assert len(summary.items) == 1
    assert summary.omitted_count == 0
    assert summary.items[0].kind == "observation"
    assert summary.items[0].status is None
    assert summary.items[0].confidence is None
    assert summary.items[0].statement == "sales.revenue: value=118.7 rows=100"


def test_summary_orders_changes_by_absolute_magnitude_then_key() -> None:
    summary = build_artifact_evidence_summary(
        ArtifactEvidenceProjection(
            finding_count=3,
            items=(
                _change(item_id="small", magnitude=2.0, key="b"),
                _change(item_id="large_b", magnitude=-10.0, key="b"),
                _change(item_id="large_a", magnitude=10.0, key="a"),
            ),
        )
    )

    assert [item.statement for item in summary.items] == [
        "sales.revenue: direction=increase magnitude=10 comparison=left_vs_right",
        "sales.revenue: direction=increase magnitude=10 comparison=left_vs_right",
        "sales.revenue: direction=increase magnitude=2 comparison=left_vs_right",
    ]


def test_summary_caps_high_level_items_and_reports_exact_omissions() -> None:
    projection = ArtifactEvidenceProjection(
        finding_count=40,
        items=tuple(
            _change(item_id=f"change_{index}", magnitude=float(index), key=f"k{index:02d}")
            for index in range(7)
        ),
    )

    summary = build_artifact_evidence_summary(projection)

    assert len(summary.items) == 5
    assert summary.omitted_count == 2


def test_empty_projection_produces_present_empty_summary() -> None:
    summary = build_artifact_evidence_summary(ArtifactEvidenceProjection(finding_count=0, items=()))
    assert summary == ArtifactEvidenceSummary(finding_count=0)


TypedSummaryValue = (
    AttributedDriver
    | TestedHypothesis
    | ForecastSummary
    | AssociationSummary
    | OpenAnomaly
    | OpenQuestion
)


@pytest.mark.parametrize(
    ("typed_value", "expected_kind"),
    [
        (
            AttributedDriver(
                id="driver",
                subject=Subject(metric="sales.revenue", analysis_axis="decomposition"),
                status="validated",
                confidence=0.8,
                confidence_basis="driver_basis",
                latest_assessment_id="ass_driver",
                dimension="country",
                dimension_keys={"country": "us"},
                contribution_value=42.0,
                contribution_share=0.6,
                contribution_role="primary_driver",
            ),
            "driver",
        ),
        (
            TestedHypothesis(
                id="test",
                subject=Subject(metric="sales.revenue", analysis_axis="scalar"),
                status="validated",
                confidence=0.95,
                confidence_basis="test_basis",
                latest_assessment_id="ass_test",
                hypothesis_family="difference",
                alternative="greater",
                method_family="t_test",
                alpha=0.05,
                p_value=0.004,
                reject_null=True,
            ),
            "tested_hypothesis",
        ),
        (
            ForecastSummary(
                id="forecast",
                subject=Subject(metric="sales.revenue", analysis_axis="forecast"),
                status="validated",
                confidence=0.7,
                confidence_basis="forecast_basis",
                latest_assessment_id="ass_forecast",
                forecast_window=TimeWindow(field="ds", start="2026-06-01", end="2026-06-07"),
                horizon_index=1,
                forecast_kind="interval",
                prediction_interval=[90.0, 120.0],
            ),
            "forecast",
        ),
        (
            AssociationSummary(
                id="association",
                subject=Subject(metric="sales.revenue", analysis_axis="correlation"),
                status="validated",
                confidence=0.8,
                confidence_basis="association_basis",
                latest_assessment_id="ass_association",
                left_subject={"metric": "sales.revenue"},
                right_subject={"metric": "sales.orders"},
                method_family="pearson",
                coefficient=0.82,
                lag=0.0,
                join_basis="date",
            ),
            "association",
        ),
        (
            OpenAnomaly(
                id="anomaly",
                subject=Subject(metric="sales.revenue", analysis_axis="anomaly"),
                status="pending",
                confidence=0.6,
                confidence_basis="anomaly_basis",
                latest_assessment_id="ass_anomaly",
            ),
            "anomaly",
        ),
        (
            OpenQuestion(
                id="question",
                subject=Subject(analysis_axis="scalar"),
                status="pending",
                confidence=None,
                confidence_basis="persistent_blocking_issue:sample_size_low",
                latest_assessment_id="",
                reason="persistent_blocking_issue",
            ),
            "question",
        ),
    ],
)
def test_summary_maps_typed_status_without_recommendations(
    typed_value: TypedSummaryValue,
    expected_kind: str,
) -> None:
    summary = build_artifact_evidence_summary(
        ArtifactEvidenceProjection(
            finding_count=1,
            items=(
                ArtifactEvidenceProjectionItem(
                    canonical_item_key=expected_kind,
                    value=typed_value,
                ),
            ),
        )
    )

    item = summary.items[0]
    assert item.kind == expected_kind
    assert item.status == typed_value.status
    assert item.confidence == typed_value.confidence
    assert "recommend" not in item.statement.lower()
    assert "next step" not in item.statement.lower()
    assert "\n" not in item.statement


def test_driver_forecast_and_anomaly_ordering_rules() -> None:
    driver_low = AttributedDriver(
        id="driver_low",
        subject=Subject(metric="sales.revenue", analysis_axis="decomposition"),
        status="validated",
        confidence=0.8,
        confidence_basis="driver_basis",
        latest_assessment_id="ass_driver_low",
        dimension="country",
        dimension_keys={"country": "jp"},
        contribution_value=-4.0,
        contribution_role="offsetting_factor",
    )
    driver_high = driver_low.model_copy(
        update={
            "id": "driver_high",
            "latest_assessment_id": "ass_driver_high",
            "dimension_keys": {"country": "us"},
            "contribution_value": 12.0,
            "contribution_role": "primary_driver",
        }
    )
    driver_summary = build_artifact_evidence_summary(
        ArtifactEvidenceProjection(
            finding_count=2,
            items=(
                ArtifactEvidenceProjectionItem(canonical_item_key="jp", value=driver_low),
                ArtifactEvidenceProjectionItem(canonical_item_key="us", value=driver_high),
            ),
        )
    )
    assert "contribution=12" in driver_summary.items[0].statement

    forecast_late = ForecastSummary(
        id="forecast_late",
        subject=Subject(metric="sales.revenue", analysis_axis="forecast"),
        status="validated",
        confidence=0.7,
        confidence_basis="forecast_basis",
        latest_assessment_id="ass_forecast_late",
        forecast_window=TimeWindow(field="ds", start="2026-06-08", end="2026-06-14"),
        horizon_index=2,
        forecast_kind="point",
    )
    forecast_early = forecast_late.model_copy(
        update={
            "id": "forecast_early",
            "latest_assessment_id": "ass_forecast_early",
            "horizon_index": 1,
        }
    )
    forecast_summary = build_artifact_evidence_summary(
        ArtifactEvidenceProjection(
            finding_count=2,
            items=(
                ArtifactEvidenceProjectionItem(canonical_item_key="h2", value=forecast_late),
                ArtifactEvidenceProjectionItem(canonical_item_key="h1", value=forecast_early),
            ),
        )
    )
    assert "horizon=1" in forecast_summary.items[0].statement

    anomaly_first = OpenAnomaly(
        id="anomaly_first",
        subject=Subject(
            metric="sales.revenue",
            slice={"candidate": "first"},
            analysis_axis="anomaly",
        ),
        status="pending",
        confidence=0.8,
        confidence_basis="candidate_score_order",
        latest_assessment_id="ass_anomaly_first",
    )
    anomaly_second = anomaly_first.model_copy(
        update={
            "id": "anomaly_second",
            "subject": anomaly_first.subject.model_copy(update={"slice": {"candidate": "second"}}),
            "latest_assessment_id": "ass_anomaly_second",
        }
    )
    anomaly_summary = build_artifact_evidence_summary(
        ArtifactEvidenceProjection(
            finding_count=2,
            items=(
                ArtifactEvidenceProjectionItem(canonical_item_key="z", value=anomaly_first),
                ArtifactEvidenceProjectionItem(canonical_item_key="a", value=anomaly_second),
            ),
        )
    )
    assert "candidate=first" in anomaly_summary.items[0].statement
