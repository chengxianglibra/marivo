"""Runtime-facing helpers around generated AOI contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, ValidationError

from marivo.contracts.generated import aoi

AoiAtomicRequest: TypeAlias = (  # noqa: UP040 - mypy hook does not support PEP 695 yet.
    aoi.Compare | aoi.Decompose | aoi.Correlate | aoi.Detect | aoi.Test | aoi.Forecast | aoi.Observe
)
AoiDerivedRequest: TypeAlias = aoi.Validate | aoi.Attribute | aoi.Diagnose  # noqa: UP040
AoiTransformRequest: TypeAlias = aoi.SampleSummary  # noqa: UP040
AoiArtifact = (
    aoi.MetricFrameArtifact
    | aoi.SampleFrameArtifact
    | aoi.DeltaFrameArtifact
    | aoi.AttributionFrameArtifact
    | aoi.CandidateSetArtifact
    | aoi.Artifact1
    | aoi.Artifact2
)


class _CanonicalSuccessArtifactShape(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: Any
    result: Any


class _CanonicalFailureArtifactShape(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: Any
    failure: Any


class RuntimeIntentEnvelope(BaseModel):
    """Marivo runtime metadata around a generated AOI request."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    session_id: str
    actor: str | None = None
    request: AoiAtomicRequest | AoiDerivedRequest | AoiTransformRequest


@dataclass(frozen=True)
class AoiOperationDefinition:
    intent_type: str
    request_types: tuple[type[AoiAtomicRequest | AoiDerivedRequest | AoiTransformRequest], ...]


AOI_OPERATION_REGISTRY: dict[str, AoiOperationDefinition] = {
    "compare": AoiOperationDefinition("compare", (aoi.Compare,)),
    "correlate": AoiOperationDefinition("correlate", (aoi.Correlate,)),
    "decompose": AoiOperationDefinition("decompose", (aoi.Decompose,)),
    "detect": AoiOperationDefinition("detect", (aoi.Detect,)),
    "forecast": AoiOperationDefinition("forecast", (aoi.Forecast,)),
    "observe": AoiOperationDefinition(
        "observe",
        (aoi.Observe,),
    ),
    "test": AoiOperationDefinition("test", (aoi.Test,)),
}

AOI_DERIVED_OPERATION_REGISTRY: dict[str, AoiOperationDefinition] = {
    "attribute": AoiOperationDefinition("attribute", (aoi.Attribute,)),
    "diagnose": AoiOperationDefinition("diagnose", (aoi.Diagnose,)),
    "validate": AoiOperationDefinition("validate", (aoi.Validate,)),
}

AOI_TRANSFORM_OPERATION_REGISTRY: dict[str, AoiOperationDefinition] = {
    "sample_summary": AoiOperationDefinition("sample_summary", (aoi.SampleSummary,)),
}


def assert_request_matches_intent(
    intent_type: str,
    request: AoiAtomicRequest,
) -> None:
    definition = AOI_OPERATION_REGISTRY.get(intent_type)
    if definition is None:
        raise ValueError(f"AOI_OPERATION_UNKNOWN: {intent_type}")
    if not isinstance(request, definition.request_types):
        raise ValueError(
            f"AOI_OPERATION_MISMATCH: intent_type={intent_type} "
            f"request_type={type(request).__name__}"
        )


def assert_derived_request_matches_intent(
    intent_type: str,
    request: AoiDerivedRequest,
) -> None:
    definition = AOI_DERIVED_OPERATION_REGISTRY.get(intent_type)
    if definition is None:
        raise ValueError(f"AOI_DERIVED_OPERATION_UNKNOWN: {intent_type}")
    if not isinstance(request, definition.request_types):
        raise ValueError(
            f"AOI_DERIVED_OPERATION_MISMATCH: intent_type={intent_type} "
            f"request_type={type(request).__name__}"
        )


def assert_transform_request_matches_operation(
    operation_type: str,
    request: AoiTransformRequest,
) -> None:
    definition = AOI_TRANSFORM_OPERATION_REGISTRY.get(operation_type)
    if definition is None:
        raise ValueError(f"AOI_TRANSFORM_OPERATION_UNKNOWN: {operation_type}")
    if not isinstance(request, definition.request_types):
        raise ValueError(
            f"AOI_TRANSFORM_OPERATION_MISMATCH: operation_type={operation_type} "
            f"request_type={type(request).__name__}"
        )


def validate_aoi_artifact(value: Any) -> AoiArtifact:
    if isinstance(
        value,
        (
            aoi.MetricFrameArtifact,
            aoi.SampleFrameArtifact,
            aoi.DeltaFrameArtifact,
            aoi.AttributionFrameArtifact,
            aoi.CandidateSetArtifact,
            aoi.Artifact1,
            aoi.Artifact2,
        ),
    ):
        value = value.model_dump(mode="json")
        if isinstance(value, dict):
            if value.get("result") is None:
                value.pop("result", None)
            if value.get("failure") is None:
                value.pop("failure", None)
    if not isinstance(value, Mapping):
        return aoi.Artifact2.model_validate(value)
    if value.get("artifact_family") == "metric_frame":
        return aoi.MetricFrameArtifact.model_validate(value)
    if value.get("artifact_family") == "sample_frame":
        return aoi.SampleFrameArtifact.model_validate(value)
    if value.get("artifact_family") == "delta_frame":
        return aoi.DeltaFrameArtifact.model_validate(value)
    if value.get("artifact_family") == "attribution_frame":
        return aoi.AttributionFrameArtifact.model_validate(value)
    if value.get("artifact_family") == "candidate_set":
        return aoi.CandidateSetArtifact.model_validate(value)
    if "result" in value and "failure" not in value:
        _CanonicalSuccessArtifactShape.model_validate(value)
        return aoi.Artifact1.model_validate(value)
    if "failure" in value and "result" not in value:
        _CanonicalFailureArtifactShape.model_validate(value)
        return aoi.Artifact2.model_validate(value)
    raise ValidationError.from_exception_data(
        "AoiArtifact",
        [
            {
                "type": "value_error",
                "loc": (),
                "input": value,
                "ctx": {"error": ValueError("invalid AOI artifact shape")},
            }
        ],
    )


def artifact_to_envelope_result(artifact: AoiArtifact) -> dict[str, Any]:
    data = artifact.model_dump(mode="json")
    if data.get("artifact_family") in (
        "metric_frame",
        "sample_frame",
        "delta_frame",
        "candidate_set",
    ):
        payload = data.get("payload")
        if isinstance(payload, dict):
            for series in payload.get("series") or []:
                if not isinstance(series, dict):
                    continue
                for point in series.get("points") or []:
                    if isinstance(point, dict) and point.get("window") is None:
                        point.pop("window", None)
        return data
    if data.get("result") is None:
        data.pop("result", None)
    if data.get("failure") is None:
        data.pop("failure", None)
    return data
