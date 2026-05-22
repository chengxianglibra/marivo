"""Typed response models for intent execution APIs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from marivo.contracts.envelope import StepRef
from marivo.contracts.generated import aoi


class _EnvelopeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: str
    step_type: str
    step_ref: StepRef
    artifact_id: str
    provenance: dict[str, Any] | None = None
    product_metadata: dict[str, Any] | None = None


class _ObserveFailureArtifact(aoi.Artifact2):
    result: None = None


class ObserveResponse(_EnvelopeBase):
    result: aoi.MetricFrameArtifact | _ObserveFailureArtifact


class _CompareFailureArtifact(aoi.Artifact2):
    result: aoi.DeltaFrameArtifact | None = None


class CompareResponse(_EnvelopeBase):
    result: aoi.DeltaFrameArtifact | _CompareFailureArtifact


class DecomposeResponse(_EnvelopeBase):
    result: aoi.AttributionFrameArtifact | aoi.Artifact2


class _CorrelateArtifact(aoi.Artifact1):
    result: aoi.AssociationResult


class _CorrelateFailureArtifact(aoi.Artifact2):
    result: aoi.AssociationResult | None = None


class CorrelateResponse(_EnvelopeBase):
    result: _CorrelateArtifact | _CorrelateFailureArtifact


class _DetectFailureArtifact(aoi.Artifact2):
    result: None = None


class DetectResponse(_EnvelopeBase):
    result: (
        aoi.PointAnomalyCandidateSetArtifact
        | aoi.PeriodShiftCandidateSetArtifact
        | _DetectFailureArtifact
    )


class _ForecastArtifact(aoi.Artifact1):
    result: aoi.ForecastSeriesResult


class _ForecastFailureArtifact(aoi.Artifact2):
    result: aoi.ForecastSeriesResult | None = None


class ForecastResponse(_EnvelopeBase):
    result: _ForecastArtifact | _ForecastFailureArtifact


class _TestArtifact(aoi.Artifact1):
    result: aoi.HypothesisTestResult


class _TestFailureArtifact(aoi.Artifact2):
    result: aoi.HypothesisTestResult | None = None


class TestResponse(_EnvelopeBase):
    result: _TestArtifact | _TestFailureArtifact


class _SampleSummaryFailureArtifact(aoi.Artifact2):
    result: None = None


class SampleSummaryResponse(_EnvelopeBase):
    result: aoi.SampleFrameArtifact | _SampleSummaryFailureArtifact


class DerivedBundleResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    bundle_type: str
    aoi_artifacts: list[
        aoi.MetricFrameArtifact
        | aoi.DeltaFrameArtifact
        | aoi.AttributionFrameArtifact
        | aoi.SampleFrameArtifact
        | aoi.PointAnomalyCandidateSetArtifact
        | aoi.PeriodShiftCandidateSetArtifact
        | aoi.Artifact1
        | aoi.Artifact2
    ]


class AttributeResponse(_EnvelopeBase):
    result: DerivedBundleResult


class DiagnoseResponse(_EnvelopeBase):
    result: DerivedBundleResult


class ValidateResponse(_EnvelopeBase):
    result: DerivedBundleResult


IntentPayload = dict[str, Any]
