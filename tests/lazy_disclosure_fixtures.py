"""Example prerequisites built from real private owners and shared semantic fixtures."""

from __future__ import annotations

from marivo._temporal import builtin_grain, time_scope
from marivo.analysis import runtime_metric
from marivo.analysis._capabilities.dataset_registry import DatasetDisclosureRegistry
from marivo.analysis.domains.completeness import (
    BoundedCompletenessDeclarationV1,
    SourceOriginCompletenessDeclarationV1,
)
from marivo.analysis.event import sequence, step
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from marivo.semantic.event import participant_role
from marivo.semantic.state_model import ModelStateHandle
from tests.lazy_event_runtime_fixtures import journey
from tests.lazy_lifecycle_fixtures import END, MODEL, START, history, sources_without_io


def example_inputs(
    disclosure: DatasetDisclosureRegistry, sources: LazySources | None = None
) -> dict[str, object]:
    source = sources if sources is not None else sources_without_io()
    revenue = ref.metric("sales.revenue")
    count_metric = ref.metric("sales.order_count")
    region = ref.dimension("sales.customers.region")
    day = ref.time_dimension("sales.orders.order_time")
    metric = source.observe(revenue)
    dimensioned = metric.with_dimensions(region)
    time_metric = (
        source.observe(revenue, time_scope=time_scope(start="2026-02-01", end="2026-02-20"))
        .with_time_axis(day, grain=builtin_grain("day"))
        .aggregate()
    )
    start_role = participant_role(event=ref.event("sales.started"), name="buyer")
    start_step = step(participant=start_role, key="start")
    finish_step = step(
        participant=participant_role(event=ref.event("sales.finished"), name="buyer"), key="finish"
    )
    event_refs = (ref.event("sales.started"), ref.event("sales.finished"))
    source_origin = ref.datasource("warehouse")
    event_completeness = (
        BoundedCompletenessDeclarationV1(
            inputs=event_refs,
            complete_from=START,
            complete_through=END,
            rationale="Explicit example coverage",
        ),
    )
    lifecycle_completeness = (
        SourceOriginCompletenessDeclarationV1(
            inputs=event_refs,
            source_origin_ref=source_origin,
            complete_through=END,
            rationale="Explicit source-origin coverage",
        ),
    )
    events = journey(source)
    environment: dict[str, object] = {
        e.name: e.implementation for p in disclosure.providers for e in p.exports
    }
    environment.update(
        daily_grain=builtin_grain("day"),
        session=source,
        metric=metric,
        dimensioned=dimensioned,
        revenue=revenue,
        count_metric=count_metric,
        region=region,
        day=day,
        population=source.population(ref.entity("sales.customers")),
        customer=ref.entity("sales.customers"),
        multi_metric=source.observe((revenue, count_metric)).with_dimensions(region),
        time_metric=time_metric,
        time_delta=time_metric.compare(time_metric),
        delta=dimensioned.aggregate().compare(dimensioned.aggregate()),
        events=events,
        funnel_delta=events.funnel().compare(events.funnel()),
        lifecycle=history(source),
        pattern=sequence(start_step, finish_step),
        start_role=start_role,
        start_step=start_step,
        finish_step=finish_step,
        window=time_scope(start=START.isoformat(), end=END.isoformat()),
        start=START,
        end=END,
        event_refs=event_refs,
        source_origin=source_origin,
        event_completeness=event_completeness,
        lifecycle_completeness=lifecycle_completeness,
        model=MODEL,
        done_state=ModelStateHandle(MODEL, "done"),
        runtime_metric=runtime_metric,
        amount_measure=ref.measure("sales.orders.amount"),
        weight_measure=ref.measure("sales.orders.weight"),
    )
    return environment
