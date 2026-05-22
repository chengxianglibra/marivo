from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from marivo.contracts.aoi_runtime import validate_aoi_artifact
from marivo.runtime.intents.detect import run_detect_intent
from marivo.runtime.intents.metric_frame import (
    build_delta_frame_artifact,
    build_metric_frame_artifact,
)
from tests.runtime.intents._runner_fixtures import _SESSION

_SOURCE_STEP_ID = "step_source"
_METRIC_ARTIFACT_ID = "artifact_metric"
_DELTA_ARTIFACT_ID = "artifact_delta"


def _metric_frame_source(
    *,
    shape: str = "time_series",
    values: list[Any] | None = None,
    metric_ref: str = "metric.revenue",
) -> dict[str, Any]:
    points = [
        {
            "window": {
                "start": f"2026-01-{day:02d}T00:00:00Z",
                "end": f"2026-01-{day + 1:02d}T00:00:00Z",
            },
            "value": value,
        }
        for day, value in enumerate(values or [100.0, 100.0, 100.0, 100.0, 220.0, 100.0], start=1)
    ]
    return build_metric_frame_artifact(
        artifact_id=_METRIC_ARTIFACT_ID,
        shape=shape,
        metric_ref=metric_ref,
        time_scope={
            "field": "event_time",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-07T00:00:00Z",
        },
        scope={},
        axes=[{"kind": "time", "grain": "day"}],
        series=[{"keys": {}, "points": points}],
        unit="usd",
    )


def _panel_metric_frame_source() -> dict[str, Any]:
    source = _metric_frame_source()
    source["shape"] = "panel"
    source["axes"] = [{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "region"}]
    source["payload"]["series"] = [
        {
            "keys": {"region": "west"},
            "points": source["payload"]["series"][0]["points"],
        }
    ]
    return source


def _panel_metric_frame_source_with_duplicate_point_keys() -> dict[str, Any]:
    source = _metric_frame_source()
    source["shape"] = "panel"
    source["axes"] = [{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "region"}]
    base_points = source["payload"]["series"][0]["points"]
    source["payload"]["series"] = [
        {"keys": {"region": "east"}, "points": base_points},
        {"keys": {"region": "west"}, "points": base_points},
    ]
    return source


def _sparse_panel_metric_frame_source() -> dict[str, Any]:
    source = _metric_frame_source()
    source["shape"] = "panel"
    source["axes"] = [{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "region"}]
    source["payload"]["series"] = [
        {
            "keys": {"region": region},
            "points": [
                {
                    "window": {
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-02T00:00:00Z",
                    },
                    "value": value,
                }
            ],
        }
        for region, value in (("east", 100.0), ("west", 120.0), ("north", 140.0))
    ]
    return source


