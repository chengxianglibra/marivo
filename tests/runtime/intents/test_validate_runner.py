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
        "grain": "day",
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
    test_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], MagicMock, MagicMock]:
    runtime = _runtime()
    params = deepcopy(params if params is not None else _valid_params())

    with patch("marivo.runtime.intents.validate.run_test_intent") as mock_test:
        mock_test.return_value = test_result or _test_result()
        result = run_validate_intent(runtime, "session-1", params)

    return result, runtime, mock_test


@pytest.mark.parametrize("alternative", ["two_sided", "greater", "less"])
def test_records_supported_alternatives(alternative: str) -> None:
    params = _valid_params()
    params["hypothesis"]["alternative"] = alternative

    result, _, mock_test = _run_with_mock_test(params)

    assert mock_test.call_args[0][2]["hypothesis"]["alternative"] == alternative
    assert mock_test.call_args[0][2]["grain"] == "day"
    assert result["result"]["hypothesis"]["alternative"] == alternative


@pytest.mark.parametrize(
    ("significance", "alpha"),
    [("conservative", 0.01), ("balanced", 0.05), ("aggressive", 0.10)],
)
def test_records_supported_significance_presets(significance: str, alpha: float) -> None:
    params = _valid_params()
    params["hypothesis"]["significance"] = significance

    result, _, mock_test = _run_with_mock_test(params)

    assert mock_test.call_args[0][2]["hypothesis"]["significance"] == significance
    assert result["result"]["hypothesis"]["significance"] == significance
    assert result["result"]["hypothesis"]["alpha"] == alpha


def test_forwards_filters_to_test_and_bundle_payload() -> None:
    params = _valid_params()
    left_filter = {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]}
    right_filter = {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'CA'"}]}
    params["current"]["filter"] = left_filter
    params["baseline"]["filter"] = right_filter

    result, _, mock_test = _run_with_mock_test(params)

    delegated_params = mock_test.call_args[0][2]
    assert delegated_params["current"]["filter"] == left_filter
    assert delegated_params["baseline"]["filter"] == right_filter
    assert delegated_params["grain"] == "day"
    assert result["result"]["current"]["filter"] == left_filter
    assert result["result"]["baseline"]["filter"] == right_filter
    assert result["result"]["grain"] == "day"


@pytest.mark.parametrize("grain", ["quarter", "year"])
def test_validate_accepts_time_granularity_grain(grain: str) -> None:
    params = _valid_params()
    params["grain"] = grain

    result, _, mock_test = _run_with_mock_test(params)

    assert mock_test.call_args[0][2]["grain"] == grain
    assert result["result"]["grain"] == grain


@pytest.mark.parametrize(
    ("reject_null", "decision"),
    [(True, "reject_null"), (False, "fail_to_reject"), (None, "undetermined")],
)
def test_maps_test_decision_into_validation_result(
    reject_null: bool | None,
    decision: str,
) -> None:
    result, _, _ = _run_with_mock_test(test_result=_test_result(reject_null=reject_null))

    assert result["result"]["result"]["decision"] == decision


def test_returns_validation_bundle_envelope_with_underlying_test_artifact() -> None:
    result, runtime, _ = _run_with_mock_test()

    assert result["intent_type"] == "validate"
    assert result["step_type"] == "validate"
    assert result["artifact_id"] == "art-validate"
    assert result["result"]["bundle_type"] == "validation_bundle"
    assert result["result"]["grain"] == "day"
    assert result["result"]["aoi_artifacts"] == [
        {
            "artifact_id": "art-test",
            "result": {
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
                "decision": {"reject_null": True},
                "assumption_notes": [],
                "method": "welch_t",
                "estimate": {"estimand": "mean_diff", "value": 10.0},
            },
        }
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
    result, _, _ = _run_with_mock_test(
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
        ({"__remove__": "grain"}, "grain"),
        ({"__remove__": "hypothesis"}, "hypothesis"),
        ({"method": "welch_t"}, "method"),
        ({"kind": "numeric"}, "kind"),
        ({"metric": ""}, "metric"),
        ({"grain": "minute"}, "grain"),
        ({"grain": None}, "grain"),
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
