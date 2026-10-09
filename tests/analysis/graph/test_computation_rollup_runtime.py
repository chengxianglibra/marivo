"""Original-state rollup checks for linear and decimal retained metrics.

The cold-process case compares warm and recovered rollups for its chosen linear
and int64 mean inputs, preserving their value types with the source offline.
The decimal mean case checks fixed rollup and Artifact reads at the captured
Ibis output scale. Ordinary Metric mean follows its inferred output type;
source execution and retained sum/count continuation may round differently.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.analysis.numeric.rollup_worker import MONTH_EXPECTED

pytestmark = pytest.mark.runtime


def _run(mode: str, project: Path, artifact: str = "") -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.analysis.numeric.rollup_worker",
            mode,
            str(project),
            artifact,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "MARIVO_TELEMETRY": "off", "TZ": "Asia/Shanghai"},
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    value = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


def test_decimal_and_int64_linear_cold_rollup_equal_warm(tmp_path: Path) -> None:
    produced = _run("produce", tmp_path)
    cold = _run("continue", tmp_path, str(produced["artifact"]))
    assert produced["pid"] != cold["pid"]
    # Hand-computed month totals: gmv 54.14, linear decimal 3.85, linear int 3,
    # and the float mean as the quantity mean division 5 / 3.
    assert produced["warm_monthly"] == MONTH_EXPECTED
    assert cold["values"] == produced["warm_monthly"]
    assert cold["dtypes"] == {
        "gmv": "object",
        "net_amount": "object",
        "net_qty": "int64",
        "amount_mean": "float64",
    }


def test_duckdb_decimal_mean_retains_captured_scale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixed Metric mean and Artifact reads preserve the captured Decimal scale."""
    from decimal import Decimal

    import duckdb

    import marivo.analysis as mv
    import marivo.semantic as ms
    from tests.analysis.numeric.rollup_worker import DAY_ROWS, author_decimal_mean_project

    project = author_decimal_mean_project(tmp_path)
    with duckdb.connect(str(project / "warehouse.duckdb")) as connection:
        connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", DAY_ROWS)
    monkeypatch.chdir(project)
    session = mv.session.get_or_create("equation-mean", report_timezone="UTC")
    fixed = (
        session.members(ms.ref.entity("sales.orders"))
        .observe(ms.ref.metric("sales.amount_mean"), by=(ms.ref.entity("sales.orders"),))
        .execute()
    )
    result = fixed.rollup().execute()
    # Merge the original 54.14 sum and count 3, then finish at Decimal(12,2).
    for materialized in (result, session.artifact(result.state.artifact_ref)):
        values = materialized.to_pandas()["value"].tolist()
        assert values == [Decimal("18.05")]
        value = values[0]
        assert isinstance(value, Decimal)
        assert value.as_tuple().exponent == -2
