"""Public dual comparison journeys with independent fixture-fact expectations."""

from __future__ import annotations

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory


def test_alignment_has_checked_exact_and_default_keep() -> None:
    assert mv.outer() == mv.outer(missing="keep")
    assert mv.exact(time=mv.ordinal()).time == mv.ordinal()
    assert not hasattr(mv.exact(), "verification")
    with pytest.raises(TypeError):
        mv.exact(**{"trust": "declared"})


def test_comparison_owns_independent_cells_and_typed_views(
    analysis_dsl_case_factory: DslCaseFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.shared_fixtures import run_ids

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    metric, via = ms.ref.metric("sales.revenue"), ms.ref.relationship("sales.order_buyer")
    current = members.observe(
        metric,
        during=mv.time_scope(start="2026-09-01", end="2026-10-01"),
        via=via,
        by=(mv.member(),),
    )
    baseline = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=via,
        by=(mv.member(),),
    )
    assert isinstance(current, mv.LogicalNumericRelation)
    assert isinstance(baseline, mv.LogicalNumericRelation)
    first, second = current, baseline
    comparison = first.compare(second)
    assert isinstance(comparison, mv.LogicalComparisonResult)
    assert isinstance(comparison.absolute, mv.LogicalDifferenceRelation)
    assert isinstance(comparison.relative, mv.LogicalNumericRelation)
    assert not hasattr(comparison, "value")
    before = run_ids(case.session)
    comparison.show()
    logical_card = capsys.readouterr().out
    assert "absolute" in logical_card and "relative" in logical_card
    assert "absolute_unit" in logical_card and "relative_unit" in logical_card
    assert run_ids(case.session) == before
    assert "design=time" in repr(comparison) and "\n" not in repr(comparison)
    result = comparison.execute()
    assert isinstance(result, mv.MaterializedComparisonResult)
    frame = result.to_pandas().set_index("member")
    assert frame.loc["A", "absolute"] == -30
    assert frame.loc["A", "relative"] == -0.5
    assert frame.loc["C", "absolute_cell_tag"] == "defined"
    assert frame.loc["C", "absolute"] == 0
    assert frame.loc["C", "relative_cell_reason"] == "zero_baseline"
    assert frame.loc["D", "absolute_cell_reason"] == "empty_contribution"
    assert frame.loc["D", "relative_cell_reason"] == "empty_contribution"
    assert isinstance(result.absolute, mv.MaterializedDifferenceRelation)
    assert isinstance(result.relative, mv.MaterializedNumericRelation)
    for view in (comparison.absolute, comparison.relative, result.absolute, result.relative):
        calls = tuple(action.call for action in view.contract().actions)
        assert any(call.startswith("relation.where(") for call in calls)
        assert any(call.startswith("relation.aggregate(") for call in calls)
    result.show()
    materialized_card = capsys.readouterr().out
    assert "absolute" in materialized_card and "relative" in materialized_card
    assert "absolute_unit" in materialized_card and "relative_unit" in materialized_card
    assert (
        result.absolute.evidence_digest()
        == result.evidence_digest()
        == result.relative.evidence_digest()
    )
    assert result.absolute.to_pandas().set_index("member").loc["A", "value"] == -30
    assert result.relative.to_pandas().set_index("member").loc["A", "value"] == -0.5
    restored = case.session.artifact(result.state.artifact_ref)
    assert isinstance(restored, mv.MaterializedComparisonResult)
    assert restored.to_pandas().equals(result.to_pandas())
    absolute, relative = comparison.absolute, comparison.relative
    assert sorted(
        relative.where(relative.value.is_defined()).execute().to_pandas()["member"].tolist()
    ) == ["A", "B"]
    defined = relative.where(relative.value.is_defined())
    # Fixture facts give A=(30-60)/60=-1/2 and B=(200-120)/120=2/3.
    assert defined.aggregate(mv.sum()).execute().to_pandas()["value"].tolist() == pytest.approx(
        [1 / 6]
    )
    fixed = current.execute().compare(baseline.execute()).execute()
    assert isinstance(fixed, mv.MaterializedComparisonResult)
    assert fixed.to_pandas().equals(result.to_pandas())
    fixed_relative = fixed.relative.where(fixed.relative.value.is_defined())
    assert fixed_relative.aggregate(mv.sum()).execute().to_pandas()[
        "value"
    ].tolist() == pytest.approx([1 / 6])
    with pytest.raises(AnalysisError):
        first.compare(first)


