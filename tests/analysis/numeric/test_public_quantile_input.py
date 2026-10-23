"""Definition-owned native quantiles survive source-offline output recovery."""

from __future__ import annotations

import os
import subprocess
import sys

import duckdb

import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory
from tests.support.execution_logs import execution_records
from tests.support.paths import PROJECT_ROOT


def test_observation_algorithm_override_is_not_public() -> None:
    assert not hasattr(ms, "quantile_metric")
    assert not hasattr(ms, "QuantileMetricInput")


def test_defined_quantiles_survive_source_offline_recovery(
    analysis_dsl_case_factory: DslCaseFactory,
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
        + "\n@ms.metric()\ndef median_amount():\n"
        + "    return amount_value.median(time=time_value)\n"
        + "\n@ms.metric()\ndef approximate_median_amount():\n"
        + "    return amount_value.approx_median(time=time_value)\n"
        + "\n@ms.metric()\ndef approximate_p95_amount():\n"
        + "    return amount_value.approx_quantile(0.95, time=time_value)\n"
    )
    ms.load(workspace_dir=case.root)
    session = case.session
    query = session.observe(
        {
            "median": session.catalog.metrics.get("sales.median_amount"),
            "approximate_median": session.catalog.metrics.get("sales.approximate_median_amount"),
            "approximate_p95": session.catalog.metrics.get("sales.approximate_p95_amount"),
        },
        by=session.members(session.catalog.entities.get("sales.customer")),
    )
    output = query.persist()
    exported = output.to_pandas()
    frame = exported.set_index(exported.columns[0])
    for name in ("median", "approximate_median"):
        assert frame.loc["A", ("value", name)] == 187.875
        assert frame.loc[["B", "C", "D"], ("value", name)].isna().all()
    assert frame.loc["A", ("value", "approximate_p95")] == 250.5
    # The algorithm belongs to the definition and actual source statement.
    submitted = [
        str(record["sql"]).upper()
        for record in execution_records(case.root)
        if record["event"] == "query.submitted"
    ]
    assert any("QUANTILE_CONT" in sql for sql in submitted)
    assert any("APPROX_QUANTILE" in sql for sql in submitted)
    # Original quantities are requested from the explicit source at the new grain.
    total = (
        session.observe(
            {
                "median": session.catalog.metrics.get("sales.median_amount"),
                "approximate_median": session.catalog.metrics.get(
                    "sales.approximate_median_amount"
                ),
            }
        )
        .compute()
        .to_pandas()
    )
    assert total[("value", "median")].tolist() == [187.875]
    assert total[("value", "approximate_median")].tolist() == [187.875]
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
for name in ('median', 'approximate_median'):
    assert frame.loc['A', ('value', name)] == 187.875
    assert frame.loc[['B', 'C', 'D'], ('value', name)].isna().all()
assert len(frame) == 4
assert frame.loc['A', ('value', 'approximate_p95')] == 250.5
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
