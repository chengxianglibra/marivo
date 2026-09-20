"""Cold-process rollup equality for linear and decimal retained metrics.

The plan's rollup-equation mandate (C4 §5.4) requires warm aggregate results to
equal cold-process rollup results exactly, with the original value types. The
three-process worker authors a real project, persists a checkpoint, renames the
source database away, and rolls the checkpoint up in a fresh process under a
different host timezone. MySQL's decimal mean keeps its admission rejection
this stage: the live mean-equation probe measured the engine's AVG scale-s+4
rounding as ROUND_HALF_UP while the prescribed cold sum/count quantization is
ROUND_HALF_EVEN, so the bit-exact warm==cold contract cannot hold (see the
task 4 report for the raw probe rows).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.lazy_rollup_equation_worker import MONTH_EXPECTED

pytestmark = pytest.mark.runtime


def _run(mode: str, project: Path, artifact: str = "") -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.lazy_rollup_equation_worker",
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
        "gmv": "decimal128(38, 2)[pyarrow]",
        "net_amount": "decimal128(38, 2)[pyarrow]",
        "net_qty": "int64[pyarrow]",
        "amount_mean": "double[pyarrow]",
    }
    assert cold["day_label"] == "2026-07-01"


def test_duckdb_decimal_mean_stays_rejected_with_unresolved_type_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DuckDB decimal-mean cell stays closed: AVG is DOUBLE, not Decimal.

    The engine's native AVG over DECIMAL publishes an unresolved double, which
    the declared-cast transport rule refuses; the structured error names the
    missing resolved Decimal fact instead of silently publishing a float.
    """
    import marivo.analysis as mv
    import marivo.semantic as ms
    from marivo.analysis.compiler.errors import DatasetCompilationError
    from tests.lazy_rollup_equation_worker import author_decimal_mean_project

    project = author_decimal_mean_project(tmp_path)
    monkeypatch.chdir(project)
    session = mv.session.get_or_create("equation-mean", report_timezone="UTC")
    logical = session.observe(ms.ref.metric("sales.amount_mean")).aggregate()
    with pytest.raises(DatasetCompilationError, match="resolved exact Decimal precision"):
        logical.execute()
    assert (project / "marivo.toml").is_file()
