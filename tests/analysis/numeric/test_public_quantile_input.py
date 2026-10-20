"""Definition-owned native quantiles survive source-offline output recovery."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Literal

import duckdb
import pytest

import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory
from tests.support.execution_logs import execution_records
from tests.support.paths import PROJECT_ROOT


def test_observation_algorithm_override_is_not_public() -> None:
    assert not hasattr(ms, "quantile_metric")
    assert not hasattr(ms, "QuantileMetricInput")


@pytest.mark.parametrize("agg", ("median", "approx_median"))
def test_defined_quantile_survives_source_offline_recovery(
    analysis_dsl_case_factory: DslCaseFactory, agg: Literal["median", "approx_median"]
) -> None:
    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute("SET threads=1")
        db.execute('ALTER TABLE "order" ALTER amount TYPE DOUBLE')
        db.execute('DELETE FROM "order"')
        for index, amount in enumerate((125.25, 250.5)):
            db.execute(
                'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
                [str(index), "A", "web", "paid", "2026-08-15", amount],
            )
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + f"\n@ms.metric()\ndef median_amount():\n    return amount_value.{agg}(time=time_value)\n"
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    query = session.observe(
        {"value": session.catalog.metrics.get("sales.median_amount")},
        by=session.members(session.catalog.require(ms.ref.entity("sales.customer"))),
    )
    output = query.persist()
    exported = output.to_pandas()
    frame = exported.set_index(exported.columns[0])
    assert frame.loc["A", ("value", "value")] == 187.875
    assert frame.loc[["B", "C", "D"], ("value", "value")].isna().all()
    # The algorithm belongs to the definition and actual source statement.
    submitted = [
        str(record["sql"]).upper()
        for record in execution_records(case.root)
        if record["event"] == "query.submitted"
    ]
    expected = "APPROX_QUANTILE" if agg == "approx_median" else "QUANTILE_CONT"
    assert any(expected in sql for sql in submitted)
    # Original quantities are requested from the explicit source at the new grain.
    total = (
        session.observe({"value": session.catalog.metrics.get("sales.median_amount")})
        .compute()
        .to_pandas()
    )
    assert total[("value", "value")].tolist() == [187.875]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    script = """
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService
from tests.shared_fixtures import run_ids

def forbidden(*args, **kwargs):
    raise AssertionError('quantile output loaded Semantic or connected a source')
ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
session = mv.session.resume(sys.argv[1])
before = run_ids(session)
fixed = session.persisted_frame(sys.argv[2])
assert isinstance(fixed, mv.AnalysisFrame)
frame = fixed.to_pandas()
frame = frame.set_index(frame.columns[0])
assert frame.loc['A', ('value', 'value')] == 187.875
assert frame.loc[['B','C','D'], ('value','value')].isna().all()
assert len(frame) == 4
assert run_ids(session) == before
"""
    result = subprocess.run(
        [sys.executable, "-c", script, session.id, output.artifact.id],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
