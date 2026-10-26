"""Explicit source observations retain exact values across terminal cold reads."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.analysis.numeric.rollup_worker import MONTH_EXPECTED
from tests.support.json import Json, checked, obj


def _run(mode: str, project: Path) -> dict[str, Json]:
    process = subprocess.run(
        [sys.executable, "-B", "-m", "tests.analysis.numeric.rollup_worker", mode, str(project)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "MARIVO_TELEMETRY": "off", "TZ": "Asia/Shanghai"},
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    return obj(checked(json.loads(process.stdout)))


@pytest.mark.release
@pytest.mark.slow
def test_decimal_and_int64_linear_cold_rollup_equal_warm(tmp_path: Path) -> None:
    produced = _run("produce", tmp_path)
    # Both original and month-grain outputs have already been committed. Their
    # terminal reads must survive absent models and source files independently.
    (tmp_path / "warehouse.duckdb").rename(tmp_path / "warehouse.offline")
    shutil.rmtree(tmp_path / "models")
    offline = _run("offline", tmp_path)
    cold = _run("cold", tmp_path)
    assert len({os.getpid(), produced["pid"], offline["pid"], cold["pid"]}) == 4
    assert produced["warm_monthly"] == MONTH_EXPECTED
    for reader in (offline, cold):
        assert reader["values"] == produced["warm_monthly"]
        assert reader["dtypes"] == {
            "net_amount": "decimal128(38, 0)[pyarrow]",
            "net_qty": "decimal128(38, 0)[pyarrow]",
            "amount_mean": "double[pyarrow]",
        }


def test_duckdb_decimal_mean_retains_captured_scale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source mean and saved Frame reads retain captured Decimal scale."""
    from decimal import Decimal

    import duckdb

    import marivo.analysis as mv
    from tests.analysis.numeric.rollup_worker import DAY_ROWS, author_decimal_mean_project

    project = author_decimal_mean_project(tmp_path)
    with duckdb.connect(str(project / "warehouse.duckdb")) as connection:
        connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", DAY_ROWS)
    monkeypatch.chdir(project)
    session = mv.session.get_or_create("equation-mean", timezone="UTC")
    query = session.observe(
        {"value": session.catalog.get("metric:sales.amount_mean").value},
        by=(session.time_scope(start="2026-07-01", end="2026-08-01"),),
    )
    result = query.persist()
    # Merge the original 54.14 sum and count 3, then finish at Decimal(12,2).
    for materialized in (result, session.persisted_frame(result.artifact.id)):
        values = materialized.to_pandas()["value"].tolist()
        assert values == [Decimal("18.05")]
        value = values[0]
        assert isinstance(value, Decimal)
        assert value.as_tuple().exponent == -2
