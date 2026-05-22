from __future__ import annotations

from datetime import UTC, datetime

import pytest

from marivo.contracts.aoi_projection import project_aoi_artifact_result
from marivo.contracts.generated import aoi
from marivo.runtime import intent_execution


def _observe_request() -> aoi.Observe:
    return aoi.Observe(
        metric="view_time",
        time_scope=aoi.TimeScope(
            field="event_time",
            start=datetime(2026, 5, 1, tzinfo=UTC),
            end=datetime(2026, 5, 8, tzinfo=UTC),
        ),
        granularity="day",
    )


def _detect_request() -> aoi.Detect:
    return aoi.Detect(
        metric="view_time",
        time_scope=aoi.TimeScope(
            field="event_time",
            start=datetime(2026, 5, 1, tzinfo=UTC),
            end=datetime(2026, 5, 8, tzinfo=UTC),
        ),
        granularity="day",
        filter=aoi.Expression(
            dialects=[aoi.Dialect(dialect="ANSI_SQL", expression="region = 'US'")]
        ),
        dimension="region",
        strategy="period_shift",
        sensitivity="balanced",
        limit=5,
    )


def _decompose_request() -> aoi.Decompose:
    return aoi.Decompose(
        compare_artifact_id="artifact-compare",
        dimension="region",
        limit=5,
    )


def test_project_decompose_result_returns_attribution_frame_artifact() -> None:
    projected = project_aoi_artifact_result(
        "decompose",
        {
            "artifact_id": "art_attr",
            "artifact_family": "attribution_frame",
            "shape": "ranked_contributions",
            "subject": {
                "kind": "comparison",
                "metric_ref": "metric.revenue",
                "current": {
                    "time_scope": {
                        "field": "time",
                        "start": "2024-01-08T00:00:00Z",
                        "end": "2024-01-15T00:00:00Z",
                    },
                    "scope": {},
                },
                "baseline": {
                    "time_scope": {
                        "field": "time",
                        "start": "2024-01-01T00:00:00Z",
                        "end": "2024-01-08T00:00:00Z",
                    },
                    "scope": {},
                },
            },
            "axes": [{"kind": "dimension", "name": "channel"}],
            "measures": [
                {"id": "contribution_abs", "value_type": "number", "nullable": False},
                {"id": "contribution_pct", "value_type": "number", "nullable": True},
            ],
            "capabilities": ["filterable"],
            "lineage": {"operation": "decompose", "source_artifact_ids": ["art_cmp"]},
            "payload": {
                "series": [
                    {
                        "keys": {"channel": "paid"},
                        "points": [
                            {
                                "contribution_abs": 12.0,
                                "contribution_pct": 0.6,
                                "rank": 1,
                            }
                        ],
                    }
                ],
                "scope": {
                    "current_value": 120.0,
                    "baseline_value": 100.0,
                    "delta_abs": 20.0,
                    "delta_pct": 0.2,
                    "direction": "increase",
                },
                "quality": {"reconciliation_status": "within_tolerance"},
            },
        },
    )

    assert projected["artifact_family"] == "attribution_frame"
    assert projected["shape"] == "ranked_contributions"
    assert projected["payload"]["series"][0]["points"][0]["contribution_abs"] == 12.0


def _validate_request() -> aoi.Validate:
    return aoi.Validate(
        metric="view_time",
        current=aoi.Slice(
            time_scope=aoi.TimeScope(
                field="event_time",
                start=datetime(2026, 5, 1, tzinfo=UTC),
                end=datetime(2026, 5, 8, tzinfo=UTC),
            )
        ),
        baseline=aoi.Slice(
            time_scope=aoi.TimeScope(
                field="event_time",
                start=datetime(2026, 4, 24, tzinfo=UTC),
                end=datetime(2026, 5, 1, tzinfo=UTC),
            )
        ),
        grain="day",
        hypothesis=aoi.Hypothesis(
            family="two_sample_mean",
            alternative="greater",
            significance="balanced",
        ),
    )


def _diagnose_request() -> aoi.Diagnose:
    return aoi.Diagnose(
        metric="view_time",
        time_scope=aoi.TimeScope(
            field="event_time",
            start=datetime(2026, 5, 1, tzinfo=UTC),
            end=datetime(2026, 5, 8, tzinfo=UTC),
        ),
        granularity="day",
        dimensions=["region"],
        strategy="point_anomaly",
        candidate_limit=2,
    )


def _forecast_request() -> aoi.Forecast:
    return aoi.Forecast(source_artifact_id="artifact-source", horizon=14)


def test_observe_accepts_aoi_request_and_dispatches_lowered_params(monkeypatch) -> None:
    runtime = object()
    calls: list[tuple[object, str, dict[str, object]]] = []
    expected = {"status": "ok"}

    monkeypatch.setattr(intent_execution, "_assert_session_is_open", lambda *_: None)

    def runner(runtime_arg, session_id, params, *, reasoning=None):
        calls.append((runtime_arg, session_id, params))
        return expected

    monkeypatch.setitem(intent_execution.AOI_RUNNERS, "observe", runner)

    result = intent_execution.observe(runtime, "s1", _observe_request())

    assert result is expected
    assert calls == [
        (
            runtime,
            "s1",
            {
                "metric": "view_time",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-05-01T00:00:00Z",
                    "end": "2026-05-08T00:00:00Z",
                },
                "filter": None,
                "granularity": "day",
            },
        )
    ]


