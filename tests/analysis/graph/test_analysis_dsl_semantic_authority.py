"""Semantic authority checks retained across the public Store 7 cutover."""

from dataclasses import replace

import marivo.semantic as ms
from marivo.semantic.metric_graph_lowering import _dependency_fingerprint
from marivo.semantic.validator import assembly_validate
from tests.shared_fixtures import DslCaseFactory


def _context(factory: DslCaseFactory):
    case = factory("j1")
    return case.catalog._state, case.names.domain


def test_policy_value_changes_metric_dependency_identity(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    context, domain = _context(analysis_dsl_case_factory)
    metric_id = f"{domain}.opaque_revenue"
    original = context.registry.metrics[metric_id]
    assert original.null_policy == ms.nulls.ignore()
    assert original.empty_policy == ms.empty.null()
    assert original.event_time_dimension == f"{domain}.order.ordered_at"
    changed = replace(
        context.registry,
        metrics={
            **context.registry.metrics,
            metric_id: replace(original, empty_policy=ms.empty.zero()),
        },
    )
    assert _dependency_fingerprint(
        context.registry, metric_id=metric_id, sidecar=context.sidecar
    ) != _dependency_fingerprint(changed, metric_id=metric_id, sidecar=context.sidecar)


def test_cross_owner_time_and_additivity_are_rejected_at_assembly(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    context, domain = _context(analysis_dsl_case_factory)
    metric_id = f"{domain}.opaque_revenue"
    original = context.registry.metrics[metric_id]
    bad_time = replace(
        context.registry,
        metrics={
            **context.registry.metrics,
            metric_id: replace(original, event_time_dimension=f"{domain}.customer.region"),
        },
    )
    errors, _ = assembly_validate(bad_time, sidecar=context.sidecar)
    assert any("invalid event time" in error.message for error in errors)

    amount_id = f"{domain}.order.amount"
    measure = context.registry.measures[amount_id]
    bad_coordinate = replace(
        context.registry,
        measures={
            **context.registry.measures,
            amount_id: replace(
                measure,
                dsl_additivity=ms.additive(over=(ms.ref.dimension(f"{domain}.customer.region"),)),
            ),
        },
    )
    errors, _ = assembly_validate(bad_coordinate, sidecar=context.sidecar)
    assert any("non-native additivity coordinate" in error.message for error in errors)
