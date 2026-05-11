"""Typed response models for intent execution APIs.

Currently all responses are RootModel[JsonObject] wrappers.
Migration path: once all intent handlers return ExecutionEnvelope,
these will become typed models with explicit step_ref, artifact_id,
and result fields. The wire format stays the same via to_legacy_dict().
"""

from __future__ import annotations

from pydantic import RootModel

from marivo.transports.http.models.json_contract import JsonObject


class ObserveResponse(RootModel[JsonObject]):
    """Response payload for observe intent execution."""


class CompareResponse(RootModel[JsonObject]):
    """Response payload for compare intent execution."""


class DecomposeResponse(RootModel[JsonObject]):
    """Response payload for decompose intent execution."""


class CorrelateResponse(RootModel[JsonObject]):
    """Response payload for correlate intent execution."""


class DetectResponse(RootModel[JsonObject]):
    """Response payload for detect intent execution."""


class IntentTestResponse(RootModel[JsonObject]):
    """Response payload for test intent execution."""


class ForecastResponse(RootModel[JsonObject]):
    """Response payload for forecast intent execution."""


class AttributeResponse(RootModel[JsonObject]):
    """Response payload for attribute intent execution."""


class DiagnoseResponse(RootModel[JsonObject]):
    """Response payload for diagnose intent execution."""


class ValidateResponse(RootModel[JsonObject]):
    """Response payload for validate intent execution."""


IntentPayload = JsonObject