def test_compare_rejects_mismatched_aoi_request_before_runner(monkeypatch) -> None:
    runtime = object()
    called = False
    request = aoi.Forecast(source_artifact_id="artifact_1", horizon=7)

    monkeypatch.setattr(intent_execution, "_assert_session_is_open", lambda *_: None)

    def runner(*_):
        nonlocal called
        called = True
        return {"status": "ok"}

    monkeypatch.setitem(intent_execution.AOI_RUNNERS, "compare", runner)

    with pytest.raises(ValueError, match="AOI_OPERATION_MISMATCH"):
        intent_execution.compare(runtime, "s1", request)

    assert called is False


def test_detect_accepts_aoi_request_and_dispatches_lowered_params(monkeypatch) -> None:
    runtime = object()
    calls: list[tuple[object, str, dict[str, object]]] = []
    expected = {"status": "ok"}

    monkeypatch.setattr(intent_execution, "_assert_session_is_open", lambda *_: None)

    def runner(runtime_arg, session_id, params, *, reasoning=None):
        calls.append((runtime_arg, session_id, params))
        return expected

    monkeypatch.setitem(intent_execution.AOI_RUNNERS, "detect", runner)

    result = intent_execution.detect(runtime, "s1", _detect_request())

    assert result is expected
    assert calls == [
        (
            runtime,
            "s1",
            {
                "metric": "view_time",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-05-01T00:00:00Z",
                    "end": "2026-05-08T00:00:00Z",
                },
                "granularity": "day",
                "filter": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]},
                "dimension": "region",
                "strategy": "period_shift",
                "sensitivity": "balanced",
                "limit": 5,
            },
        )
    ]


def test_decompose_accepts_aoi_request_and_dispatches_lowered_params(monkeypatch) -> None:
    runtime = object()
    calls: list[tuple[object, str, dict[str, object]]] = []
    expected = {"status": "ok"}

    monkeypatch.setattr(intent_execution, "_assert_session_is_open", lambda *_: None)

    def runner(runtime_arg, session_id, params, *, reasoning=None):
        calls.append((runtime_arg, session_id, params))
        return expected

    monkeypatch.setitem(intent_execution.AOI_RUNNERS, "decompose", runner)

    result = intent_execution.decompose(runtime, "s1", _decompose_request())

    assert result is expected
    assert calls == [
        (
            runtime,
            "s1",
            {
                "compare_artifact_id": "artifact-compare",
                "dimension": "region",
                "limit": 5,
            },
        )
    ]


def test_forecast_accepts_aoi_request_and_dispatches_lowered_params(monkeypatch) -> None:
    runtime = object()
    calls: list[tuple[object, str, dict[str, object]]] = []
    expected = {"status": "ok"}

    monkeypatch.setattr(intent_execution, "_assert_session_is_open", lambda *_: None)

    def runner(runtime_arg, session_id, params, *, reasoning=None):
        calls.append((runtime_arg, session_id, params))
        return expected

    monkeypatch.setitem(intent_execution.AOI_RUNNERS, "forecast", runner)

    result = intent_execution.forecast(runtime, "s1", _forecast_request())

    assert result is expected
    assert calls == [
        (
            runtime,
            "s1",
            {
                "source_artifact_id": "artifact-source",
                "horizon": 14,
            },
        )
    ]


def test_validate_accepts_aoi_request_and_dispatches_lowered_params(monkeypatch) -> None:
    runtime = object()
    calls: list[tuple[object, str, dict[str, object]]] = []
    expected = {"status": "ok"}

    monkeypatch.setattr(intent_execution, "_assert_session_is_open", lambda *_: None)

    def runner(runtime_arg, session_id, params, *, reasoning=None):
        calls.append((runtime_arg, session_id, params))
        return expected

    monkeypatch.setitem(intent_execution.DERIVED_RUNNERS, "validate", runner)

    result = intent_execution.validate(runtime, "s1", _validate_request())

    assert result is expected
    assert calls == [
        (
            runtime,
            "s1",
            {
                "metric": "view_time",
                "current": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-05-01T00:00:00Z",
                        "end": "2026-05-08T00:00:00Z",
                    }
                },
                "baseline": {
                    "time_scope": {
                        "field": "event_time",
                        "start": "2026-04-24T00:00:00Z",
                        "end": "2026-05-01T00:00:00Z",
                    }
                },
                "grain": "day",
                "hypothesis": {
                    "family": "two_sample_mean",
                    "alternative": "greater",
                    "significance": "balanced",
                },
            },
        )
    ]


def test_diagnose_accepts_aoi_request_and_dispatches_lowered_params(monkeypatch) -> None:
    runtime = object()
    calls: list[tuple[object, str, dict[str, object]]] = []
    expected = {"status": "ok"}

    monkeypatch.setattr(intent_execution, "_assert_session_is_open", lambda *_: None)

    def runner(runtime_arg, session_id, params, *, reasoning=None):
        calls.append((runtime_arg, session_id, params))
        return expected

    monkeypatch.setitem(intent_execution.DERIVED_RUNNERS, "diagnose", runner)

    result = intent_execution.diagnose(runtime, "s1", _diagnose_request())

    assert result is expected
    assert calls == [
        (
            runtime,
            "s1",
            {
                "metric": "view_time",
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-05-01T00:00:00Z",
                    "end": "2026-05-08T00:00:00Z",
                },
                "granularity": "day",
                "dimensions": ["region"],
                "strategy": "point_anomaly",
                "sensitivity": "aggressive",
                "candidate_limit": 2,
                "decomposition_limit": 5,
            },
        )
    ]
