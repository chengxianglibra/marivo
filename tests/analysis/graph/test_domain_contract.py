"""Current statistic actions and non-scalar units."""

import marivo.analysis as mv
import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory


def test_registered_templates_and_non_scalar_units_keep_their_actions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    values = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"sales.{case.names.buyer}"),
        by=(mv.member(),),
    )
    assert "relation.correlate(*others)" in {action.call for action in values.contract().actions}
    statistic = values.aggregate(mv.count())
    assert "relation.compare(baseline)" in {action.call for action in statistic.contract().actions}
    assert "comparison_unavailable" not in dict(statistic.contract()._facts)
    assert "relation.correlate(*others)" not in {
        action.call for action in statistic.contract().actions
    }
    import pytest

    from marivo.analysis.errors import AnalysisError

    with pytest.raises(AnalysisError, match="identical"):
        statistic.compare(statistic)
