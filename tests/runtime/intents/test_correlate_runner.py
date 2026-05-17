from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.runtime.intents._runner_fixtures import _FAKE_ARTIFACT_ID, _SESSION

_LEFT_ARTIFACT_ID = "art_left_obs"
_RIGHT_ARTIFACT_ID = "art_right_obs"
_LEFT_STEP_ID = "step_left"
_RIGHT_STEP_ID = "step_right"


def _series(values: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "window": {"start": f"2024-01-{idx:02d}", "end": f"2024-01-{idx + 1:02d}"},
            "value": value,
        }
        for idx, value in enumerate(values, start=1)
    ]


def _time_series_observation(
    metric: str = "m1",
    *,
    values: list[Any] | None = None,
    granularity: str = "day",
) -> dict[str, Any]:
    series = _series(values if values is not None else [10.0, 20.0, 30.0, 40.0, 50.0])
    return {
        "observation_type": "time_series",
        "metric": metric,
        "schema_version": "1.0",
        "unit": None,
        "granularity": granularity,
        "series": series,
        "analytical_metadata": {
            "aggregation_semantics": "sum",
            "additive_dimensions": ["country", "device", "date"],
            "row_count": len(series),
        },
        "time_scope": {
            "kind": "range",
            "start": series[0]["window"]["start"],
            "end": series[-1]["window"]["end"],
        },
        "scope": {},
    }


def _make_runtime(
    left_artifact: dict[str, Any] | None = None,
    right_artifact: dict[str, Any] | None = None,
) -> MagicMock:
    runtime = MagicMock()
    runtime.core = MagicMock()
    runtime.resolve_artifact_with_step_by_id.side_effect = [
        (_LEFT_STEP_ID, left_artifact or _time_series_observation("m1")),
        (_RIGHT_STEP_ID, right_artifact or _time_series_observation("m2")),
    ]
    runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
    runtime.insert_step.return_value = None
    return runtime


def _correlate_params(**overrides: Any) -> dict[str, Any]:
    params = {
        "left_artifact_id": _LEFT_ARTIFACT_ID,
        "right_artifact_id": _RIGHT_ARTIFACT_ID,
    }
    params.update(overrides)
    return params


def _run_correlate(
    runtime: MagicMock,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from marivo.runtime.intents.correlate import run_correlate_intent

    return run_correlate_intent(runtime, _SESSION, params or _correlate_params())


def _assert_correlate_fails_without_commit(
    runtime: MagicMock,
    params: dict[str, Any],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _run_correlate(runtime, params)
    runtime.commit_artifact_with_extraction.assert_not_called()
    runtime.insert_step.assert_not_called()


def test_correlate_calls_commit_artifact_with_extraction() -> None:
    runtime = _make_runtime()

    _run_correlate(runtime)

    runtime.commit_artifact_with_extraction.assert_called_once()


def test_correlate_passes_step_type_correlate() -> None:
    runtime = _make_runtime()

    _run_correlate(runtime)

    _, kwargs = runtime.commit_artifact_with_extraction.call_args
    assert kwargs.get("step_type") == "correlate"


def test_correlate_artifact_type_is_pairwise_ts_association() -> None:
    runtime = _make_runtime()

    _run_correlate(runtime)

    args, _ = runtime.commit_artifact_with_extraction.call_args
    assert args[2] == "pairwise_time_series_association"


def test_correlate_records_resolved_artifact_lineage() -> None:
    runtime = _make_runtime()

    result = _run_correlate(runtime)

    assert result["left_ref"]["step_id"] == _LEFT_STEP_ID
    assert result["left_ref"]["artifact_id"] == _LEFT_ARTIFACT_ID
    assert result["right_ref"]["step_id"] == _RIGHT_STEP_ID
    assert result["right_ref"]["artifact_id"] == _RIGHT_ARTIFACT_ID
    assert result["source_lineage"]["left_artifact"]["artifact_id"] == _LEFT_ARTIFACT_ID
    assert result["source_lineage"]["right_artifact"]["artifact_id"] == _RIGHT_ARTIFACT_ID


def test_correlate_omitted_method_defaults_to_spearman() -> None:
    runtime = _make_runtime()

    result = _run_correlate(runtime)

    assert result["statistic"]["method"] == "spearman"
    assert result["alignment"]["status"] == "aligned"


@pytest.mark.parametrize("method", ["pearson", "spearman"])
def test_correlate_accepts_supported_methods(method: str) -> None:
    runtime = _make_runtime()

    result = _run_correlate(runtime, _correlate_params(method=method))

    assert result["statistic"]["method"] == method


def test_correlate_omitted_min_pairs_uses_default_five() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1", values=[1, 2, 3, 4]),
        _time_series_observation("m2", values=[1, 2, 3, 4]),
    )

    _assert_correlate_fails_without_commit(runtime, _correlate_params(), "minimum 5")


def test_correlate_explicit_min_pairs_allows_shorter_aligned_series() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1", values=[1, 2, 3]),
        _time_series_observation("m2", values=[2, 4, 6]),
    )

    result = _run_correlate(runtime, _correlate_params(min_pairs=3))

    assert result["statistic"]["n_pairs"] == 3
    assert result["analytical_metadata"]["matched_pair_count"] == 3


