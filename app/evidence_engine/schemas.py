from __future__ import annotations

from typing import Any, TypedDict


class ObservationSubject(TypedDict):
    metric: str
    slice: dict[str, Any]


class Observation(TypedDict):
    observation_id: str
    type: str
    subject: ObservationSubject
    payload: dict[str, Any]
    significance: dict[str, Any]
    quality: dict[str, Any]


class Claim(TypedDict):
    claim_id: str
    type: str
    text: str
    scope: dict[str, Any]
    confidence: float
    status: str
    supporting_observations: list[str]
    contradicting_observations: list[str]
    confidence_breakdown: dict[str, Any]


class Recommendation(TypedDict):
    rec_id: str
    claim_id: str
    action_text: str
    priority: str
    expected_impact: str
    risk: str
    validation_metric: dict[str, Any]
