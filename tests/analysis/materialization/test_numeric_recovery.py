"""Native numeric producers and terminal output survive independent processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.shared_fixtures import DslCase, export_dsl_parquet_models
from tests.support.paths import PROJECT_ROOT


@pytest.mark.slow
def test_parquet_row_statistics_and_native_results_publish_in_a_process(
    retained_coordinates_case: DslCase,
) -> None:
    case = retained_coordinates_case
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + """
@ms.metric()
def distinct():
    return amount_value.count_distinct(time=time_value)
@ms.metric()
def approx_distinct():
    return amount_value.approx_nunique(time=time_value)
@ms.metric()
def quantile():
    return amount_value.quantile(0.5, time=time_value)
@ms.metric()
def approx_quantile():
    return amount_value.approx_quantile(0.5, time=time_value)
"""
    )
    export_dsl_parquet_models(case, case.root)
    reports = []
    for phase in ("produce", "offline", "cold"):
        if phase == "offline":
            (case.root / "source_files").rename(case.root / "source_files.offline")
            (case.root / "models").rename(case.root / "models.offline")
            case.database_path.rename(case.database_path.with_suffix(".offline"))
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
        report = json.loads(process.stdout.splitlines()[-1])
        assert set(report["results"]) >= {"sum", "mean", "count", "quantile", "read", "category"}
        reports.append(report)
    assert len({os.getpid(), *(report["pid"] for report in reports)}) == 4
