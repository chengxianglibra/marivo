"""Tests for the current AOI validate derived intent runner contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marivo.runtime.intents.validate import run_validate_intent


def _valid_params() -> dict[str, Any]:
    return {
        "metric": "metric.test_metric",
        "current": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            }
        },
        "baseline": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-08T00:00:00Z",
                "end": "2026-01-15T00:00:00Z",
            }
        },
        "granularity": "day",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }


def _runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.core.metric_name_from_ref = MagicMock(return_value="test_metric")
    runtime.insert_artifact.return_value = "art-validate"
    return runtime


def _test_result(
    *,
    reject_null: bool | None = True,
    assumption_notes: list[str] | None = None,
    method: str = "welch_t",
) -> dict[str, Any]:
    return {
        "intent_type": "test",
        "step_type": "test",
        "step_ref": {"session_id": "session-1", "step_id": "step-test", "step_type": "test"},
        "artifact_id": "art-test",
        "result_type": "hypothesis_test",
        "kind": "numeric",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
            "alpha": 0.05,
        },
        "statistic": 2.1,
        "p_value": 0.04,
        "decision": {"reject_null": reject_null},
        "assumption_notes": assumption_notes or [],
        "method": method,
        "estimate": {"estimand": "mean_diff", "value": 10.0},
    }


def _run_with_mock_test(
    params: dict[str, Any] | None = None,
    *,
    observe_results: list[dict[str, Any]] | None = None,
    sample_results: list[dict[str, Any]] | None = None,
    test_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], MagicMock, MagicMock, MagicMock, MagicMock]:
    runtime = _runtime()
    params = deepcopy(params if params is not None else _valid_params())
    observe_results = observe_results or [
        {
            "intent_type": "observe",
            "step_type": "observe",
            "step_ref": {"session_id": "session-1", "step_id": "step-observe-current"},
            "artifact_id": "art_metric_current",
            "result": {
                "artifact_id": "art_metric_current",
                "result": {"result_type": "metric_frame"},
            },
        },
        {
            "intent_type": "observe",
            "step_type": "observe",
            "step_ref": {"session_id": "session-1", "step_id": "step-observe-baseline"},
            "artifact_id": "art_metric_baseline",
            "result": {
                "artifact_id": "art_metric_baseline",
                "result": {"result_type": "metric_frame"},
            },
        },
    ]
    sample_results = sample_results or [
        {
            "intent_type": "sample_summary",
            "step_type": "sample_summary",
            "step_ref": {"session_id": "session-1", "step_id": "step-sample-current"},
            "artifact_id": "art_sample_current",
            "result": {
                "artifact_id": "art_sample_current",
                "result": {"result_type": "sample_frame"},
            },
        },
        {
            "intent_type": "sample_summary",
            "step_type": "sample_summary",
            "step_ref": {"session_id": "session-1", "step_id": "step-sample-baseline"},
            "artifact_id": "art_sample_baseline",
            "result": {
                "artifact_id": "art_sample_baseline",
                "result": {"result_type": "sample_frame"},
            },
        },
    ]

    with (
        patch("marivo.runtime.intents.validate.run_observe_intent") as mock_observe,
        patch(
            "marivo.runtime.intents.validate.run_sample_summary_transform"
        ) as mock_sample_summary,
        patch("marivo.runtime.intents.validate.run_test_intent") as mock_test,
    ):
        mock_observe.side_effect = observe_results
        mock_sample_summary.side_effect = sample_results
        mock_test.return_value = test_result or _test_result()
        result = run_validate_intent(runtime, "session-1", params)

    return result, runtime, mock_observe, mock_sample_summary, mock_test


@pytest.mark.parametrize("alternative", ["two_sided", "greater", "less"])
def test_records_supported_alternatives(alternative: str) -> None:
    params = _valid_params()
    params["hypothesis"]["alternative"] = alternative

    result, _, _, _, mock_test = _run_with_mock_test(params)

    assert mock_test.call_args[0][2]["hypothesis"]["alternative"] == alternative
    assert result["result"]["hypothesis"]["alternative"] == alternative


@pytest.mark.parametrize(
    ("significance", "alpha"),
    [("conservative", 0.01), ("balanced", 0.05), ("aggressive", 0.10)],
)
def test_records_supported_significance_presets(significance: str, alpha: float) -> None:
    params = _valid_params()
    params["hypothesis"]["significance"] = significance

    result, _, _, _, mock_test = _run_with_mock_test(params)

    assert mock_test.call_args[0][2]["hypothesis"]["significance"] == significance
    assert result["result"]["hypothesis"]["significance"] == significance
    assert result["result"]["hypothesis"]["alpha"] == alpha


def test_forwards_filters_to_observe_and_bundle_payload() -> None:
    params = _valid_params()
    left_filter = {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]}
    right_filter = {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'CA'"}]}
    params["current"]["filter"] = left_filter
    params["baseline"]["filter"] = right_filter

    result, _, mock_observe, _, _ = _run_with_mock_test(params)

    delegated_current = mock_observe.call_args_list[0][0][2]
    delegated_baseline = mock_observe.call_args_list[1][0][2]
    assert delegated_current["filter"] == left_filter
    assert delegated_baseline["filter"] == right_filter
    assert delegated_current["granularity"] == "day"
    assert delegated_baseline["granularity"] == "day"
    assert "grain" not in delegated_current
    assert "grain" not in delegated_baseline
    assert result["result"]["current"]["filter"] == left_filter
    assert result["result"]["baseline"]["filter"] == right_filter
    assert result["result"]["granularity"] == "day"


@pytest.mark.parametrize("granularity", ["quarter", "year"])
def test_validate_accepts_time_granularity(granularity: str) -> None:
    params = _valid_params()
    params["granularity"] = granularity

    result, _, mock_observe, _, _ = _run_with_mock_test(params)

    assert mock_observe.call_args_list[0][0][2]["granularity"] == granularity
    assert mock_observe.call_args_list[1][0][2]["granularity"] == granularity
    assert "grain" not in mock_observe.call_args_list[0][0][2]
    assert "grain" not in mock_observe.call_args_list[1][0][2]
    assert result["result"]["granularity"] == granularity


@pytest.mark.parametrize(
    ("reject_null", "decision"),
    [(True, "reject_null"), (False, "fail_to_reject"), (None, "undetermined")],
)
def test_maps_test_decision_into_validation_result(
    reject_null: bool | None,
    decision: str,
) -> None:
    result, _, _, _, _ = _run_with_mock_test(test_result=_test_result(reject_null=reject_null))

    assert result["result"]["result"]["decision"] == decision


def test_returns_validation_bundle_envelope_with_underlying_test_artifact() -> None:
    result, runtime, _, _, _ = _run_with_mock_test()

    assert result["intent_type"] == "validate"
    assert result["step_type"] == "validate"
    assert result["artifact_id"] == "art-validate"
    assert result["result"]["bundle_type"] == "validation_bundle"
    assert result["result"]["granularity"] == "day"
    assert [artifact["artifact_id"] for artifact in result["result"]["aoi_artifacts"]] == [
        "art_metric_current",
        "art_metric_baseline",
        "art_sample_current",
        "art_sample_baseline",
        "art-test",
    ]
    assert result["result"]["refs"]["test_ref"] == {
        "step_type": "test",
        "session_id": "session-1",
        "step_id": "step-test",
        "artifact_id": "art-test",
        "result_type": "hypothesis_test",
    }
    assert result["product_metadata"]["status"] == "succeeded"
    assert result["product_metadata"]["issues"] == []
    runtime.insert_artifact.assert_called_once()
    runtime.insert_step.assert_called_once()


def test_degenerate_assumption_notes_mark_bundle_needs_attention() -> None:
    result, _, _, _, _ = _run_with_mock_test(
        test_result=_test_result(
            assumption_notes=["one or both groups have zero variance; result may be degenerate"]
        )
    )

    assert result["result"]["validation"]["status"] == "needs_attention"
    assert result["product_metadata"]["status"] == "needs_attention"
    assert result["product_metadata"]["issues"] == [
        {
            "code": "test_assumption_warning",
            "severity": "warning",
            "message": "one or both groups have zero variance; result may be degenerate",
            "source": "test",
        }
    ]


def test_test_failure_is_wrapped() -> None:
    runtime = _runtime()

    with (
        pytest.raises(ValueError, match="validate: TEST_FAILED"),
        patch(
            "marivo.runtime.intents.validate.run_observe_intent",
            side_effect=[
                {
                    "artifact_id": "art_metric_current",
                    "result": {"artifact_id": "art_metric_current"},
                },
                {
                    "artifact_id": "art_metric_baseline",
                    "result": {"artifact_id": "art_metric_baseline"},
                },
            ],
        ),
        patch(
            "marivo.runtime.intents.validate.run_sample_summary_transform",
            side_effect=[
                {
                    "artifact_id": "art_sample_current",
                    "result": {"artifact_id": "art_sample_current"},
                },
                {
                    "artifact_id": "art_sample_baseline",
                    "result": {"artifact_id": "art_sample_baseline"},
                },
            ],
        ),
        patch(
            "marivo.runtime.intents.validate.run_test_intent",
            side_effect=ValueError("sample summary failed"),
        ),
    ):
        run_validate_intent(runtime, "session-1", _valid_params())


@pytest.mark.parametrize(
    ("payload_patch", "message"),
    [
        (None, "params"),
        ({"__remove__": "metric"}, "metric"),
        ({"__remove__": "current"}, "current"),
        ({"__remove__": "baseline"}, "baseline"),
        ({"__remove__": "granularity"}, "granularity"),
        ({"__remove__": "hypothesis"}, "hypothesis"),
        ({"method": "welch_t"}, "method"),
        ({"kind": "numeric"}, "kind"),
        ({"metric": ""}, "metric"),
        ({"granularity": "minute"}, "granularity"),
        ({"granularity": None}, "granularity"),
        ({"current": []}, "current"),
        ({"baseline": []}, "baseline"),
        ({"current": {"scope": {"predicate": "region = 'US'"}}}, "scope"),
        ({"current": {"filter": None}}, "filter"),
        ({"current": {"__remove__": "time_scope"}}, "time_scope"),
        ({"baseline": {"__remove__": "time_scope"}}, "time_scope"),
        ({"hypothesis": None}, "hypothesis"),
        ({"hypothesis": {"family": "two_sample_proportion"}}, "family"),
        ({"hypothesis": {"alternative": "not_equal"}}, "alternative"),
        ({"hypothesis": {"significance": "loose"}}, "significance"),
        ({"hypothesis": {"family": None}}, "family"),
        ({"hypothesis": {"alternative": None}}, "alternative"),
        ({"hypothesis": {"significance": None}}, "significance"),
        ({"hypothesis": {"__remove__": "family"}}, "family"),
        ({"hypothesis": {"__remove__": "alternative"}}, "alternative"),
        ({"hypothesis": {"__remove__": "significance"}}, "significance"),
        ({"hypothesis": {"alpha": 0.05}}, "alpha"),
    ],
)
def test_rejects_non_current_request_shapes(
    payload_patch: dict[str, Any] | None,
    message: str,
) -> None:
    runtime = _runtime()
    params: dict[str, Any] | None = _valid_params()
    if payload_patch is None:
        params = None
    else:
        params = deepcopy(params)
        _merge_patch(params, payload_patch)

    with pytest.raises(ValueError, match=message):
        run_validate_intent(runtime, "session-1", params)


def test_validate_expands_through_sample_summary_before_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    calls: list[tuple[str, dict[str, Any]]] = []

    def _observe(_runtime: MagicMock, _session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(("observe", params))
        artifact_id = "art_metric_current" if len(calls) == 1 else "art_metric_baseline"
        return {"artifact_id": artifact_id, "result": {"artifact_id": artifact_id}}

    def _sample(_runtime: MagicMock, _session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(("sample_summary", params))
        artifact_id = "art_sample_current" if len(calls) == 3 else "art_sample_baseline"
        return {"artifact_id": artifact_id, "result": {"artifact_id": artifact_id}}

    def _test(_runtime: MagicMock, _session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(("test", params))
        return _test_result()

    monkeypatch.setattr("marivo.runtime.intents.validate.run_observe_intent", _observe)
    monkeypatch.setattr("marivo.runtime.intents.validate.run_sample_summary_transform", _sample)
    monkeypatch.setattr("marivo.runtime.intents.validate.run_test_intent", _test)

    result = run_validate_intent(runtime, "sess_1", _valid_params())

    assert [name for name, _ in calls] == [
        "observe",
        "observe",
        "sample_summary",
        "sample_summary",
        "test",
    ]
    assert calls[2][1] == {"source_artifact_id": "art_metric_current", "sample_kind": "numeric"}
    assert calls[3][1] == {"source_artifact_id": "art_metric_baseline", "sample_kind": "numeric"}
    assert calls[4][1]["current_sample_artifact_id"] == "art_sample_current"
    assert calls[4][1]["baseline_sample_artifact_id"] == "art_sample_baseline"
    assert calls[4][1]["hypothesis"] == _valid_params()["hypothesis"]
    assert result["result"]["aoi_artifacts"]


def _merge_patch(target: dict[str, Any], patch_value: dict[str, Any]) -> None:
    for key, value in patch_value.items():
        if key == "__remove__":
            target.pop(str(value))
            continue
        nested = target.get(key)
        if isinstance(value, dict) and isinstance(nested, dict):
            _merge_patch(nested, value)
        else:
            target[key] = value
