"""Regression counterexamples from the adversarial review."""

from datetime import datetime, timedelta
from fractions import Fraction
from typing import Literal

import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.time_grid import bind_grid, continuation
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_exchange import from_arrow
from marivo.analysis.materialization.statistical_execution import _verify_ranks
from marivo.analysis.methods.deviation_numeric import RationalFact
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
@pytest.mark.parametrize(
    "method", ("pearson", "spearman", "kendall", "naive", "drift", "seasonal_naive")
)
def test_ratio_statistics_require_original_coverage(
    analysis_dsl_case_factory: DslCaseFactory,
    method: Literal["pearson", "spearman", "kendall", "naive", "drift", "seasonal_naive"],
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-07-01", end="2026-10-01"), grain=mv.grain("month")
    )
    history = (
        members.each(grid)
        .observe(
            ms.ref.metric("sales.order_count"),
            during=grid.window,
            via=ms.ref.relationship("sales." + case.names.buyer),
            by=(ms.ref.entity("sales.customer"),),
        )
        .group_by(grid)
        .rollup()
        .execute()
    )
    ratio = history.ratio(history).execute()
    assert ratio.to_pandas().cell_tag.tolist() == ["defined"] * 3
    with pytest.raises(AnalysisError, match="original captured coverage fact"):
        if method in ("pearson", "spearman", "kendall"):
            revenue = (
                members.each(grid)
                .observe(
                    ms.ref.metric("sales.revenue"),
                    during=grid.window,
                    via=ms.ref.relationship("sales." + case.names.buyer),
                    by=(ms.ref.entity("sales.customer"),),
                )
                .group_by(grid)
                .rollup()
                .execute()
            )
            ratio.correlate(revenue.ratio(revenue).execute(), method=method).execute()
        else:
            model = (
                mv.naive()
                if method == "naive"
                else mv.drift()
                if method == "drift"
                else mv.seasonal_naive(periods=2)
            )
            ratio.forecast(horizon=mv.periods(1), model=model).execute()


@pytest.mark.parametrize(
    "end,unit,count",
    (
        ("2026-10-31T22:00:00-04:00", "hour", 1),
        ("2026-03-07T23:00:00-05:00", "hour", 1),
        ("2026-11-01T00:00:00-04:00", "hour", 4),
        ("2026-03-08T00:00:00-05:00", "hour", 4),
        ("2026-03-08T01:59:00-05:00", "minute", 4),
        ("2026-11-01T01:30:00-05:00", "minute", 4),
    ),
)
def test_future_grid_uses_actual_dst_boundaries(
    end: str, unit: Literal["hour", "minute"], count: int
) -> None:
    finish = datetime.fromisoformat(end)
    step = timedelta(hours=1) if unit == "hour" else timedelta(minutes=1)
    history = bind_grid(
        mv.time_scope(start=finish - 2 * step, end=finish),
        mv.grain(unit),
        report_timezone="America/New_York",
    )
    future = continuation(history, count)
    expected_start = history.cells[-1].end
    assert [(c.start, c.end, c.partial) for c in future.cells] == [
        (expected_start + i * step, expected_start + (i + 1) * step, False) for i in range(count)
    ]


def test_rank_witness_ties_corruption_and_subquadratic_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparisons = 0
    less = Fraction.__lt__
    equal = Fraction.__eq__

    def counted_less(left: Fraction, right: object) -> bool:
        nonlocal comparisons
        comparisons += 1
        assert isinstance(right, Fraction)
        return less(left, right)

    def counted_equal(left: Fraction, right: object) -> bool:
        nonlocal comparisons
        comparisons += 1
        return equal(left, right)

    original = tuple(Fraction(i // 2) for i in reversed(range(256)))
    ranks = tuple(RationalFact.capture(Fraction(4 * (i // 2) + 3, 2)) for i in reversed(range(256)))
    with monkeypatch.context() as patch:
        patch.setattr(Fraction, "__lt__", counted_less)
        patch.setattr(Fraction, "__eq__", counted_equal)
        _verify_ranks(original, ranks)
    assert comparisons < 256 * 20
    for corrupt in (ranks[:-1], (ranks[-1], *ranks[1:]), (RationalFact("3", "2"),) * 256):
        with pytest.raises(AnalysisError):
            _verify_ranks(original, corrupt)


@pytest.mark.runtime
@pytest.mark.parametrize("family", ("association", "forecast"))
@pytest.mark.parametrize("fixed", (False, True))
def test_statistical_rank_projection_and_tables(
    analysis_dsl_case_factory: DslCaseFactory,
    family: Literal["association", "forecast"],
    fixed: bool,
) -> None:
    case = analysis_dsl_case_factory("j4_ties")
    members = case.session.members(ms.ref.entity("sales.customer"))
    via = ms.ref.relationship("sales." + case.names.buyer)
    logical: mv.LogicalAssociationResult | mv.LogicalForecastResult
    numeric: (
        mv.MaterializedNumericRelation
        | mv.LogicalNumericRelation
        | mv.MaterializedCoefficientRelation
        | mv.LogicalCoefficientRelation
    )
    if family == "association":
        during = mv.time_scope(start="2026-08-01", end="2026-09-01")
        a = members.observe(
            ms.ref.metric("sales.revenue"),
            during=during,
            via=via,
            by=(ms.ref.entity("sales.customer"),),
        )
        b = members.observe(
            ms.ref.metric("sales.order_count"),
            during=during,
            via=via,
            by=(ms.ref.entity("sales.customer"),),
        )
        logical = a.correlate(b, method="spearman")
        numeric = logical.execute().coefficient if fixed else logical.coefficient
    else:
        grid = mv.time_grid(
            during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
        )
        history = members.each(grid).observe(
            ms.ref.metric("sales.order_count"),
            during=grid.window,
            via=via,
            by=(ms.ref.entity("sales.customer"),),
        )
        logical = history.forecast(horizon=mv.periods(2))
        numeric = logical.execute().prediction if fixed else logical.prediction
    if fixed:
        case.database_path.rename(case.database_path.with_suffix(".offline"))
    ranking = numeric.rank(order="descending", ties="dense")
    ranked = ranking.ranks.execute()
    expected = ranked.to_pandas().value.tolist()
    assert expected and min(expected) == 1
    findings = ranked.evidence_digest().finding_count
    assert findings > 0
    for combined in (False, True):
        table = (
            mv.table(value=ranking.values, rank=ranking.ranks)
            if combined
            else mv.table(rank=ranking.ranks)
        ).execute()
        assert table.to_pandas()["rank"].tolist() == expected
        assert table._dataset is not None
        assert table._dataset.evidence_digest().finding_count == findings
        ref = table.artifact_ref
        restored = case.session.artifact(ref.ref)
        assert isinstance(restored, mv.MaterializedTable)
        assert restored.to_pandas().equals(table.to_pandas())
        assert table._dataset is not None
        checked = table._dataset.verified()
        with pytest.raises(AnalysisError):
            from_arrow(
                checked.primary,
                checked.contract,
                parts=tuple(p for p in checked.parts if p.role != "table_fits"),
                method_state=checked.method_state,
            )
        bad = checked.primary.set_column(
            checked.primary.schema.get_field_index("column_0__value"),
            "column_0__value",
            pa.array(
                [999] * checked.primary.num_rows, type=checked.primary["column_0__value"].type
            ),
        )
        with pytest.raises(AnalysisError):
            from_arrow(
                bad, checked.contract, parts=checked.parts, method_state=checked.method_state
            )
