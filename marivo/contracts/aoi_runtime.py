"""Runtime-facing helpers around generated AOI contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict, ValidationError

from marivo.contracts.generated import aoi

AoiAtomicRequest: TypeAlias = (  # noqa: UP040 - mypy hook does not support PEP 695 yet.
    aoi.Compare
    | aoi.Decompose
    | aoi.Correlate
    | aoi.Detect
    | aoi.Test
    | aoi.Forecast
    | aoi.Observe1
    | aoi.Observe2
    | aoi.Observe3
)
AoiDerivedRequest: TypeAlias = aoi.Validate | aoi.Attribute  # noqa: UP040
AoiArtifact: TypeAlias = aoi.Artifact1 | aoi.Artifact2  # noqa: UP040


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
    request: AoiAtomicRequest | AoiDerivedRequest


@dataclass(frozen=True)
class AoiOperationDefinition:
    intent_type: str
    request_types: tuple[type[AoiAtomicRequest | AoiDerivedRequest], ...]


AOI_OPERATION_REGISTRY: dict[str, AoiOperationDefinition] = {
    "compare": AoiOperationDefinition("compare", (aoi.Compare,)),
    "correlate": AoiOperationDefinition("correlate", (aoi.Correlate,)),
    "decompose": AoiOperationDefinition("decompose", (aoi.Decompose,)),
    "detect": AoiOperationDefinition("detect", (aoi.Detect,)),
    "forecast": AoiOperationDefinition("forecast", (aoi.Forecast,)),
    "observe": AoiOperationDefinition(
        "observe",
        (aoi.Observe1, aoi.Observe2, aoi.Observe3),
    ),
    "test": AoiOperationDefinition("test", (aoi.Test,)),
}

AOI_DERIVED_OPERATION_REGISTRY: dict[str, AoiOperationDefinition] = {
    "attribute": AoiOperationDefinition("attribute", (aoi.Attribute,)),
    "validate": AoiOperationDefinition("validate", (aoi.Validate,)),
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


def validate_aoi_artifact(value: Any) -> AoiArtifact:
    if isinstance(value, (aoi.Artifact1, aoi.Artifact2)):
        value = value.model_dump(exclude_none=True)
    if not isinstance(value, Mapping):
        return aoi.Artifact2.model_validate(value)
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
    return artifact.model_dump(exclude_none=True)
