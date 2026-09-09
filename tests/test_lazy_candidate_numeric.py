"""Independent list-and-math references for complete-series time discovery."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from marivo._temporal import _snapshot_json, certify_period_calendar
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.operators.candidate_contracts import (
    CandidateObjective,
    CandidatePayload,
    CandidateSpecV1,
)
from marivo.analysis.operators.candidate_values import candidate_item_id, execute_candidate
from marivo.analysis.operators.errors import CandidateError
from marivo.refs import ref
from tests.lazy_forecast_fixtures import history
from tests.lazy_observation_fixtures import make_sources


def _spec(
    objective: CandidateObjective = "point_anomalies",
    *,
    threshold: float = 1.0,
    limit: int = 50,
    panel: bool = False,
) -> CandidateSpecV1:
    metric = history(make_sources(), panel=panel)
    result = (
        metric.discover.point_anomalies(threshold=threshold, limit=limit)
        if objective == "point_anomalies"
        else metric.discover.interesting_windows(threshold=threshold, limit=limit)
        if objective == "interesting_windows"
        else metric.compare(metric).discover.period_shifts(threshold=threshold, limit=limit)
    )
    assert isinstance(result._root, LogicalRootHandle)
    assert isinstance(result._root.payload, CandidatePayload)
    return result._root.payload.spec


def _frame(values: Sequence[int | float | Decimal | None], *, period: bool = False) -> pd.DataFrame:
    times = [date(2026, 2, 1) + timedelta(days=index) for index in range(len(values))]
    if period:
        return pd.DataFrame(
            {
                "comparison_ordinal": pd.Series(range(len(values)), dtype="int64[pyarrow]"),
                "current_time": pd.Series(times, dtype=pd.ArrowDtype(pa.date32())),
                "baseline_time": pd.Series(
                    [time - timedelta(days=40) for time in times], dtype=pd.ArrowDtype(pa.date32())
                ),
                "delta": pd.Series(np.array(values, dtype=object), dtype="object"),
            }
        )
    return pd.DataFrame(
        {
            "order_time": pd.Series(times, dtype=pd.ArrowDtype(pa.date32())),
            "revenue": pd.Series(np.array(values, dtype=object), dtype="object"),
        }
    )


def _reference_scores(values: Sequence[float]) -> tuple[float, float, list[float]]:
    mean = sum(values) / len(values)
    stddev = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
    return mean, stddev, [(value - mean) / stddev for value in values]


def _reference_runs(
    scores: Sequence[float | None], coordinates: Sequence[int], threshold: float
) -> list[list[int]]:
    result: list[list[int]] = []
    active: list[int] = []
    for index, score in enumerate(scores):
        hit = score is not None and abs(score) >= threshold
        adjacent = index > 0 and coordinates[index] == coordinates[index - 1] + 1
        if active and (not hit or not adjacent):
            result.append(active)
            active = []
        if hit:
            active.append(index)
    if active:
        result.append(active)
    return result


@pytest.mark.parametrize("values", [[-1.0, 0.0, 1.0], [1.0, 2.0, 3.0, 7.0, 8.0, 9.0]])
def test_point_scores_use_independent_population_reference(values: list[float]) -> None:
    mean, stddev, scores = _reference_scores(values)
    spec = _spec(threshold=0.5)
    result, summary = execute_candidate(_frame(values), spec)
    expected = sorted(
        [(index, score) for index, score in enumerate(scores) if abs(score) >= 0.5],
        key=lambda item: (-abs(item[1]), item[0]),
    )
    assert result.score.tolist() == pytest.approx([abs(score) for _, score in expected])
    assert result.observed_value.tolist() == [values[index] for index, _ in expected]
    assert result.baseline_value.tolist() == pytest.approx([mean] * len(expected))
    assert result.signed_deviation.tolist() == pytest.approx(
        [values[index] - mean for index, _ in expected]
    )
    assert result.direction.tolist() == ["high" if score > 0 else "low" for _, score in expected]
    assert summary.baseline_stddev_range == pytest.approx((stddev, stddev))
    assert summary.evaluated_unit_count == len(values)


def test_threshold_equality_and_evaluated_empty_have_exact_schema() -> None:
    values = [-1.0, 0.0, 1.0]
    _, _, scores = _reference_scores(values)
    at_boundary, summary = execute_candidate(_frame(values), _spec(threshold=abs(scores[0])))
    assert len(at_boundary) == 2
    empty, empty_summary = execute_candidate(_frame(values), _spec(threshold=2.0))
    assert empty.empty and empty_summary.evaluated_series_count == 1
    assert empty_summary.score_range is None
    assert empty_summary.reason_counts == (("point_zscore_threshold_met", 0),)
    assert (
        pa.Schema.from_pandas(empty).remove_metadata()
        == pa.Schema.from_pandas(at_boundary).remove_metadata()
    )
    assert pa.Schema.from_pandas(empty).field("reason_codes").type == pa.list_(pa.string())
    assert summary.pre_limit_candidate_count == 2


def test_window_runs_break_at_missing_and_null_buckets_keep_global_baseline() -> None:
    frame = _frame([-4.0, 4.0, None, -4.0, 4.0, 0.0, 0.0])
    frame.loc[3:, "order_time"] = [date(2026, 2, day) for day in (6, 7, 8, 9)]
    result, summary = execute_candidate(frame.iloc[::-1], _spec("interesting_windows", threshold=1))
    assert result.window_start.tolist() == [date(2026, 2, 1), date(2026, 2, 6)]
    assert result.window_end.tolist() == [date(2026, 2, 2), date(2026, 2, 7)]
    assert result.point_count.tolist() == [2, 2]
    assert result.direction.tolist() == ["low", "low"]
    assert result.baseline_start.tolist() == [date(2026, 2, 1)] * 2
    assert result.baseline_end.tolist() == [date(2026, 2, 9)] * 2
    assert summary.searched_unit_count == 7 and summary.evaluated_unit_count == 6
    complete = [-4.0, 4.0, -4.0, 4.0, 0.0, 0.0]
    _, _, scores = _reference_scores(complete)
    assert result.score.tolist() == pytest.approx([abs(scores[0])] * 2)


def test_missing_bucket_alone_breaks_an_unusual_run() -> None:
    frame = _frame([-4.0, 4.0, 0.0, 0.0])
    frame.loc[1:, "order_time"] = [date(2026, 2, day) for day in (3, 4, 5)]
    result, _ = execute_candidate(frame, _spec("interesting_windows", threshold=1))
    assert result.point_count.tolist() == [1, 1]
    assert result.direction.tolist() == ["low", "high"]


@pytest.mark.parametrize(
    "values,direction",
    [([-4.0, 4.0, 0.0, 0.0], "low"), ([4.0, -4.0, 0.0, 0.0], "high")],
)
def test_window_exact_opposite_peak_tie_uses_earliest_time(
    values: list[float], direction: str
) -> None:
    # Mean is 0 and population variance is (16 + 16) / 4 = 8.
    # Both first points have absolute z-score sqrt(2), with opposite signs.
    result, summary = execute_candidate(
        _frame(values).iloc[::-1], _spec("interesting_windows", threshold=1.0)
    )
    assert result.window_start.tolist() == [date(2026, 2, 1)]
    assert result.window_end.tolist() == [date(2026, 2, 2)]
    assert result.point_count.tolist() == [2]
    assert result.direction.tolist() == [direction]
    assert result.score.tolist() == pytest.approx([math.sqrt(2)])
    assert result.peak_absolute_zscore.tolist() == pytest.approx([math.sqrt(2)])
    assert result.baseline_start.tolist() == [date(2026, 2, 1)]
    assert result.baseline_end.tolist() == [date(2026, 2, 4)]
    assert summary.baseline_mean_range == (0.0, 0.0)
    assert summary.baseline_stddev_range == pytest.approx((math.sqrt(8), math.sqrt(8)))


def test_panel_status_counts_and_null_dimensions_are_independent() -> None:
    eligible, constant, insufficient = _frame([-1.0, 0.0, 1.0]), _frame([0.1] * 11), _frame([1.0])
    eligible["channel"] = pd.Series([None] * 3, dtype="string[pyarrow]")
    constant["channel"] = pd.Series(["constant"] * 11, dtype="string[pyarrow]")
    insufficient["channel"] = pd.Series(["short"], dtype="string[pyarrow]")
    result, summary = execute_candidate(
        pd.concat([constant, insufficient, eligible], ignore_index=True), _spec(panel=True)
    )
    assert result.channel.isna().all() and len(result) == 2
    assert summary.series_count == 3 and summary.evaluated_series_count == 1
    assert summary.constant_series_count == 1 and summary.insufficient_series_count == 1


@pytest.mark.parametrize("values", [[], [1.0], [None, None, None], [4.0] * 4, [0.1] * 11])
def test_no_evaluable_series_is_not_an_empty_result(values: list[float | None]) -> None:
    with pytest.raises(CandidateError, match="no evaluable series"):
        execute_candidate(_frame(values), _spec())


@pytest.mark.parametrize("objective", ["interesting_windows", "period_shifts"])
def test_constant_fractional_baselines_cannot_create_rounding_candidates(
    objective: CandidateObjective,
) -> None:
    with pytest.raises(CandidateError, match="no evaluable series"):
        execute_candidate(_frame([0.1] * 17, period=objective == "period_shifts"), _spec(objective))


@pytest.mark.parametrize(
    "values",
    [
        [float("nan"), 1.0, 2.0],
        [float("inf"), 1.0, 2.0],
        [1e308, -1e308, 1e308],
        [1e-300, 2e-300, 3e-300],
        [2**60, 2**60 + 1, 2**60 + 2],
        [Decimal("1.00000000000000001"), Decimal("2"), Decimal("3")],
    ],
)
def test_nonfinite_overflow_and_lost_precision_are_typed_failures(
    values: list[int | float | Decimal | None],
) -> None:
    with pytest.raises(CandidateError):
        execute_candidate(_frame(values), _spec())


def test_custom_calendar_uses_captured_period_adjacency_without_forecast_completeness() -> None:
    spec = _spec("interesting_windows", threshold=1)
    authority = decode_fold_authority(spec.definition.fold_authority)
    snapshot = certify_period_calendar(
        calendar_ref=ref.period_calendar("sales.fiscal"),
        boundary_timezone="UTC",
        coverage=(date(2026, 2, 1), date(2026, 2, 13)),
        rows=tuple({"date": date(2026, 2, day), "period": (day - 1) // 2} for day in range(1, 13)),
        levels={"reporting_period": "period"},
    )
    authority = authority.model_copy(
        update={
            "grain": ("semantic", "sales.fiscal", "reporting_period"),
            "calendar_json": json.dumps(_snapshot_json(snapshot)),
            "scope": ("2026-02-02", "2026-02-08"),
        }
    )
    spec = replace(spec, definition=replace(spec.definition, fold_authority=authority.to_json()))
    frame = _frame([-4.0, 4.0, 0.0, 0.0])
    frame["order_time"] = [date(2026, 2, day) for day in (1, 3, 7, 9)]
    result, _ = execute_candidate(frame, spec)
    assert result.window_start.tolist() == [date(2026, 2, 1)]
    assert result.window_end.tolist() == [date(2026, 2, 3)]
    assert result.point_count.tolist() == [2]


@pytest.mark.parametrize(
    "gap,null,nonfinite",
    [(False, False, False), (True, False, False), (False, True, False), (False, False, True)],
)
@pytest.mark.parametrize("length", [35, 80])
def test_period_scores_and_paired_endpoints_match_independent_sliding_reference(
    gap: bool, null: bool, nonfinite: bool, length: int
) -> None:
    values: list[float | None] = [
        10.0 if length * 2 // 5 <= index < length * 3 // 5 else 0.0 for index in range(length)
    ]
    if null:
        values[15] = None
    if nonfinite:
        values[15] = float("inf")
    frame = _frame(values, period=True)
    if gap:
        frame = frame.drop(index=15).reset_index(drop=True)
    ordinals = frame.comparison_ordinal.tolist()
    numbers = frame.delta.tolist()
    window = max(7, len(frame) // 10)
    means: list[float | None] = []
    for end in range(len(frame)):
        start = end - window + 1
        items = numbers[max(0, start) : end + 1]
        valid = start >= 0 and ordinals[end] - ordinals[start] == window - 1
        valid = valid and all(value is not None and math.isfinite(value) for value in items)
        means.append(sum(items) / window if valid else None)
    available = [value for value in means if value is not None]
    mean, stddev, _ = _reference_scores(available)
    scores = [(value - mean) / stddev if value is not None else None for value in means]
    runs = _reference_runs(scores, ordinals, 1.0)
    expected = []
    for run in runs:
        peak = max(run, key=lambda index: abs(scores[index] or 0.0))
        score = scores[peak]
        assert score is not None
        expected.append((run[0], run[-1], abs(score), "high" if score > 0 else "low"))
    expected.sort(key=lambda item: (-item[2], item[0], item[1]))
    result, summary = execute_candidate(frame.iloc[::-1], _spec("period_shifts"))
    assert result.score.tolist() == pytest.approx([item[2] for item in expected])
    assert result.window_start.tolist() == [frame.current_time.iloc[item[0]] for item in expected]
    assert result.window_end.tolist() == [frame.current_time.iloc[item[1]] for item in expected]
    assert result.baseline_start.tolist() == [
        frame.baseline_time.iloc[item[0]] for item in expected
    ]
    assert result.baseline_end.tolist() == [frame.baseline_time.iloc[item[1]] for item in expected]
    assert result.direction.tolist() == [item[3] for item in expected]
    assert result.window_size.tolist() == [window] * len(expected)
    assert summary.evaluated_unit_count == len(available)
    assert summary.searched_unit_count == len(frame) - window + 1
    assert summary.baseline_mean_range == pytest.approx((mean, mean))


def test_period_minimum_two_complete_means_and_constant_status() -> None:
    for values in ([1.0] * 7, [1.0] * 8, [None] * 8, [1.0] * 7 + [None]):
        with pytest.raises(CandidateError, match="no evaluable series"):
            execute_candidate(_frame(values, period=True), _spec("period_shifts"))
    frame, summary = execute_candidate(
        _frame([0.0] * 7 + [1.0], period=True), _spec("period_shifts", threshold=0.9)
    )
    assert len(frame) == 1 and frame.direction.tolist() == ["low"]
    assert summary.evaluated_unit_count == 2
    assert frame.window_start.tolist() == [date(2026, 2, 7)]
    assert frame.window_end.tolist() == [date(2026, 2, 8)]


@pytest.mark.parametrize(
    "values,direction",
    [
        ([-7.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 7.0], "low"),
        ([7.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -7.0], "high"),
    ],
)
def test_period_exact_opposite_peak_tie_uses_earliest_comparison_ordinal(
    values: list[float], direction: str
) -> None:
    # The two complete seven-point means are -1, +1 (or +1, -1).
    # Their mean is 0, population variance is 1, and both scores are exactly 1.
    result, summary = execute_candidate(
        _frame(values, period=True).iloc[::-1], _spec("period_shifts", threshold=1.0)
    )
    assert result.window_start.tolist() == [date(2026, 2, 7)]
    assert result.window_end.tolist() == [date(2026, 2, 8)]
    assert result.baseline_start.tolist() == [date(2025, 12, 29)]
    assert result.baseline_end.tolist() == [date(2025, 12, 30)]
    assert result.window_size.tolist() == [7]
    assert result.direction.tolist() == [direction]
    assert result.score.tolist() == [1.0]
    assert result.peak_absolute_zscore.tolist() == [1.0]
    assert summary.evaluated_unit_count == 2
    assert summary.baseline_mean_range == (0.0, 0.0)
    assert summary.baseline_stddev_range == (1.0, 1.0)


@pytest.mark.parametrize("objective", ["interesting_windows", "period_shifts"])
def test_window_evaluated_empty_preserves_exact_typed_schema(objective: CandidateObjective) -> None:
    source = _frame(
        [-1.0, 0.0, 1.0] if objective == "interesting_windows" else [0.0] * 7 + [1.0],
        period=objective == "period_shifts",
    )
    populated, _ = execute_candidate(source, _spec(objective, threshold=0.5))
    empty, summary = execute_candidate(source, _spec(objective, threshold=100.0))
    assert populated.shape[0] > 0 and empty.empty
    assert summary.evaluated_series_count == 1 and summary.pre_limit_candidate_count == 0
    assert (
        pa.Schema.from_pandas(empty).remove_metadata()
        == pa.Schema.from_pandas(populated).remove_metadata()
    )


def test_period_unavailable_panel_does_not_erase_an_evaluated_panel() -> None:
    eligible = _frame([0.0] * 7 + [1.0], period=True)
    unavailable = _frame([None] * 8, period=True)
    eligible["channel"] = pd.Series(["eligible"] * 8, dtype="string[pyarrow]")
    unavailable["channel"] = pd.Series(["unavailable"] * 8, dtype="string[pyarrow]")
    result, summary = execute_candidate(
        pd.concat([eligible, unavailable], ignore_index=True),
        _spec("period_shifts", threshold=0.9, panel=True),
    )
    assert result.channel.tolist() == ["eligible"]
    assert summary.evaluated_series_count == 1 and summary.unavailable_series_count == 1
    assert summary.searched_unit_count == 4 and summary.evaluated_unit_count == 2


def test_identity_order_limit_and_complete_digest_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec(threshold=0.1, limit=1)
    source = _frame([-4.0, 0.0, 4.0])
    output, summary = execute_candidate(source, spec)
    shuffled, _ = execute_candidate(source.iloc[::-1], spec)
    assert output.equals(shuffled) and summary.pre_limit_candidate_count == 2
    assert output.time_coordinate.tolist() == [date(2026, 2, 1)]
    record: dict[str, object] = {
        field.name: output[field.name].iloc[0] for field in spec.output_row.schema.columns
    }
    identifier = candidate_item_id(spec.definition, spec.output_row, record)
    assert identifier == output.item_id.iloc[0] and identifier.startswith("sha256:")
    other_authority = replace(spec.definition, input_authority="different")
    assert candidate_item_id(other_authority, spec.output_row, record) != identifier
    other_limit = replace(spec.definition, limit=2)
    assert candidate_item_id(other_limit, spec.output_row, record) != identifier
    with pytest.raises(CandidateError, match="duplicate input coordinates"):
        execute_candidate(pd.concat([source, source.iloc[[1]]], ignore_index=True), spec)
    monkeypatch.setattr(
        "marivo.analysis.operators.candidate_values.candidate_item_id",
        lambda *args: "sha256:" + "0" * 64,
    )
    with pytest.raises(CandidateError, match="digest contradiction"):
        execute_candidate(source, spec)


def test_duplicate_period_candidate_keys_fail_before_discovery_limit() -> None:
    source = _frame([0.0] * 14 + [10.0] * 7 + [0.0] * 14, period=True)
    source["current_time"] = pd.Series(
        [date(2026, 2, 1)] * len(source), dtype=pd.ArrowDtype(pa.date32())
    )
    source["baseline_time"] = pd.Series(
        [date(2026, 1, 1)] * len(source), dtype=pd.ArrowDtype(pa.date32())
    )
    with pytest.raises(CandidateError, match="duplicate candidate keys"):
        execute_candidate(source, _spec("period_shifts", threshold=0.5, limit=1))


def test_cooperative_checkpoint_prevents_an_output() -> None:
    def cancelled() -> None:
        raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError):
        execute_candidate(_frame([-1.0, 0.0, 1.0]), _spec(), check=cancelled)
