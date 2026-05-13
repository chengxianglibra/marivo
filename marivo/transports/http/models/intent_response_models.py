"""Typed response models for derived intent execution APIs."""

from __future__ import annotations

from pydantic import RootModel

from marivo.transports.http.models.json_contract import JsonObject


class AttributeResponse(RootModel[JsonObject]):
    """Response payload for attribute intent execution."""


class DiagnoseResponse(RootModel[JsonObject]):
    """Response payload for diagnose intent execution."""


class ValidateResponse(RootModel[JsonObject]):
    """Response payload for validate intent execution."""


IntentPayload = JsonObject
