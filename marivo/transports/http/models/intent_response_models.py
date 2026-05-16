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


class _ObserveArtifact(aoi.Artifact1):
    result: (
        aoi.ScalarObservationResult
        | aoi.TimeSeriesObservationResult
        | aoi.SegmentedObservationResult
    )


class _ObserveFailureArtifact(aoi.Artifact2):
    result: (
        aoi.ScalarObservationResult
        | aoi.TimeSeriesObservationResult
        | aoi.SegmentedObservationResult
        | None
    ) = None


class ObserveResponse(_EnvelopeBase):
    result: _ObserveArtifact | _ObserveFailureArtifact


class _CompareArtifact(aoi.Artifact1):
    result: aoi.ScalarDeltaResult | aoi.TimeSeriesDeltaResult | aoi.SegmentedDeltaResult


class _CompareFailureArtifact(aoi.Artifact2):
    result: aoi.ScalarDeltaResult | aoi.TimeSeriesDeltaResult | aoi.SegmentedDeltaResult | None = (
        None
    )


class CompareResponse(_EnvelopeBase):
    result: _CompareArtifact | _CompareFailureArtifact


class _DecomposeArtifact(aoi.Artifact1):
    result: aoi.DeltaDecompositionResult


class _DecomposeFailureArtifact(aoi.Artifact2):
    result: aoi.DeltaDecompositionResult | None = None


class DecomposeResponse(_EnvelopeBase):
    result: _DecomposeArtifact | _DecomposeFailureArtifact


class _CorrelateArtifact(aoi.Artifact1):
    result: aoi.AssociationResult


class _CorrelateFailureArtifact(aoi.Artifact2):
    result: aoi.AssociationResult | None = None


class CorrelateResponse(_EnvelopeBase):
    result: _CorrelateArtifact | _CorrelateFailureArtifact


class _DetectArtifact(aoi.Artifact1):
    result: aoi.AnomalyCandidatesResult


class _DetectFailureArtifact(aoi.Artifact2):
    result: aoi.AnomalyCandidatesResult | None = None


class DetectResponse(_EnvelopeBase):
    result: _DetectArtifact | _DetectFailureArtifact


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


class DerivedBundleResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    bundle_type: str
    aoi_artifacts: list[aoi.Artifact1 | aoi.Artifact2]


class AttributeResponse(_EnvelopeBase):
    result: DerivedBundleResult


class DiagnoseResponse(_EnvelopeBase):
    result: DerivedBundleResult


class ValidateResponse(_EnvelopeBase):
    result: DerivedBundleResult


IntentPayload = dict[str, Any]
