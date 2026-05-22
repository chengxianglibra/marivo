from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marivo.runtime.intents.attribute import run_attribute_intent
from marivo.runtime.intents.metric_frame import (
    build_attribution_frame_artifact,
    build_metric_frame_artifact,
)


def _make_runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: metric
    runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
    resolved_metric = MagicMock()
    resolved_metric.semantic_object = {
        "header": {
            "decomposition_semantics": "sum",
        }
    }
    runtime.resolve_metric.return_value = resolved_metric
    runtime.insert_artifact.return_value = "art_attribute_001"
    runtime.insert_step.return_value = None
    return runtime


def _params() -> dict[str, Any]:
    return {
        "metric": "metric.revenue",
        "current": {
            "time_scope": {
                "field": "event_date",
                "start": "2026-01-08T00:00:00Z",
                "end": "2026-01-15T00:00:00Z",
            },
        },
        "baseline": {
            "time_scope": {
                "field": "event_date",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            },
        },
        "dimensions": ["channel", "region"],
    }


def _filter(expression: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]}


def _observe_result(
    side: str, *, time_scope: dict[str, Any] | None = None, scope: dict[str, Any] | None = None
) -> dict[str, Any]:
    artifact = build_metric_frame_artifact(
        artifact_id=f"art_{side}",
        shape="scalar",
        metric_ref="metric.revenue",
        time_scope=time_scope or {"field": "time", "start": "2026-01-01", "end": "2026-01-08"},
        scope=scope or {},
        axes=[],
        series=[{"keys": {}, "points": [{"value": 42.0}]}],
        unit=None,
    )
    return {
        "artifact_id": f"art_{side}",
        "step_ref": {
            "session_id": "sess_attr",
            "step_id": f"step_{side}",
            "step_type": "observe",
        },
        "result": artifact,
        "product_metadata": {"observe_metadata": {"row_count": 10}},
    }


def _compare_result(
    *,
    comparability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    series_data = [
        {
            "keys": {},
            "points": [
                {
                    "current_value": 120.0,
                    "baseline_value": 100.0,
                    "delta_abs": 20.0,
                    "delta_pct": 0.2,
                    "direction": "increase",
                }
            ],
        }
    ]
    return {
        "artifact_id": "art_compare",
        "step_ref": {
            "session_id": "sess_attr",
            "step_id": "step_compare",
            "step_type": "compare",
        },
        "comparability": comparability or {"status": "comparable", "issues": []},
        "schema_version": "2.0",
        "artifact_family": "delta_frame",
        "shape": "scalar_delta",
        "comparison_type": "scalar_delta",  # transition alias
        "axes": [{"kind": "comparison_side"}],
        "subject": {
            "kind": "comparison",
            "metric_ref": "metric.revenue",
        },
        "measures": [
            {"id": "delta_abs", "value_type": "number", "nullable": True, "unit": None},
            {"id": "delta_pct", "value_type": "number", "nullable": True, "unit": None},
        ],
        "payload": {"series": series_data},
        # Backward-compatible top-level series for read_compare_scalar_point
        "series": series_data,
        # Backward-compatible aliases
        "current_value": 120.0,
        "baseline_value": 100.0,
        "absolute_delta": 20.0,
        "relative_delta": 0.2,
        "direction": "increase",
    }


def _decompose_result(
    dimension: str,
    *,
    rows: list[dict[str, Any]] | None = None,
    attribution: dict[str, Any] | None = None,
    scope_absolute_delta: float | None = 20.0,
    unexplained_reason: str | None = None,
) -> dict[str, Any]:
    default_rows = [
        {dimension: "A", "contribution_abs": 12.0, "contribution_pct": 0.6},
        {dimension: "B", "contribution_abs": 8.0, "contribution_pct": 0.4},
    ]
    flat_rows = rows if rows is not None else default_rows
    series_entries = [
        {
            "keys": {dimension: row[dimension]},
            "points": [{k: v for k, v in row.items() if k != dimension}],
        }
        for row in flat_rows
    ]
    artifact = build_attribution_frame_artifact(
        artifact_id=f"art_decompose_{dimension}",
        metric_ref="metric.revenue",
        dimension=dimension,
        subject={"kind": "comparison", "metric_ref": "metric.revenue"},
        series=series_entries,
        scope={
            "current_value": 120.0,
            "baseline_value": 100.0,
            "delta_abs": scope_absolute_delta,
            "delta_pct": 0.2,
            "direction": "increase",
        },
        quality={
            "reconciliation_status": "within_tolerance",
            "unexplained_delta_abs": 0.0,
            "unexplained_pct": 0.0,
        },
        lineage={"operation": "decompose", "source_artifact_ids": ["art_compare"]},
    )
    result: dict[str, Any] = {
        **artifact,
        "artifact_id": f"art_decompose_{dimension}",
        "step_ref": {
            "session_id": "sess_attr",
            "step_id": f"step_decompose_{dimension}",
            "step_type": "decompose",
        },
        "schema_version": "2.0",
        "attribution": attribution or {"status": "attributable", "issues": []},
    }
    if unexplained_reason is not None:
        result["unexplained_reason"] = unexplained_reason
    return result


def _run_with_patched_children(
    params: dict[str, Any],
    *,
    runtime: MagicMock | None = None,
    compare_result: dict[str, Any] | None = None,
    decompose_results: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], MagicMock, MagicMock, MagicMock, MagicMock]:
    runtime = runtime or _make_runtime()
    decompose_results = decompose_results or [
        _decompose_result("channel"),
        _decompose_result("region"),
    ]
    observe_sides = iter(["current", "baseline"])

    def _observe_side_effect(
        _runtime: MagicMock, _session_id: str, observe_params: dict[str, Any]
    ) -> dict[str, Any]:
        return _observe_result(
            next(observe_sides),
            time_scope=observe_params.get("time_scope"),
            scope=observe_params.get("scope") or {},
        )

    with (
        patch(
            "marivo.runtime.intents.attribute.run_observe_intent",
            side_effect=_observe_side_effect,
        ) as mock_observe,
        patch(
            "marivo.runtime.intents.attribute.run_compare_intent",
            return_value=compare_result or _compare_result(),
        ) as mock_compare,
        patch(
            "marivo.runtime.intents.attribute.run_decompose_intent",
            side_effect=decompose_results,
        ) as mock_decompose,
    ):
        result = run_attribute_intent(runtime, "sess_attr", params)
    return result, runtime, mock_observe, mock_compare, mock_decompose


