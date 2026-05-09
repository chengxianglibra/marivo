"""Typed response models for intent execution APIs."""

from __future__ import annotations

from pydantic import RootModel

from marivo.api.models.json_contract import JsonObject


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
