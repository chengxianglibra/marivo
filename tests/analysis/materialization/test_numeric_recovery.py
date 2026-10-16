"""Source-process publication of row states and direct-only aggregates."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.shared_fixtures import DslCase, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


@pytest.mark.slow
def test_parquet_row_states_and_direct_only_results_publish_in_a_process(
    retained_coordinates_case: DslCase,
) -> None:
    parquet = True
    case = retained_coordinates_case
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + """
distinct = ms.aggregate(name='distinct', measure=amount, agg='count_distinct', time=ordered_at)
approx_distinct = ms.aggregate(name='approx_distinct', measure=amount, agg='approx_count_distinct', time=ordered_at)
quantile = ms.aggregate(name='quantile', measure=amount, agg=('percentile', 0.5), time=ordered_at)
approx_quantile = ms.aggregate(name='approx_quantile', measure=amount, agg=('approx_percentile', 0.5), time=ordered_at)
"""
    )
    if parquet:
        export_dsl_parquet_models(case, case.root)
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.analysis.materialization.numeric_recovery_worker import run_process; import sys; run_process(*sys.argv[1:])",
            str(case.root),
            case.session.id,
        ],
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads(process.stdout.splitlines()[-1])
    assert set(report["results"]) >= {"sum", "mean", "count", "quantile", "read", "category"}