@pytest.mark.slow
def test_dual_artifact_and_views_recover_without_sources(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import os
    import subprocess
    import sys

    from tests.shared_fixtures import run_ids
    from tests.support.paths import PROJECT_ROOT

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    channel = ms.ref.dimension("sales.order.channel")
    current = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-09-01", end="2026-10-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(), channel),
    )
    baseline = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(), channel),
    )
    result = current.rollup().compare(baseline.rollup()).execute()
    before = run_ids(case.session)
    assert result.absolute.state.artifact_ref == result.state.artifact_ref
    assert result.relative.state.artifact_ref == result.state.artifact_ref
    result.absolute.to_pandas()
    result.relative.to_pandas()
    assert run_ids(case.session) == before
    frame = result.to_pandas()
    frame.iloc[0, frame.columns.get_loc("absolute")] = 999
    assert result.to_pandas().iloc[0]["absolute"] != 999
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    (case.root / "models").rename(case.root / "models.offline")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from unittest.mock import patch
import duckdb
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from tests.shared_fixtures import run_ids

def forbidden(*args, **kwargs):
    raise AssertionError("cold dual result cannot read sources")

with patch.object(ms, "load", forbidden), patch.object(SourceSession, "__init__", forbidden), patch.object(duckdb, "connect", forbidden):
    session = mv.session.resume(sys.argv[1], by="id")
    result = session.artifact(sys.argv[2])
    assert isinstance(result, mv.MaterializedComparisonResult)
    before = run_ids(session)
    root = result.to_pandas()
    absolute = result.absolute
    relative = result.relative
    assert isinstance(absolute, mv.MaterializedDifferenceRelation)
    assert isinstance(relative, mv.MaterializedNumericRelation)
    assert absolute.to_pandas().iloc[0]["value"] == root.iloc[0]["absolute"]
    assert relative.to_pandas().iloc[0]["value"] == root.iloc[0]["relative"]
    assert run_ids(session) == before
    defined = relative.where(relative.value.is_defined()).execute()
    assert len(defined.to_pandas()) == 1
""",
            case.session.id,
            result.state.artifact_ref.ref,
        ],
        cwd=case.root,
        env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), MARIVO_TELEMETRY="off"),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_relative_decimal_overflow_prevents_absolute_publication(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    from tests.analysis.materialization.publication_fixtures import publication_counts

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            'ALTER TABLE "order" ALTER amount TYPE DECIMAL(38,38) '
            "USING CAST(amount * 0.001 AS DECIMAL(38,38))"
        )
    members = case.session.members(ms.ref.entity("sales.customer"))
    metric, via = ms.ref.metric("sales.revenue"), ms.ref.relationship("sales.order_buyer")
    current = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=via,
        by=(mv.member(),),
    )
    baseline = members.observe(
        metric,
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=via,
        by=(mv.member(),),
    )
    for fixed in (False, True):
        left, right = (current.execute(), baseline.execute()) if fixed else (current, baseline)
        before = publication_counts(case.session)
        # C has absolute=-0.05 and relative=-1, outside DECIMAL(38,38)'s range.
        with pytest.raises(AnalysisError, match="relative comparison finish failed"):
            left.compare(right).absolute.execute()
        assert publication_counts(case.session) == before


def test_dual_exchange_rejects_incomplete_or_forged_views(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from dataclasses import replace

    from marivo.analysis.materialization.cell_arrow import logical_table
    from marivo.analysis.materialization.errors import MaterializationError
    from marivo.analysis.materialization.graph_exchange import from_arrow

    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    current = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-09-01", end="2026-10-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    baseline = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
        by=(mv.member(),),
    )
    saved = current.compare(baseline).execute()
    assert saved._dataset is not None
    checked = saved._dataset.verified()
    primary, parts = logical_table(checked.primary), checked.parts
    parts = tuple(part for part in parts if part.role != "comparison")
    with pytest.raises(MaterializationError, match="missing, reordered or extra method state part"):
        from_arrow(
            primary,
            replace(checked.contract, schema=primary.schema, _frozen=None),
            parts=parts,
            completed_checks=checked.completed_checks,
        )
