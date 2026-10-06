"""Selection guidance requires retained Subject authority."""

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from tests.shared_fixtures import DslCaseFactory


def test_selection_discloses_members_only_with_retained_subject_map(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    members = case.session.members(ms.ref.entity("sales.customer"))
    values = members.observe(
        ms.ref.metric("sales.order_count"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"sales.{case.names.buyer}"),
    )
    assert isinstance(values, mv.LogicalNumericRelation)
    selected = values.where(values.value.gt(0))
    assert "relation.members()" in {action.call for action in selected.contract().actions}
    assert isinstance(selected.members(), mv.LogicalAnalysisDomain)
    shares = values.share_of(values.rollup())
    selected_share = shares.where(shares.value.gt(0.5))
    assert "relation.members()" not in {action.call for action in selected_share.contract().actions}
    assert "relation.summarize(method)" in {
        action.call for action in selected_share.contract().actions
    }
    with pytest.raises(DatasetConstructionError) as refusal:
        selected_share.members()
    assert refusal.value.received == "current result has no verified Subject map"
    assert refusal.value.expected and refusal.value.repair
