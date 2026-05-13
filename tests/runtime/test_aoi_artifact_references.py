from __future__ import annotations

from typing import Any

import pytest

from marivo.runtime.intents.forecast import run_forecast_intent


class FakeRuntime:
    def __init__(self, artifacts: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.artifacts = artifacts or {}
        self.resolved_artifact_ids: list[tuple[str, str]] = []
        self.committed_payload: dict[str, Any] | None = None

    def resolve_artifact_by_id(self, session_id: str, artifact_id: str) -> dict[str, Any] | None:
        self.resolved_artifact_ids.append((session_id, artifact_id))
        return self.artifacts.get((session_id, artifact_id))

    def commit_artifact_with_extraction(
        self,
        session_id: str,
        step_id: str,
        artifact_type: str,
        name: str,
        content: dict[str, Any],
        *,
        step_type: str | None = None,
        artifact_schema_version: str | None = None,
    ) -> str:
        self.committed_payload = content
        return "art_forecast"

    def insert_step(
        self,
        step_id: str,
        session_id: str,
        step_type: str,
        summary: str,
        result: dict[str, Any],
        *,
        provenance: dict[str, Any] | None = None,
        semantic_metadata: dict[str, Any] | None = None,
    ) -> None:
        pass


def _time_series_artifact() -> dict[str, Any]:
    return {
        "observation_type": "time_series",
        "metric": "view_time",
        "schema_version": "1.0",
        "granularity": "day",
        "time_scope": {"field": "event_time", "start": "2026-05-01", "end": "2026-05-03"},
        "analytical_metadata": {"timezone": "UTC", "data_complete": True},
        "series": [
            {"window": {"start": "2026-05-01", "end": "2026-05-02"}, "value": 10.0},
            {"window": {"start": "2026-05-02", "end": "2026-05-03"}, "value": 12.0},
        ],
    }


def test_forecast_resolves_source_by_artifact_id_and_returns_committed_id() -> None:
    runtime = FakeRuntime({("s1", "art_obs"): _time_series_artifact()})

    result = run_forecast_intent(
        runtime,
        "s1",
        {"source_artifact_id": "art_obs", "horizon": 1, "profile": "level"},
    )

    assert runtime.resolved_artifact_ids == [("s1", "art_obs")]
    assert result["artifact_id"] == "art_forecast"
    assert result["source_ref"]["artifact_id"] == "art_obs"


def test_forecast_missing_source_artifact_id_raises_artifact_not_found() -> None:
    runtime = FakeRuntime()

    with pytest.raises(ValueError, match="ARTIFACT_NOT_FOUND"):
        run_forecast_intent(
            runtime,
            "s1",
            {"source_artifact_id": "missing", "horizon": 1, "profile": "level"},
        )
