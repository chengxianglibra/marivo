"""Selection continuations follow explicit complete coordinate authority."""

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from tests.shared_fixtures import DslCaseFactory


def test_selection_discloses_explicit_source_bridge_for_complete_identity(
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
    entity = session.catalog.entities.get("sales.customer")
    selected = values.where(values["value"] > 0)
    members = session.population_from(selected, on=((selected.axes[0], entity),))
    assert isinstance(members, mv.Population)
    assert session.frame(members).compute().to_pandas().iloc[:, 0].tolist() == [
        "A",
        "B",
        "C",
        "D",
    ]
    derived = values.select({"count": values["value"] + 1})
    selected_derived = derived.where(derived["count"] > 1)
    derived_members = session.population_from(
        selected_derived, on=((selected_derived.axes[0], entity),)
    )
    assert session.frame(derived_members).compute().to_pandas().iloc[:, 0].tolist() == [
        "A",
        "B",
        "C",
        "D",
    ]
    statistic = values.reduce({"count": mv.row_count()})
    scalar_domain = session.population_from(statistic, on=())
    with pytest.raises(AnalysisError) as refusal:
        scalar_domain.project(by=(mv.identity(entity),))
    assert refusal.value.expected and refusal.value.received and refusal.value.repair
