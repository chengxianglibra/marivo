"""Interpretation boundaries preserve values and source-free recovery."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.graph_dataset import _shared_column_boundaries, duration_facts
from tests.analysis.lifecycle.lifecycle_fixtures import START, build_lifecycle_public


@pytest.mark.parametrize(
    "unit,divisor", (("s", 1), ("ms", 1000), ("us", 1000000), ("ns", 1000000000))
)
def test_duration_disclosure_uses_actual_carrier(unit: str, divisor: int) -> None:
    assert dict(duration_facts(unit)) == {
        "duration_unit": unit,
        "duration_seconds": f"seconds = ticks / {divisor}",
    }


@pytest.mark.runtime
def test_interpretation_source_fixed_cold(tmp_path: Path) -> None:
    receipts = []
    for phase in ("produce", "fixed", "cold"):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.graph.interpretation_worker",
                str(tmp_path),
                phase,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        receipts.append(json.loads(result.stdout.splitlines()[-1]))
        if phase == "produce":
            for family in ("history", "statistics"):
                import shutil

                project = tmp_path / family
                shutil.rmtree(project / "models")
                for path in (*project.glob("*.duckdb*"), *project.rglob("*.parquet")):
                    if ".marivo" not in path.parts:
                        path.unlink()
    assert len({item["pid"] for item in receipts}) == 3


def test_shared_column_facts_keep_distinct_units_and_column_order() -> None:
    facts = dict(
        _shared_column_boundaries(
            (
                ("first", duration_facts("us")),
                ("second", duration_facts("ms")),
                ("third", duration_facts("us")),
            )
        )
    )
    assert facts["[first, third].duration_unit"] == "us"
    assert facts["[first, third].duration_seconds"] == "seconds = ticks / 1000000"
    assert facts["second.duration_unit"] == "ms"
    assert facts["second.duration_seconds"] == "seconds = ticks / 1000"


@pytest.mark.runtime
def test_history_duration_seconds_preserves_exact_microseconds(tmp_path: Path) -> None:
    session, members, _, claims, _ = build_lifecycle_public(
        tmp_path, rows=[(0, "started", 0, 1), (0, "paid", 86412.333333, 2)]
    )
    end = START + timedelta(days=2)
    history = session.lifecycle.replay(
        ms.ref.state_model("commerce.model"),
        population=members,
        window=mv.time_scope(start=START, end=end),
        seed=mv.from_inception(),
        completeness=tuple(replace(claim, complete_through=end) for claim in claims),
    ).execute()
    logical_dwell = history.dwell()
    expected_units = {
        name + "." + key: value
        for name in ("mean_duration", "median_duration", "p90_duration")
        for key, value in (
            ("duration_unit", "us"),
            ("duration_seconds", "seconds = ticks / 1000000"),
        )
    }
    assert expected_units.items() <= dict(logical_dwell.contract()._facts).items()
    dwell = logical_dwell.execute()
    assert expected_units.items() <= dict(dwell.contract()._facts).items()
    duration = dwell.to_pandas().set_index("model_state")["mean_duration"]["open"]
    assert duration == timedelta(microseconds=86412333333)
    assert duration.total_seconds() == 86412.333333
    assert (
        dict(dwell.mean_duration.contract()._facts)["duration_seconds"]
        == "seconds = ticks / 1000000"
    )
