"""Current statistic actions and typed Journey count registrations."""

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
    assert isinstance(
        statistic.compare(statistic, design=mv.CohortContrast()), mv.LogicalDifferenceRelation
    )


def test_retained_journey_count_keys_stay_fixed_and_typed() -> None:
    from marivo.analysis.methods.journey_physical import consumers
    from marivo.analysis.methods.physical import DurationType, FixedShape, NoTime, ScalarType
    from marivo.analysis.methods.semantics import MethodKey

    for method in ("row.count", "row.count_defined"):
        declarations = consumers(MethodKey(method))
        assert {item.key.input_types for item in declarations} == {
            (ScalarType("string"),),
            (ScalarType("timestamp"),),
            (DurationType("us"),),
        }
        assert all(item.key.shape == FixedShape(NoTime()) for item in declarations)
        assert all(item.key.input_domains == ("journey",) for item in declarations)
        assert all(item.key.route == "artifact_python" for item in declarations)
        assert all(item.precision == "checked_int64" for item in declarations)
