"""Selection continuations follow explicit complete coordinate authority."""

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory


def test_selection_discloses_population_for_retained_complete_identity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    members = session.members(ms.ref.entity("sales.customer"))
    values = session.observe(
        {"value": session.catalog.metrics.get("sales.order_count")},
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
        by=members,
    )
    selected = values.where(values["value"] > 0)
    assert "query.population()" in {action.call for action in selected.contract().actions}
    assert isinstance(selected.population(), mv.Population)
    assert session.query(selected.population()).execute().to_pandas().iloc[:, 0].tolist() == [
        "A",
        "B",
        "C",
        "D",
    ]
    shares = values.share("value")
    selected_share = shares.where(shares["value.share"] > 0.2)
    # Derived values retain their complete coordinates; original authority is separate.
    assert isinstance(selected_share.population(), mv.Population)
    assert session.query(selected_share.population()).execute().to_pandas().iloc[:, 0].tolist() == [
        "A",
        "B",
        "C",
        "D",
    ]
    statistic = values.reduce({"count": mv.row_count()})
    with pytest.raises(AnalysisError) as refusal:
        statistic.population().project(by=(mv.member(),))
    assert refusal.value.expected and refusal.value.received and refusal.value.repair
