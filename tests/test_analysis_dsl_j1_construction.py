"""Real declarations and source-free private construction for the J1 slice."""

from __future__ import annotations

from dataclasses import replace

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import _RunNodeBindings
from marivo.analysis.observation.dsl_j1 import J1Context
from marivo.analysis.operators.dsl_j1_contracts import J1_OBSERVE_SUM, J1_ROLLUP_SUM
from marivo.semantic.metric_graph_lowering import _dependency_fingerprint
from marivo.semantic.validator import assembly_validate
from tests.shared_fixtures import DslCaseFactory


def _context(factory: DslCaseFactory) -> tuple[J1Context, str]:
    case = factory("j1")
    state = case.catalog._state
    return J1Context(state.registry, state.sidecar, "j1-session", "j1-store"), case.names.domain


def test_j1_real_builder_declarations_construct_without_source_reads(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, domain = _context(analysis_dsl_case_factory)

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("J1 construction opened a business source")

    monkeypatch.setattr(duckdb, "connect", forbidden_connect)
    customer = ms.ref.entity(f"{domain}.customer")
    region = ms.ref.dimension(f"{domain}.customer.region")
    channel = ms.ref.dimension(f"{domain}.order.channel")
    revenue = ms.ref.metric(f"{domain}.revenue")
    buyer = ms.ref.relationship(f"{domain}.order_buyer")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    customers = context.members(customer)
    assert J1_OBSERVE_SUM.implementations == J1_ROLLUP_SUM.implementations == ()
    assert customers.domain.kind == "entity"
    assert customers.root.operator_id == "dsl.j1.members"

    total = customers.observe(revenue, during=august, via=buyer).rollup()
    assert total.domain.kind == "singleton"
    assert total.quantity.required_parts
    assert total.root.requirements == ("contribution_partition@v1", "complete_coverage@v1")

    direct = customers.group_by(region)
    read = customers.read(region)
    via_read = read.group_by()
    assert direct.dimension == via_read.dimension
    assert direct.domain.group_fields == via_read.domain.group_fields
    regional = direct.observe(revenue, during=august, via=buyer)
    assert regional.domain.kind == "group"

    east = read.where(read.value.eq("east")).members()
    east_channels = (
        east.observe(revenue, during=august, via=buyer, coordinates=(channel,))
        .group_by(channel)
        .rollup()
    )
    assert east_channels.domain.kind == "group"
    assert east_channels.root.requirements == ("contribution_partition@v1", "complete_coverage@v1")
    assert east_channels.quantity.metric_identity == "metric:" + revenue.path

    with pytest.raises(DatasetConstructionError, match="single-valued Dimension"):
        customers.group_by(channel)
    with pytest.raises(DatasetConstructionError, match="exact read node"):
        read.where(customers.read(region).value.eq("east"))


def test_j1_missing_authority_and_opaque_body_reject(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    context, domain = _context(analysis_dsl_case_factory)
    customer = ms.ref.entity(f"{domain}.customer")
    revenue = ms.ref.metric(f"{domain}.revenue")
    buyer = ms.ref.relationship(f"{domain}.order_buyer")
    august = mv.time_scope(start="2026-08-01", end="2026-09-01")
    customers = context.members(customer)
    with pytest.raises(DatasetConstructionError, match="builder-backed sum"):
        customers.observe(ms.ref.metric(f"{domain}.opaque_revenue"), during=august, via=buyer)
    with pytest.raises(DatasetConstructionError, match="directed Relationship"):
        customers.observe(revenue, during=august, via=ms.ref.relationship(f"{domain}.line_order"))

    original = context.registry.metrics[revenue.path]
    missing_registry = replace(
        context.registry,
        metrics={
            **context.registry.metrics,
            revenue.path: replace(original, event_time_dimension=None),
        },
    )
    missing_context = replace(context, registry=missing_registry)
    with pytest.raises(DatasetConstructionError, match="declared event time"):
        missing_context.members(customer).observe(revenue, during=august, via=buyer)


def test_j1_explicit_node_identity_is_distinct_from_definition(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    context, domain = _context(analysis_dsl_case_factory)
    customer = ms.ref.entity(f"{domain}.customer")
    first = context.members(customer)
    second = context.members(customer)
    assert first.root is not second.root
    assert first.root.definition_fingerprint == second.root.definition_fingerprint
    bindings: _RunNodeBindings[object] = _RunNodeBindings(context.session_id)
    implementation = object()
    assert bindings.bind(first.root, implementation) is implementation
    assert bindings.bind(first.root, implementation) is implementation
    assert bindings.bind(second.root, object()) is not implementation


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