def test_attribute_expands_child_runners_and_commits_bundle_in_request_order() -> None:
    params = _params()
    params["decomposition_limit"] = 2

    result, runtime, mock_observe, mock_compare, mock_decompose = _run_with_patched_children(params)

    assert mock_observe.call_count == 2
    assert [call.args[2]["scope"] for call in mock_observe.call_args_list] == [None, None]
    mock_compare.assert_called_once_with(
        runtime,
        "sess_attr",
        {"current_artifact_id": "art_current", "baseline_artifact_id": "art_baseline"},
    )
    assert [call.args[2]["dimension"] for call in mock_decompose.call_args_list] == [
        "channel",
        "region",
    ]
    runtime.insert_artifact.assert_called_once()
    runtime.insert_step.assert_called_once()
    assert result["intent_type"] == "attribute"
    assert result["artifact_id"] == "art_attribute_001"
    assert result["result"]["bundle_type"] == "attribute_bundle"
    assert result["product_metadata"]["status"] == "succeeded"
    assert result["result"]["dimensions"] == ["channel", "region"]
    assert [driver["dimension"] for driver in result["result"]["drivers"]] == ["channel", "region"]
    first_ref = result["result"]["drivers"][0]["decompose_ref"]
    assert first_ref["artifact_family"] == "attribution_frame"
    assert first_ref["shape"] == "ranked_contributions"
    assert "decomposition_type" not in first_ref


def test_attribute_defaults_to_delta_share_and_limit_five() -> None:
    result, _, _, _, _ = _run_with_patched_children(_params())

    assert result["provenance"]["decomposition_method"] == "delta_share"
    assert result["provenance"]["decomposition_limit"] == 5
    projection = result["product_metadata"]["projection_metadata"]
    assert projection["decomposition_limit"] == 5


@pytest.mark.parametrize("limit", [1, 2, 100])
def test_attribute_accepts_explicit_delta_share_and_supported_limits(limit: int) -> None:
    params = _params()
    params["decomposition_method"] = "delta_share"
    params["decomposition_limit"] = limit

    result, _, _, _, _ = _run_with_patched_children(params)

    assert result["provenance"]["decomposition_method"] == "delta_share"
    assert result["provenance"]["decomposition_limit"] == limit


def test_attribute_converts_aoi_filters_to_observe_scope_predicates() -> None:
    params = _params()
    params["current"]["filter"] = _filter("region = 'US'")
    params["baseline"]["filter"] = _filter("region = 'CA'")

    result, _, mock_observe, _, _ = _run_with_patched_children(params)

    assert [call.args[2]["scope"] for call in mock_observe.call_args_list] == [
        {"predicate": "region = 'US'"},
        {"predicate": "region = 'CA'"},
    ]
    assert result["result"]["current"]["scope"] == {"predicate": "region = 'US'"}
    assert result["result"]["baseline"]["scope"] == {"predicate": "region = 'CA'"}


