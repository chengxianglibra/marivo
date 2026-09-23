"""Shared assertions for equivalent backend and recovery scenarios."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest


def assert_status_fold_values(
    values: Sequence[float],
    expected: Sequence[float],
    statements: Sequence[tuple[str, str]],
) -> None:
    assert list(values) == pytest.approx(expected)
    assert any(
        role == "validation_batch" and "__mv_status" in statement for role, statement in statements
    )


def assert_primary_status_gate(statements: Sequence[tuple[str, str]]) -> None:
    primary = [statement for role, statement in statements if role == "primary"]
    assert len(primary) == 1 and "__mv_status" in primary[0]


def assert_relationship_values(regions: Sequence[str], means: Sequence[float]) -> None:
    assert list(regions) == ["EU", "US"]
    assert list(means) == [15.0, 35.0]


def assert_date_bucket_values(unit: str, rows: Sequence[tuple[str, float]]) -> None:
    expected = {
        "day": [
            ("2026-02-01", 10.0),
            ("2026-02-02", 20.0),
            ("2026-02-03", 30.0),
            ("2026-02-04", 40.0),
        ],
        "week": [("2026-01-26", 10.0), ("2026-02-02", 90.0)],
        "month": [("2026-02-01", 100.0)],
        "quarter": [("2026-01-01", 100.0)],
        "year": [("2026-01-01", 100.0)],
    }
    assert list(rows) == expected[unit]


def assert_version_identities(identities: Sequence[tuple[int, ...]]) -> None:
    assert sorted(identities) == [(1,), (2,)]


def assert_forecast_history(
    forecast_values: Sequence[float], training_row_counts: Sequence[int], transferred_rows: int
) -> None:
    assert list(forecast_values) == [40.0, 40.0]
    assert list(training_row_counts) == [4, 4]
    assert transferred_rows == 4


def assert_kendall_source_reduction(
    coefficients: Sequence[float], transferred_rows: int, local_execution_count: int
) -> None:
    assert list(coefficients) == [1.0]
    assert transferred_rows == 4
    assert local_execution_count > 0


def assert_time_discovery(row_count: int, transferred_rows: int) -> None:
    assert row_count == 2
    assert transferred_rows == 4


def assert_dimension_comparison(
    channels: Sequence[str], baseline_value: float, current_value: float, deltas_are_null: bool
) -> None:
    assert list(channels) == ["a", "b"]
    assert baseline_value == 15.0
    assert current_value == 35.0
    assert deltas_are_null


def assert_cross_root_ratio(
    revenue: Sequence[float], line_revenue: Sequence[float], ratio: Sequence[float]
) -> None:
    assert list(revenue) == [100.0]
    assert list(line_revenue) == [65.0]
    assert list(ratio) == [0.65]


def assert_missing_relationship_values(
    unmatched_revenue: Sequence[float], eu_revenue: Sequence[float]
) -> None:
    assert list(unmatched_revenue) == [70.0]
    assert list(eu_revenue) == [30.0]


def assert_no_dataset_artifacts(artifact_count: int) -> None:
    assert artifact_count == 0


def assert_weighted_mean_values(
    values: Sequence[float], is_null: bool, expected: float | None
) -> None:
    if expected is None:
        assert is_null
    else:
        assert list(values) == [expected]


def assert_retained_axis_attribution(
    contributions: Sequence[float], overall_deltas: Sequence[float]
) -> None:
    assert list(contributions) == [20.0]
    assert list(overall_deltas) == [20.0]


RecoveryWorker = Callable[[str, Path, str, str], subprocess.CompletedProcess[str]]


def assert_producer_death_recovers(
    worker: RecoveryWorker, project: Path, source_table: str, point: str
) -> None:
    process = worker("produce", project, source_table, point)
    assert process.returncode == 73, process.stdout + process.stderr
    crashed = json.loads((project / "crash.json").read_text())
    recovered = worker("recover", project, source_table, "")
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["pid"] != crashed["pid"]
    committed = point == "after_commit"
    assert result["lifecycle"] == ("succeeded" if committed else "failed")
    assert result["before"]["dataset_artifacts"] == int(committed)
    assert result["before"]["analysis_action_runs"] == 1


def assert_composed_parts_recover(
    worker: RecoveryWorker, project: Path, source_table: str, point: str
) -> None:
    process = worker("produce", project, source_table, point + ":ratio")
    assert process.returncode == 73, process.stdout + process.stderr
    recovered = worker("recover", project, source_table, "")
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    result = json.loads(recovered.stdout)
    committed = point == "after_commit"
    assert result["lifecycle"] == ("succeeded" if committed else "failed")
    assert result["before"]["dataset_artifacts"] == int(committed)
