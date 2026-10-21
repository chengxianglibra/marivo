"""Public comparison output, nullable columns and independent cold recovery."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.frame_data import StoredFrameBacking
from marivo.analysis.errors import AnalysisError
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.shared_fixtures import DslCase, DslCaseFactory


def _monthly(case: DslCase, start: str, end: str, metric: str = "revenue") -> mv.AnalysisFrame:
    session = case.session
    members = session.members(session.catalog.entities.get("sales.customer"))
    return session.observe(
        {"value": session.catalog.metrics.get("sales." + metric)},
        by=mv.product(session.time_scope(start=start, end=end), members),
    )


def test_correspondence_factories_are_axis_free_and_checked() -> None:
    assert mv.ordinal() == mv.ordinal()
    assert mv.offset(periods=-1) == mv.offset(periods=-1)
    assert mv.offset(years=-1) == mv.offset(years=-1)
    with pytest.raises(AnalysisError):
        mv.offset(periods=True)
    with pytest.raises(TypeError):
        mv.ordinal(**{"trust": "declared"})


def test_comparison_owns_independent_nullable_columns(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.shared_fixtures import run_ids

    case = analysis_dsl_case_factory("j2")
    current = _monthly(case, "2026-09-01", "2026-10-01")
    baseline = _monthly(case, "2026-08-01", "2026-09-01")
    comparison = current.compare(
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
        match={"reference": mv.ordinal()},
    )
    assert isinstance(comparison, mv.AnalysisFrame)
    before = run_ids(case.session)
    comparison.show()
    assert "compute" in capsys.readouterr().out
    assert run_ids(case.session) == before
    assert "\n" not in repr(comparison)
    relative = comparison.select({"value": comparison["reference.relative.value"]})
    defined = relative.where(~relative["value"].is_null())
    total = defined.reduce({"value": defined["value"].sum()})
    result = comparison.persist()
    assert isinstance(result, mv.AnalysisFrame)
    exported = result.to_pandas()
    frame = exported.set_index(ordinary_columns(exported)[0])
    assert frame[("value", "value")].tolist() == [30, 200, 0, 0]
    assert frame[("value", "reference.baseline.value")].tolist() == [60, 120, 0, 0]
    assert frame[("value", "reference.absolute.value")].tolist() == [-30, 80, 0, 0]
    assert frame[("value", "reference.relative.value")].iloc[:2].tolist() == pytest.approx(
        [-0.5, 2 / 3]
    )
    assert frame[("value", "reference.relative.value")].iloc[2:].isna().all()
    assert frame["presence"].all().all()
    filtered = defined.compute().to_pandas()
    assert filtered[ordinary_columns(filtered)[0]].tolist() == ["A", "B"]
    assert total.compute().to_pandas()[("value", "value")].tolist() == pytest.approx([1 / 6])
    result.show()
    card = capsys.readouterr().out
    assert "reference.absolute.value" in card and "reference.relative.value" in card
    assert (
        result.artifact.evidence_digest()
        == case.session.persisted_frame(result.artifact.id).artifact.evidence_digest()
    )
    pd.testing.assert_frame_equal(
        case.session.persisted_frame(result.artifact.id).to_pandas(), exported
    )
    # Exported values can be analyzed without constructing another execution.
    assert frame[("value", "reference.relative.value")].dropna().sum() == pytest.approx(1 / 6)
    before = run_ids(case.session)
    with pytest.raises(AnalysisError):
        current.compare({"reference": result.artifact}, match={"reference": mv.ordinal()})  # type: ignore[arg-type]  # Audit views are not Frame operands.
    assert run_ids(case.session) == before
    same = (
        current.compare(
            {"reference": current},
            on={"reference": tuple(zip(current.axes, current.axes, strict=True))},
            match={"reference": mv.ordinal()},
        )
        .compute()
        .to_pandas()
    )
    assert same[("value", "reference.absolute.value")].tolist() == [0, 0, 0, 0]
    assert same[("value", "reference.relative.value")].iloc[:2].tolist() == [0.0, 0.0]
    assert same[("value", "reference.relative.value")].iloc[2:].isna().all()


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
    result = current_total.compare(
        {"reference": baseline_total},
        on={"reference": tuple(zip(current_total.axes, baseline_total.axes, strict=True))},
        match={"reference": mv.ordinal()},
    ).persist()
    before = run_ids(case.session)
    frame = result.to_pandas()
    assert frame[("value", "value")].tolist() == [3]
    assert frame[("value", "reference.baseline.value")].tolist() == [4]
    assert frame[("value", "reference.absolute.value")].tolist() == [-1]
    assert frame[("value", "reference.relative.value")].tolist() == [-0.25]
    position = frame.columns.get_loc(("value", "reference.absolute.value"))
    assert isinstance(position, int)
    frame.iloc[0, position] = 999
    assert result.to_pandas()[("value", "reference.absolute.value")].tolist() == [-1]
    assert run_ids(case.session) == before
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    script = """
import sys
from unittest.mock import patch
import duckdb
import marivo.analysis as mv
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import run_ids