def _delta_frame_source(
    *,
    shape: str = "time_series_delta",
    metric_ref: str = "metric.revenue",
    keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    return build_delta_frame_artifact(
        artifact_id=_DELTA_ARTIFACT_ID,
        shape=shape,
        metric_ref=metric_ref,
        axes=(
            [{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "region"}]
            if shape == "panel_delta"
            else [{"kind": "time", "grain": "day"}]
        ),
        series=[
            {
                "keys": keys or {},
                "points": [
                    {
                        "window": {
                            "start": "2026-01-05T00:00:00Z",
                            "end": "2026-01-06T00:00:00Z",
                        },
                        "baseline_window": {
                            "start": "2025-12-29T00:00:00Z",
                            "end": "2025-12-30T00:00:00Z",
                        },
                        "current_value": 130.0,
                        "baseline_value": 100.0,
                        "delta_abs": 30.0,
                        "delta_pct": 0.3,
                        "direction": "increase",
                    }
                ],
            }
        ],
        unit="usd",
    )


def _panel_delta_frame_source_with_duplicate_point_keys() -> dict[str, Any]:
    source = _delta_frame_source(shape="panel_delta", keys={"region": "east"})
    source["payload"]["series"].append(
        {
            "keys": {"region": "west"},
            "points": [dict(source["payload"]["series"][0]["points"][0])],
        }
    )
    return source


def _runtime_with_source(source_artifact: dict[str, Any] | None) -> MagicMock:
    runtime = MagicMock()
    runtime.resolve_artifact_with_step_by_id.return_value = (
        None if source_artifact is None else (_SOURCE_STEP_ID, source_artifact)
    )
    runtime.commit_artifact_with_extraction.side_effect = lambda *args, **kwargs: kwargs[
        "artifact_id"
    ]
    runtime.insert_step.return_value = None
    return runtime


def _run_detect(runtime: MagicMock, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"source_artifact_id": _METRIC_ARTIFACT_ID}
    params.update(overrides)
    return run_detect_intent(runtime, _SESSION, params)


def test_detect_metric_frame_commits_candidate_set_without_sql() -> None:
    runtime = _runtime_with_source(_metric_frame_source())

    envelope = _run_detect(runtime, sensitivity="balanced", limit=5)

    artifact = envelope["result"]
    validated_artifact = validate_aoi_artifact(artifact).model_dump(mode="json")
    assert artifact["artifact_family"] == "candidate_set"
    assert validated_artifact["artifact_family"] == "candidate_set"
    assert artifact["shape"] == "point_anomaly_candidates"
    assert artifact["lineage"]["strategy"] == "point_anomaly"
    assert artifact["payload"]["scan_summary"]["total_candidate_count"] == 1
    item = artifact["payload"]["items"][0]
    assert item["item_id"] == "point_anomaly:series_0:2026-01-05T00:00:00Z"
    assert item["source_point_ref"]["artifact_id"] == _METRIC_ARTIFACT_ID
    assert item["source_point_ref"]["point_key"] == "2026-01-05T00:00:00Z"
    assert item["direction"] == "increase"
    runtime.compile_step.assert_not_called()
    runtime.resolve_metric_execution_context.assert_not_called()
    args, kwargs = runtime.commit_artifact_with_extraction.call_args
    assert args[2] == "candidate_set"
    committed_payload = args[4]
    assert envelope["artifact_id"] == artifact["artifact_id"] == committed_payload["artifact_id"]
    assert envelope["artifact_id"] == kwargs["artifact_id"]
    assert kwargs["step_type"] == "detect"


def test_detect_delta_frame_commits_period_shift_candidate_set() -> None:
    runtime = _runtime_with_source(_delta_frame_source())

    envelope = run_detect_intent(
        runtime,
        _SESSION,
        {
            "source_artifact_id": _DELTA_ARTIFACT_ID,
            "sensitivity": "balanced",
        },
    )

    artifact = envelope["result"]
    item = artifact["payload"]["items"][0]
    assert artifact["artifact_family"] == "candidate_set"
    assert artifact["shape"] == "period_shift_candidates"
    assert artifact["lineage"]["strategy"] == "period_shift"
    assert item["item_id"] == "period_shift:series_0:2026-01-05T00:00:00Z"
    assert item["baseline_window"] == {
        "start": "2025-12-29T00:00:00Z",
        "end": "2025-12-30T00:00:00Z",
    }
    assert item["source_delta_point_ref"]["artifact_id"] == _DELTA_ARTIFACT_ID
    assert item["value"] == 130.0
    assert item["baseline_value"] == 100.0
    assert item["delta_abs"] == 30.0
    assert item["delta_pct"] == 0.3
    assert item["score"] == 0.3
    assert item["direction"] == "increase"


def test_detect_panel_delta_frame_commits_period_shift_candidate_set_with_source_keys() -> None:
    runtime = _runtime_with_source(
        _delta_frame_source(shape="panel_delta", keys={"region": "west"})
    )

    envelope = run_detect_intent(
        runtime,
        _SESSION,
        {
            "source_artifact_id": _DELTA_ARTIFACT_ID,
            "sensitivity": "balanced",
        },
    )

    artifact = envelope["result"]
    item = artifact["payload"]["items"][0]
    assert artifact["shape"] == "period_shift_candidates"
    assert artifact["subject"]["source_shape"] == "panel_delta"
    assert artifact["lineage"]["strategy"] == "period_shift"
    assert item["keys"] == {"region": "west"}
    assert item["source_delta_point_ref"] == {
        "artifact_id": _DELTA_ARTIFACT_ID,
        "series_index": 0,
        "point_index": 0,
        "series_keys": {"region": "west"},
        "point_key": "2026-01-05T00:00:00Z",
    }


def test_detect_panel_metric_frame_candidate_item_ids_include_series_discriminator() -> None:
    runtime = _runtime_with_source(_panel_metric_frame_source_with_duplicate_point_keys())

    envelope = _run_detect(runtime, sensitivity="balanced")

    item_ids = [item["item_id"] for item in envelope["result"]["payload"]["items"]]
    assert item_ids == [
        "point_anomaly:series_0:2026-01-05T00:00:00Z",
        "point_anomaly:series_1:2026-01-05T00:00:00Z",
    ]
    assert len(item_ids) == len(set(item_ids))


def test_detect_panel_delta_frame_candidate_item_ids_include_series_discriminator() -> None:
    runtime = _runtime_with_source(_panel_delta_frame_source_with_duplicate_point_keys())

    envelope = run_detect_intent(
        runtime,
        _SESSION,
        {
            "source_artifact_id": _DELTA_ARTIFACT_ID,
            "sensitivity": "balanced",
        },
    )

    item_ids = [item["item_id"] for item in envelope["result"]["payload"]["items"]]
    assert item_ids == [
        "period_shift:series_0:2026-01-05T00:00:00Z",
        "period_shift:series_1:2026-01-05T00:00:00Z",
    ]
    assert len(item_ids) == len(set(item_ids))


@pytest.mark.parametrize(
    "removed_field",
    ["metric", "time_scope", "granularity", "filter", "dimension", "strategy"],
)
def test_detect_rejects_removed_source_style_fields(removed_field: str) -> None:
    runtime = _runtime_with_source(_metric_frame_source())

    with pytest.raises(ValueError, match="unsupported parameter"):
        _run_detect(runtime, **{removed_field: "legacy"})

    runtime.resolve_artifact_with_step_by_id.assert_not_called()
    runtime.commit_artifact_with_extraction.assert_not_called()


@pytest.mark.parametrize("shape", ["scalar", "segmented"])
def test_detect_rejects_unsupported_metric_frame_shapes(shape: str) -> None:
    runtime = _runtime_with_source(_metric_frame_source(shape=shape))

    with pytest.raises(ValueError, match=f"metric_frame shape '{shape}' is not supported"):
        _run_detect(runtime)

    runtime.commit_artifact_with_extraction.assert_not_called()


@pytest.mark.parametrize("shape", ["scalar_delta", "segmented_delta"])
def test_detect_rejects_unsupported_delta_frame_shapes(shape: str) -> None:
    runtime = _runtime_with_source(_delta_frame_source(shape=shape))

    with pytest.raises(ValueError, match=f"delta_frame shape '{shape}' is not supported"):
        run_detect_intent(runtime, _SESSION, {"source_artifact_id": _DELTA_ARTIFACT_ID})

    runtime.commit_artifact_with_extraction.assert_not_called()


def test_detect_rejects_other_artifact_families() -> None:
    runtime = _runtime_with_source(
        {
            "artifact_id": "artifact_other",
            "artifact_family": "forecast_series",
            "shape": "time_series_forecast",
            "payload": {},
        }
    )

    with pytest.raises(
        ValueError, match="source artifact family 'forecast_series' is not supported"
    ):
        _run_detect(runtime)

    runtime.commit_artifact_with_extraction.assert_not_called()


def test_detect_empty_metric_frame_commits_candidate_set_with_empty_items() -> None:
    runtime = _runtime_with_source(_metric_frame_source(values=[100.0, 100.0, 100.0, 100.0]))

    envelope = _run_detect(runtime, sensitivity="balanced")

    artifact = envelope["result"]
    assert artifact["artifact_family"] == "candidate_set"
    assert artifact["shape"] == "point_anomaly_candidates"
    assert artifact["payload"]["items"] == []
    assert artifact["payload"]["scan_summary"]["total_candidate_count"] == 0
    assert artifact["payload"]["truncation"] == {
        "returned_candidate_count": 0,
        "total_candidate_count": 0,
        "truncated": False,
    }
    runtime.commit_artifact_with_extraction.assert_called_once()


def test_detect_sparse_panel_metric_frame_reports_no_eligible_series() -> None:
    runtime = _runtime_with_source(_sparse_panel_metric_frame_source())

    envelope = _run_detect(runtime, sensitivity="balanced")

    artifact = envelope["result"]
    assert artifact["payload"]["items"] == []
    assert artifact["payload"]["quality"]["status"] == "needs_attention"
    assert any(
        issue["code"] == "insufficient_points" for issue in artifact["payload"]["quality"]["issues"]
    )


def test_detect_panel_metric_frame_preserves_source_keys_in_candidate_ref() -> None:
    runtime = _runtime_with_source(_panel_metric_frame_source())

    envelope = _run_detect(runtime, sensitivity="balanced")

    item = envelope["result"]["payload"]["items"][0]
    assert item["keys"] == {"region": "west"}
    assert item["source_point_ref"]["series_keys"] == {"region": "west"}


def test_detect_fails_when_metric_frame_missing_metric_ref() -> None:
    source = _metric_frame_source()
    source["subject"].pop("metric_ref")
    runtime = _runtime_with_source(source)

    with pytest.raises(ValueError, match="missing metric_ref"):
        _run_detect(runtime)

    runtime.commit_artifact_with_extraction.assert_not_called()


def test_detect_invalid_sensitivity_uses_invalid_argument_error_style() -> None:
    runtime = _runtime_with_source(_metric_frame_source())

    with pytest.raises(
        ValueError,
        match="detect: INVALID_ARGUMENT - sensitivity='extreme' is not valid",
    ):
        _run_detect(runtime, sensitivity="extreme")

    runtime.resolve_artifact_with_step_by_id.assert_not_called()
    runtime.commit_artifact_with_extraction.assert_not_called()


def test_detect_missing_source_artifact_uses_artifact_not_found_error() -> None:
    runtime = _runtime_with_source(None)

    with pytest.raises(ValueError, match="ARTIFACT_NOT_FOUND"):
        _run_detect(runtime)

    runtime.commit_artifact_with_extraction.assert_not_called()
