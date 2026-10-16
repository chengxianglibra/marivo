"""R1 source policies retain computation identity and owning field constraints."""

from dataclasses import replace

import pytest

import marivo.semantic as ms
from marivo._definition.expressions import SourceAggregate
from marivo._definition.graph import computation_fingerprint
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.expressions import Measure, TimeField, resolve
from marivo.semantic.validator import assembly_validate
from tests.shared_fixtures import DslCaseFactory


def test_policy_value_changes_metric_computation_identity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    metric = case.catalog._state.registry.declared_metrics["sales.revenue"]
    assert isinstance(metric.node, SourceAggregate)
    assert metric.node.nulls == "ignore"
    assert metric.node.time is not None
    assert metric.node.time.field.ref.path == "sales.order.ordered_at"
    changed = replace(metric.node, nulls="error")
    assert computation_fingerprint(metric.node) != computation_fingerprint(changed)


def test_time_kind_and_additivity_coordinates_are_checked_in_their_owner(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    amount = resolve(case.catalog, ms.ref.measure("sales.order.amount"))
    assert isinstance(amount, Measure)
    lines = resolve(case.catalog, ms.ref.measure("sales.order_line.line_amount"))
    time = resolve(case.catalog, ms.ref.time_dimension("sales.order.ordered_at"))
    assert isinstance(lines, Measure) and isinstance(time, TimeField)
    with pytest.raises(SemanticExpressionError) as caught:
        lines.sum(time=time)
    assert caught.value.expected and caught.value.received and caught.value.repair

    context = case.catalog._state
    measure = context.registry.measures["sales.order.amount"]
    bad_coordinate = replace(
        context.registry,
        measures={
            **context.registry.measures,
            "sales.order.amount": replace(
                measure,
                dsl_additivity=ms.additive(over=(ms.ref.dimension("sales.customer.region"),)),
            ),
        },
    )
    errors, _ = assembly_validate(bad_coordinate, sidecar=context.sidecar)
    assert any("non-native additivity coordinate" in error.message for error in errors)