def test_correlate_positive_association_derives_sign_and_significance() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1", values=[1, 2, 3, 4, 5]),
        _time_series_observation("m2", values=[2, 4, 6, 8, 10]),
    )

    result = _run_correlate(runtime, _correlate_params(method="pearson"))

    assert result["statistic"]["coefficient"] == pytest.approx(1.0)
    assert result["statistic"]["p_value"] == pytest.approx(0.0)
    assert result["sign"] == "positive"
    assert result["significance"] == "significant"


def test_correlate_negative_association_derives_sign() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1", values=[1, 2, 3, 4, 5]),
        _time_series_observation("m2", values=[10, 8, 6, 4, 2]),
    )

    result = _run_correlate(runtime, _correlate_params(method="pearson"))

    assert result["statistic"]["coefficient"] == pytest.approx(-1.0)
    assert result["sign"] == "negative"


def test_correlate_zero_association_derives_zero_sign() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1", values=[-2, -1, 0, 1, 2]),
        _time_series_observation("m2", values=[4, 1, 0, 1, 4]),
    )

    result = _run_correlate(runtime, _correlate_params(method="pearson"))

    assert result["statistic"]["coefficient"] == pytest.approx(0.0)
    assert result["sign"] == "zero"
    assert result["significance"] == "not_significant"


def test_correlate_constant_series_commits_warning_with_undefined_statistic() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1", values=[1, 1, 1, 1, 1]),
        _time_series_observation("m2", values=[1, 2, 3, 4, 5]),
    )

    result = _run_correlate(runtime)

    assert result["alignment"]["status"] == "needs_attention"
    assert result["alignment"]["issues"][0]["code"] == "constant_series"
    assert result["statistic"]["coefficient"] is None
    assert result["statistic"]["p_value"] is None
    assert result["sign"] == "undefined"
    assert result["significance"] == "undefined"
    runtime.commit_artifact_with_extraction.assert_called_once()


def test_correlate_drops_non_numeric_pairs_and_reports_counts() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1", values=[1, "bad", 3, 4, 5, 6]),
        _time_series_observation("m2", values=[1, 2, 3, None, 5, 6]),
    )

    result = _run_correlate(runtime, _correlate_params(min_pairs=4))

    metadata = result["analytical_metadata"]
    assert result["statistic"]["n_pairs"] == 4
    assert metadata["left_point_count"] == 6
    assert metadata["right_point_count"] == 6
    assert metadata["matched_pair_count"] == 4
    assert metadata["dropped_left_points"] == 2
    assert metadata["dropped_right_points"] == 2
    assert metadata["matched_time_scope"] == {
        "kind": "range",
        "start": "2024-01-01",
        "end": "2024-01-07",
    }


def test_correlate_artifact_metadata_includes_pairing_and_query_hash() -> None:
    runtime = _make_runtime()

    result = _run_correlate(runtime, _correlate_params(method="pearson"))

    assert result["association_type"] == "pairwise_time_series_association"
    assert result["analytical_metadata"]["pairing_rule"] == "intersection_by_time_bucket"
    assert result["analytical_metadata"]["significance_level"] == 0.05
    assert result["execution_metadata"]["engine"] == "service"
    assert len(result["execution_metadata"]["query_hash"]) == 16
    assert result["version_metadata"]["artifact_schema_version"] == "1.0"


