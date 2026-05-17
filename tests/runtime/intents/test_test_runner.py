"""Tests for the current AOI test intent runner contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marivo.runtime.intents._helpers import SampleSummary
from marivo.runtime.intents.test import _betai, _p_value_from_t, _t_sf, run_test_intent


def _valid_params() -> dict[str, Any]:
    return {
        "metric": "metric.test_metric",
        "left": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-08T00:00:00Z",
            }
        },
        "right": {
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-08T00:00:00Z",
                "end": "2026-01-15T00:00:00Z",
            }
        },
        "kind": "numeric",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }


def _runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.core.normalize_intent_metric_ref = MagicMock(return_value="metric.test_metric")
    runtime.core.metric_name_from_ref = MagicMock(return_value="test_metric")
    return runtime


def _sample(
    *,
    n: int | None = 30,
    mean: float | None = 100.0,
    standard_deviation: float | None = 15.0,
    predicate_filter_lineage: dict[str, Any] | None = None,
) -> SampleSummary:
    return SampleSummary(
        n=n,
        mean=mean,
        standard_deviation=standard_deviation,
        predicate_filter_lineage=predicate_filter_lineage,
    )


def _run_with_mock_data(
    params: dict[str, Any] | None = None,
    *,
    left_summary: SampleSummary | None = None,
    right_summary: SampleSummary | None = None,
) -> tuple[dict[str, Any], MagicMock]:
    runtime = _runtime()
    params = deepcopy(params if params is not None else _valid_params())

    with patch("marivo.runtime.intents.test.compute_numeric_sample_summary") as mock_compute:
        mock_compute.side_effect = [
            left_summary or _sample(),
            right_summary or _sample(n=25, mean=90.0, standard_deviation=12.0),
        ]
        with patch(
            "marivo.runtime.intents.test.resolve_predicate_lineage_reuse_for_intent"
        ) as mock_lineage:
            mock_lineage.return_value = {
                "issues": [],
                "fatal_message": None,
                "reuse_summary": None,
            }
            with patch("marivo.runtime.intents.test.commit_step_result") as mock_commit:
                mock_commit.return_value = {
                    "intent_type": "test",
                    "step_type": "test",
                    "step_ref": {"session_id": "s1", "step_id": "step-1", "step_type": "test"},
                    "artifact_id": "art-1",
                }
                run_test_intent(runtime, "session-1", params)
                artifact = mock_commit.call_args[0][6]
                return artifact, mock_compute


def test_t_sf_symmetry() -> None:
    for t in [-3.0, -1.0, 0.0, 1.0, 3.0]:
        for df in [5, 10, 30, 100]:
            assert _t_sf(t, df) + _t_sf(-t, df) == pytest.approx(1.0)


def test_p_value_two_sided_zero_t() -> None:
    assert _p_value_from_t(0.0, 10, "two_sided") == pytest.approx(1.0)


def test_p_value_decreases_with_larger_t() -> None:
    assert _p_value_from_t(1.0, 30, "two_sided") > _p_value_from_t(5.0, 30, "two_sided")


def test_betai_boundary_values() -> None:
    assert _betai(1, 1, 0.0) == pytest.approx(0.0)
    assert _betai(1, 1, 1.0) == pytest.approx(1.0)


@pytest.mark.parametrize("alternative", ["two_sided", "greater", "less"])
def test_records_supported_alternatives(alternative: str) -> None:
    params = _valid_params()
    params["hypothesis"]["alternative"] = alternative

    artifact, _ = _run_with_mock_data(params)

    assert artifact["hypothesis"]["alternative"] == alternative
    assert artifact["p_value"] is not None


@pytest.mark.parametrize(
    ("significance", "alpha"),
    [("conservative", 0.01), ("balanced", 0.05), ("aggressive", 0.10)],
)
def test_records_supported_significance_presets(significance: str, alpha: float) -> None:
    params = _valid_params()
    params["hypothesis"]["significance"] = significance

    artifact, _ = _run_with_mock_data(params)

    assert artifact["hypothesis"]["significance"] == significance
    assert artifact["hypothesis"]["alpha"] == alpha


def test_artifact_shape_is_current_hypothesis_test_result() -> None:
    artifact, _ = _run_with_mock_data()

    assert artifact["result_type"] == "hypothesis_test"
    assert artifact["kind"] == "numeric"
    assert artifact["hypothesis"] == {
        "family": "two_sample_mean",
        "alternative": "two_sided",
        "significance": "balanced",
        "alpha": 0.05,
    }
    assert isinstance(artifact["statistic"], float)
    assert isinstance(artifact["assumption_notes"], list)
    assert all(isinstance(note, str) for note in artifact["assumption_notes"])
    assert artifact["method"] == "welch_t"
    assert artifact["estimate"]["estimand"] == "mean_diff"
    assert "label" not in artifact["hypothesis"]
    assert "assumptions" not in artifact
    assert "left_ref" not in artifact
    assert "right_ref" not in artifact
    assert "sample_kind" not in artifact


def test_passes_filters_to_sample_summaries_and_source_lineage() -> None:
    params = _valid_params()
    left_filter = {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]}
    right_filter = {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'CA'"}]}
    params["left"]["filter"] = left_filter
    params["right"]["filter"] = right_filter

    artifact, mock_compute = _run_with_mock_data(params)

    assert mock_compute.call_args_list[0].kwargs["scope_raw"] == left_filter
    assert mock_compute.call_args_list[1].kwargs["scope_raw"] == right_filter
    assert artifact["source_lineage"]["left"]["filter"] == left_filter
    assert artifact["source_lineage"]["right"]["filter"] == right_filter


def test_zero_variance_slice_adds_assumption_note() -> None:
    artifact, _ = _run_with_mock_data(left_summary=_sample(standard_deviation=0.0))

    assert any("zero variance" in note for note in artifact["assumption_notes"])


@pytest.mark.parametrize(
    ("left_summary", "right_summary", "message"),
    [
        (_sample(n=1), _sample(n=25, mean=90.0, standard_deviation=12.0), "n >= 2"),
        (_sample(mean=None), _sample(n=25, mean=90.0, standard_deviation=12.0), "missing"),
        (_sample(standard_deviation=0.0), _sample(standard_deviation=0.0), "standard error"),
    ],
)
def test_rejects_insufficient_data(
    left_summary: SampleSummary,
    right_summary: SampleSummary,
    message: str,
) -> None:
    runtime = _runtime()

    with (
        patch("marivo.runtime.intents.test.compute_numeric_sample_summary") as mock_compute,
        patch(
            "marivo.runtime.intents.test.resolve_predicate_lineage_reuse_for_intent",
            return_value={"issues": [], "fatal_message": None, "reuse_summary": None},
        ),
    ):
        mock_compute.side_effect = [left_summary, right_summary]
        with pytest.raises(ValueError, match=message):
            run_test_intent(runtime, "session-1", _valid_params())


@pytest.mark.parametrize(
    ("payload_patch", "message"),
    [
        (None, "params"),
        ({"__remove__": "metric"}, "metric"),
        ({"__remove__": "kind"}, "kind"),
        ({"__remove__": "hypothesis"}, "hypothesis"),
        ({"method": "welch_t"}, "method"),
        ({"kind": "Numeric"}, "kind"),
        ({"kind": "rate"}, "kind"),
        ({"left": {"scope": {"predicate": "region = 'US'"}}}, "scope"),
        ({"left": {"filter": None}}, "filter"),
        ({"hypothesis": {"family": "two_sample_proportion"}}, "family"),
        ({"hypothesis": {"alternative": "not_equal"}}, "alternative"),
        ({"hypothesis": {"significance": "loose"}}, "significance"),
        ({"hypothesis": {"__remove__": "family"}}, "family"),
        ({"hypothesis": {"__remove__": "alternative"}}, "alternative"),
        ({"hypothesis": {"__remove__": "significance"}}, "significance"),
        ({"hypothesis": {"alpha": 0.05}}, "alpha"),
        ({"hypothesis": {"label": "legacy label"}}, "label"),
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
        run_test_intent(runtime, "session-1", params)


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
