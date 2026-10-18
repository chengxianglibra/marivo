"""Public comparison output, nullable columns and independent cold recovery."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCase, DslCaseFactory


def _monthly(case: DslCase, start: str, end: str, metric: str = "revenue") -> mv.Query:
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    return session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)},
        during=session.time_scope(start=start, end=end),
        by=members,
    )


def test_correspondence_factories_are_axis_free_and_checked() -> None:
    assert mv.ordinal() == mv.ordinal()
    assert mv.period_offset(-1) == mv.period_offset(by=-1)
    assert mv.previous_year() == mv.previous_year()
    with pytest.raises(AnalysisError):
        mv.period_offset(True)
    with pytest.raises(TypeError):
        mv.ordinal(**{"trust": "declared"})


def test_comparison_owns_independent_nullable_columns(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.shared_fixtures import run_ids

    case = analysis_dsl_case_factory("j2")
    current = _monthly(case, "2026-09-01", "2026-10-01")
    baseline = _monthly(case, "2026-08-01", "2026-09-01")
    comparison = current.compare(baseline)
    assert isinstance(comparison, mv.Query)
    before = run_ids(case.session)
    comparison.show()
    assert "execute" in capsys.readouterr().out
    assert run_ids(case.session) == before
    assert "\n" not in repr(comparison)
    relative = comparison.select({"value": comparison["value.relative"]})
    defined = relative.where(~relative["value"].is_null())
    total = defined.reduce({"value": defined["value"].sum()})
    result = comparison.execute()
    assert isinstance(result, mv.Artifact)
    exported = result.to_pandas()
    frame = exported.set_index(exported.columns[0])
    assert frame[("value", "value.current")].tolist() == [30, 200, 0, 0]
    assert frame[("value", "value.baseline")].tolist() == [60, 120, 0, 0]
    assert frame[("value", "value.absolute")].tolist() == [-30, 80, 0, 0]
    assert frame[("value", "value.relative")].iloc[:2].tolist() == pytest.approx([-0.5, 2 / 3])
    assert frame[("value", "value.relative")].iloc[2:].isna().all()
    assert frame["presence"].all().all()
    filtered = defined.execute().to_pandas()
    assert filtered.iloc[:, 0].tolist() == ["A", "B"]
    assert total.execute().to_pandas()[("value", "value")].tolist() == pytest.approx([1 / 6])
    result.show()
    card = capsys.readouterr().out
    assert "value.absolute" in card and "value.relative" in card
    assert result.evidence_digest() == case.session.artifact(result.id).evidence_digest()
    pd.testing.assert_frame_equal(case.session.artifact(result.id).to_pandas(), exported)
    # Exported values can be analyzed without constructing another execution.
    assert frame[("value", "value.relative")].dropna().sum() == pytest.approx(1 / 6)
    before = run_ids(case.session)
    with pytest.raises(AnalysisError):
        current.compare(result)  # type: ignore[arg-type]  # Exercise the runtime Artifact boundary.
    assert run_ids(case.session) == before
    same = current.compare(current).execute().to_pandas()
    assert same[("value", "value.absolute")].tolist() == [0, 0, 0, 0]
    assert same[("value", "value.relative")].iloc[:2].tolist() == [0.0, 0.0]
    assert same[("value", "value.relative")].iloc[2:].isna().all()


@pytest.mark.slow
def test_comparison_columns_recover_without_sources(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import os
    import subprocess
    import sys

    from tests.shared_fixtures import run_ids
    from tests.support.paths import PROJECT_ROOT

    case = analysis_dsl_case_factory("j2")
    current = _monthly(case, "2026-09-01", "2026-10-01", "order_count")
    baseline = _monthly(case, "2026-08-01", "2026-09-01", "order_count")
    current_total = current.reduce({"value": current["value"].sum()})
    baseline_total = baseline.reduce({"value": baseline["value"].sum()})
    result = current_total.compare(baseline_total).execute()
    before = run_ids(case.session)
    frame = result.to_pandas()
    assert frame[("value", "value.current")].tolist() == [3]
    assert frame[("value", "value.baseline")].tolist() == [4]
    assert frame[("value", "value.absolute")].tolist() == [-1]
    assert frame[("value", "value.relative")].tolist() == [-0.25]
    position = frame.columns.get_loc(("value", "value.absolute"))
    assert isinstance(position, int)
    frame.iloc[0, position] = 999
    assert result.to_pandas()[("value", "value.absolute")].tolist() == [-1]
    assert run_ids(case.session) == before
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    script = """
import sys
from unittest.mock import patch
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import run_ids

def forbidden(*args, **kwargs):
    raise AssertionError('cold comparison accessed Semantic or business rows')
with patch.object(ms, 'load', forbidden), patch.object(SourceSession, '__init__', forbidden), patch.object(duckdb, 'connect', forbidden):
    session = mv.session.resume(sys.argv[1], by='id')
    before = run_ids(session)
    result = session.artifact(sys.argv[2])
    assert isinstance(result, mv.Artifact)
    frame = result.to_pandas()
    assert frame[('value', 'value.current')].tolist() == [3]
    assert frame[('value', 'value.baseline')].tolist() == [4]
    assert frame[('value', 'value.absolute')].tolist() == [-1]
    assert frame[('value', 'value.relative')].dropna().tolist() == [-0.25]
    assert run_ids(session) == before
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, case.session.id, result.id],
        cwd=case.root,
        env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), MARIVO_TELEMETRY="off"),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_decimal_absolute_keeps_precision_and_relative_uses_float(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            'ALTER TABLE "order" ALTER amount TYPE DECIMAL(38,38) '
            "USING CAST(amount * 0.001 AS DECIMAL(38,38))"
        )
    current = _monthly(case, "2026-08-01", "2026-09-01")
    baseline = _monthly(case, "2026-07-01", "2026-08-01")
    frame = current.compare(baseline).execute().to_pandas()
    frame = frame.set_index(frame.columns[0])
    assert frame.loc["C", ("value", "value.absolute")] == Decimal("-0.05")
    assert frame.loc["C", ("value", "value.relative")] == -1.0
    # A zero denominator rejects the whole comparison before any partial output.
    before = case.session.graph().artifacts
    with pytest.raises(AnalysisError):
        current.compare(baseline, zero_denominator="error").execute()
    assert case.session.graph().artifacts == before


def test_comparison_output_rejects_incomplete_and_forged_columns(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.materialization.terminal_storage import read_output

    case = analysis_dsl_case_factory("j2")
    current = _monthly(case, "2026-09-01", "2026-10-01", "order_count")
    baseline = _monthly(case, "2026-08-01", "2026-09-01", "order_count")
    saved = current.compare(baseline).execute()
    data = saved._manifest.data
    assert len(data.schema.values) == 4 and len(data.schema.presence) == 2
    with pytest.raises(AnalysisError):
        read_output(case.root, replace(data.schema, values=data.schema.values[:-1]), data.rows)
    with pytest.raises(AnalysisError):
        replace(data.schema, presence=(replace(data.schema.presence[0], nullable=True),))
    with pytest.raises(AnalysisError):
        replace(
            data,
            coordinate_projections=(replace(data.coordinate_projections[0], columns=("forged",)),),
        )
    assert saved.to_pandas()[("value", "value.absolute")].tolist() == [0, 0, 0, -1]
