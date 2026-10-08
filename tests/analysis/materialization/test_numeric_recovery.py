"""Public three-process recovery of row states and direct-only aggregates."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.shared_fixtures import DslCase, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


@pytest.mark.runtime
@pytest.mark.parametrize("parquet", [False, True])
def test_row_states_and_direct_only_results_recover_in_three_processes(
    retained_coordinates_case: DslCase, parquet: bool
) -> None:
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
    reports = []
    for phase in ("produce", "continue", "recover"):
        if phase == "continue":
            case.database_path.rename(case.database_path.with_suffix(".offline"))
            (case.root / "models").rename(case.root / "models.offline")
            if parquet:
                (case.root / "source_files").rename(case.root / "source_files.offline")
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                "from tests.analysis.materialization.numeric_recovery_worker import run_process; import sys; run_process(*sys.argv[1:])",
                str(case.root),
                case.session.id,
                phase,
            ],
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        reports.append(json.loads(process.stdout.splitlines()[-1]))
    assert len({report["pid"] for report in reports}) == 3
    assert reports[1]["results"] == reports[2]["results"]
