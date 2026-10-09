"""Definition-owned quantile identity survives source-offline Store 7 recovery."""

from __future__ import annotations

import os
import subprocess
import sys

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.ir import AggKind
from tests.shared_fixtures import DslCaseFactory
from tests.support.paths import PROJECT_ROOT


def test_observation_algorithm_override_is_not_public() -> None:
    assert not hasattr(ms, "quantile_metric")
    assert not hasattr(ms, "QuantileMetricInput")


@pytest.mark.runtime
@pytest.mark.parametrize(
    "agg, algorithm", [("median", "QUANTILE_CONT"), ("approx_median", "T-Digest")]
)
def test_defined_quantile_survives_source_offline_recovery(
    analysis_dsl_case_factory: DslCaseFactory, agg: AggKind, algorithm: str
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
        + f"\nmedian_amount = ms.aggregate(name='median_amount', measure=amount, agg={agg!r}, time=ordered_at)\n"
    )
    ms.load(workspace_dir=case.root)
    output = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            ms.ref.metric("sales.median_amount"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .execute()
    )
    assert output.to_pandas().set_index("member").loc["A", "value"] == 187.875
    assert algorithm in dict(output.contract()._facts)["algorithm"]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    script = """
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.analysis.errors import AnalysisError

def forbidden(*args, **kwargs):
    raise AssertionError('retained quantile cannot load Semantic or connect a source')

ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
fixed = mv.session.resume(sys.argv[1], by='id').artifact(sys.argv[2])
assert isinstance(fixed, mv.MaterializedNumericRelation)
assert fixed.to_pandas().set_index('member').loc['A', 'value'] == 187.875
assert sys.argv[3] in dict(fixed.contract()._facts)['algorithm']
assert not any(action.call == 'relation.rollup()' for action in fixed.contract().actions)
try:
    fixed.rollup()
except AnalysisError:
    pass
else:
    raise AssertionError('quantile must not grant original rollup')
assert fixed.aggregate(mv.count()).execute().to_pandas()['value'].tolist() == [4]
"""
    result = subprocess.run(
        [sys.executable, "-c", script, case.session.id, output.state.artifact_ref.ref, algorithm],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