def forbidden(*args, **kwargs):
    raise AssertionError('cold comparison accessed Semantic or business rows')
with patch.object(ms, 'load', forbidden), patch.object(SourceSession, '__init__', forbidden), patch.object(duckdb, 'connect', forbidden):
    session = mv.session.resume(sys.argv[1])
    before = run_ids(session)
    result = session.persisted_frame(sys.argv[2])
    assert isinstance(result, mv.AnalysisFrame)
    frame = result.to_pandas()
    assert frame[('value', 'value')].tolist() == [3]
    assert frame[('value', 'reference.baseline.value')].tolist() == [4]
    assert frame[('value', 'reference.absolute.value')].tolist() == [-1]
    assert frame[('value', 'reference.relative.value')].dropna().tolist() == [-0.25]
    assert run_ids(session) == before
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, case.session.id, result.artifact.id],
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
    frame = (
        current.compare(
            {"reference": baseline},
            on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
            match={"reference": mv.ordinal()},
        )
        .compute()
        .to_pandas()
    )
    frame = frame.set_index(ordinary_columns(frame)[0])
    assert frame.loc["C", ("value", "reference.absolute.value")] == Decimal("-0.05")
    assert frame.loc["C", ("value", "reference.relative.value")] == -1.0
    # A zero denominator rejects the whole comparison before any partial output.
    before = case.session.runs().items
    with pytest.raises(AnalysisError):
        current.compare(
            {"reference": baseline},
            zero_denominator="error",
            on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
            match={"reference": mv.ordinal()},
        ).compute()
    assert case.session.runs().items == before


def test_comparison_output_rejects_incomplete_and_forged_columns(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis.materialization.terminal_storage import read_output

    case = analysis_dsl_case_factory("j2")
    current = _monthly(case, "2026-09-01", "2026-10-01", "order_count")
    baseline = _monthly(case, "2026-08-01", "2026-09-01", "order_count")
    saved = current.compare(
        {"reference": baseline},
        on={"reference": tuple(zip(current.axes, baseline.axes, strict=True))},
        match={"reference": mv.ordinal()},
    ).persist()
    backing = saved._backing
    assert isinstance(backing, StoredFrameBacking)
    data = backing.data
    assert len(data.schema.values) == 4 and len(data.schema.presence) == 2
    with pytest.raises(AnalysisError):
        read_output(case.root, replace(data.schema, values=data.schema.values[:-1]), data.rows)
    with pytest.raises(AnalysisError):
        replace(data.schema, presence=(replace(data.schema.presence[0], nullable=True),))
    with pytest.raises(AnalysisError):
        replace(
            data.schema,
            values=(replace(data.schema.values[0], name="forged"), *data.schema.values[1:]),
        )
    assert saved.to_pandas()[("value", "reference.absolute.value")].tolist() == [0, 0, 0, -1]