@pytest.mark.parametrize(
    "payload",
    [
        {"left_artifact_id": _LEFT_ARTIFACT_ID},
        {"right_artifact_id": _RIGHT_ARTIFACT_ID},
        {"left_artifact_id": " ", "right_artifact_id": _RIGHT_ARTIFACT_ID},
        {"left_artifact_id": _LEFT_ARTIFACT_ID, "right_artifact_id": " "},
    ],
)
def test_correlate_requires_both_artifact_ids(payload: dict[str, Any]) -> None:
    runtime = _make_runtime()

    _assert_correlate_fails_without_commit(
        runtime, payload, "both left_artifact_id and right_artifact_id"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "left_ref": {"step_id": _LEFT_STEP_ID, "session_id": _SESSION},
            "right_ref": {"step_id": _RIGHT_STEP_ID, "session_id": _SESSION},
        },
        {**_correlate_params(), "pairing_rule": "intersection_by_time_bucket"},
    ],
)
def test_correlate_rejects_legacy_or_unknown_request_fields(payload: dict[str, Any]) -> None:
    runtime = _make_runtime()

    _assert_correlate_fails_without_commit(runtime, payload, "unsupported parameter")


def test_correlate_rejects_unknown_method() -> None:
    runtime = _make_runtime()

    _assert_correlate_fails_without_commit(
        runtime, _correlate_params(method="kendall"), "UNSUPPORTED_METHOD"
    )


@pytest.mark.parametrize("min_pairs", [0, -1, "not_an_int"])
def test_correlate_rejects_invalid_min_pairs(min_pairs: Any) -> None:
    runtime = _make_runtime()

    _assert_correlate_fails_without_commit(
        runtime, _correlate_params(min_pairs=min_pairs), "min_pairs must be an integer >= 1"
    )


def test_correlate_reports_missing_left_artifact_id() -> None:
    runtime = MagicMock()
    runtime.resolve_artifact_with_step_by_id.return_value = None

    _assert_correlate_fails_without_commit(
        runtime,
        _correlate_params(left_artifact_id="missing_left"),
        "left_artifact_id 'missing_left'",
    )


def test_correlate_reports_missing_right_artifact_id() -> None:
    runtime = MagicMock()
    runtime.resolve_artifact_with_step_by_id.side_effect = [
        (_LEFT_STEP_ID, _time_series_observation("m1")),
        None,
    ]

    _assert_correlate_fails_without_commit(
        runtime,
        _correlate_params(right_artifact_id="missing_right"),
        "right_artifact_id 'missing_right'",
    )


@pytest.mark.parametrize(
    ("side", "left_artifact", "right_artifact", "match"),
    [
        (
            "left",
            {"observation_type": "scalar"},
            _time_series_observation("m2"),
            "left_artifact_id",
        ),
        (
            "right",
            _time_series_observation("m1"),
            {"observation_type": "segmented"},
            "right_artifact_id",
        ),
    ],
)
def test_correlate_rejects_non_time_series_artifacts(
    side: str,
    left_artifact: dict[str, Any],
    right_artifact: dict[str, Any],
    match: str,
) -> None:
    runtime = _make_runtime(left_artifact, right_artifact)

    _assert_correlate_fails_without_commit(runtime, _correlate_params(), match)


def test_correlate_rejects_granularity_mismatch() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1", granularity="day"),
        _time_series_observation("m2", granularity="week"),
    )

    _assert_correlate_fails_without_commit(runtime, _correlate_params(), "granularity mismatch")


def test_correlate_rejects_insufficient_aligned_numeric_pairs() -> None:
    runtime = _make_runtime(
        _time_series_observation("m1", values=[1, 2, 3, 4, 5]),
        _time_series_observation("m2", values=[1, None, None, None, 5]),
    )

    _assert_correlate_fails_without_commit(runtime, _correlate_params(), "only 2 aligned")