def test_attribute_truncates_driver_rows_and_computes_others_bucket() -> None:
    params = _params()
    params["dimensions"] = ["channel"]
    params["decomposition_limit"] = 2
    rows = [
        {"channel": "A", "contribution_abs": 10.0, "contribution_pct": 0.5},
        {"channel": "B", "contribution_abs": 6.0, "contribution_pct": 0.3},
        {"channel": "C", "contribution_abs": 3.0, "contribution_pct": 0.15},
        {"channel": "D", "contribution_abs": 1.0, "contribution_pct": 0.05},
    ]

    result, _, _, _, _ = _run_with_patched_children(
        params,
        decompose_results=[_decompose_result("channel", rows=rows, scope_absolute_delta=20.0)],
    )

    driver = result["result"]["drivers"][0]
    assert driver["returned_row_count"] == 2
    assert driver["total_row_count"] == 4
    assert driver["is_truncated"] is True
    assert driver["others_absolute_contribution"] == 4.0
    assert driver["others_contribution_share"] == pytest.approx(0.2)
    assert [row["channel"] for row in driver["rows"]] == ["A", "B"]
    assert [row["absolute_contribution"] for row in driver["rows"]] == [10.0, 6.0]
    assert [row["contribution_share"] for row in driver["rows"]] == [0.5, 0.3]
    assert [issue["code"] for issue in driver["issues"]] == ["driver_truncated"]


def test_attribute_compare_needs_attention_marks_bundle_and_remaps_issue() -> None:
    result, _, _, _, _ = _run_with_patched_children(
        _params(),
        compare_result=_compare_result(
            comparability={
                "status": "needs_attention",
                "issues": [{"severity": "warning", "message": "windows differ"}],
            }
        ),
    )

    assert result["product_metadata"]["status"] == "needs_attention"
    assert result["product_metadata"]["validation"]["status"] == "needs_attention"
    assert result["result"]["comparison"]["comparability_status"] == "needs_attention"
    assert result["product_metadata"]["issues"] == [
        {"code": "compare_needs_attention", "severity": "warning", "message": "windows differ"}
    ]


def test_attribute_decompose_needs_attention_suppresses_shares_and_remaps_issues() -> None:
    params = _params()
    params["dimensions"] = ["channel"]
    attribution = {
        "status": "needs_attention",
        "issues": [
            {
                "code": "attribution_not_reconcilable",
                "severity": "error",
                "message": "does not reconcile",
            }
        ],
    }

    result, _, _, _, _ = _run_with_patched_children(
        params,
        decompose_results=[_decompose_result("channel", attribution=attribution)],
    )

    driver = result["result"]["drivers"][0]
    assert result["product_metadata"]["status"] == "needs_attention"
    assert result["product_metadata"]["validation"]["status"] == "needs_attention"
    assert driver["attribution_status"] == "needs_attention"
    assert driver["interpretation"] == "directional_only"
    assert driver["share_suppressed"] is True
    assert [row["contribution_share"] for row in driver["rows"]] == [None, None]
    assert [issue["code"] for issue in driver["issues"]] == [
        "decompose_needs_attention",
        "decompose_needs_attention",
    ]


def test_attribute_reports_missing_metric_before_committing() -> None:
    runtime = _make_runtime()
    runtime.resolve_metric.return_value = None

    with pytest.raises(ValueError, match="not found or not published"):
        run_attribute_intent(runtime, "sess_attr", _params())

    runtime.insert_artifact.assert_not_called()
    runtime.insert_step.assert_not_called()


@pytest.mark.parametrize(
    ("payload_patch", "message"),
    [
        (None, "params"),
        ({"unexpected": True}, "unsupported field"),
        ({"current": {"scope": {"predicate": "region = 'US'"}}}, "scope"),
        ({"current": {"filter": None}, "baseline": {"filter": None}}, "filter"),
        ({"current": {"time_scope": {"kind": "point"}}}, "unsupported"),
        ({"current": {"time_scope": {"__remove__": "field"}}}, "field"),
        ({"dimensions": []}, "dimensions"),
        ({"dimensions": "region"}, "dimensions"),
        ({"decomposition_limit": 0}, "decomposition_limit"),
        ({"decomposition_limit": 101}, "exceeds max allowed"),
        ({"decomposition_limit": "2"}, "positive integer"),
    ],
)
def test_attribute_rejects_non_current_or_invalid_request_shapes(
    payload_patch: dict[str, Any] | None,
    message: str,
) -> None:
    runtime = _make_runtime()
    params: dict[str, Any] | None = _params()
    if payload_patch is None:
        params = None
    else:
        params = deepcopy(params)
        _merge_patch(params, payload_patch)

    with pytest.raises(ValueError, match=message):
        run_attribute_intent(runtime, "sess_attr", params)

    runtime.insert_artifact.assert_not_called()
    runtime.insert_step.assert_not_called()


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
