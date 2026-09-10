"""Small declared Event fixtures shared by contract and native Runtime tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import ibis
import ibis.expr.types as ir

from marivo.analysis.observation.contracts import ObservationActionPort
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.ir import AiContextIR, TableColumnBindingIR, TableSourceIR
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar, ExpressionBody
from marivo.semantic.ir import (
    DimensionIR,
    DimensionKind,
    EntityIR,
    EventIR,
    EventParticipantIR,
    JoinKey,
    RelationshipIR,
    SourceLocation,
    TimestampParse,
)
from marivo.semantic.validator import Registry
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_observation_fixtures import NoIoActionPort


def _all_rows(table: ir.Table) -> ir.BooleanValue:
    return ibis.literal(True)


def make_event_registry(database: Path) -> tuple[Registry, CompiledExpressionSidecar]:
    """Add two independent Event sources targeting the existing customer Entity."""
    original, old_sidecar = make_execution_registry(database)
    registry = replace(
        original,
        entities=dict(original.entities),
        dimensions=dict(original.dimensions),
        relationships=dict(original.relationships),
        events={},
    )
    bodies = dict(old_sidecar.bodies)
    owners = dict(old_sidecar.field_owners)
    references = set(old_sidecar.catalog_refs)
    location = SourceLocation("lazy_event_fixture.py", 1)
    for name in ("started", "finished"):
        path = f"sales.{name}_rows"
        entity_ref = ref.entity(path)
        registry.entities[path] = EntityIR(
            path,
            "sales",
            f"{name}_rows",
            "warehouse",
            TableSourceIR(
                f"{name}_rows",
                columns=tuple(
                    (column, TableColumnBindingIR(column, logical))
                    for column, logical in (
                        ("occurrence_id", "int64"),
                        ("customer_id", "int64"),
                        ("occurred_at", "timestamp"),
                    )
                ),
            ),
            ("occurrence_id",),
            AiContextIR(),
            f"{name}_rows",
            location,
        )
        references.add(entity_ref)
        for column, temporal in (("occurrence_id", False), ("occurred_at", True)):
            dimension_path = f"{path}.{column}"
            dimension_ref = (
                ref.time_dimension(dimension_path) if temporal else ref.dimension(dimension_path)
            )
            body = ExpressionBody.for_column(column)
            registry.dimensions[dimension_path] = DimensionIR(
                dimension_path,
                "sales",
                path,
                column,
                AiContextIR(),
                temporal,
                DimensionKind.TIME if temporal else DimensionKind.CATEGORICAL,
                column,
                location,
                granularity="second" if temporal else None,
                parse=TimestampParse(timezone="UTC") if temporal else None,
                is_default=temporal,
                body_ast_hash=body.body_ast_hash,
                source_column=column,
            )
            bodies[dimension_ref] = body
            owners[dimension_ref] = entity_ref
            references.add(dimension_ref)
        relationship = f"sales.{name}_customer"
        registry.relationships[relationship] = RelationshipIR(
            relationship,
            "sales",
            f"{name}_customer",
            path,
            "sales.customers",
            (JoinKey("customer_id", "id"),),
            AiContextIR(),
            location,
        )
        references.add(ref.relationship(relationship))
        event_ref = ref.event(f"sales.{name}")
        body = ExpressionBody(
            callable=_all_rows, body_ast_hash="event_all_rows@v1", parameter_count=1, bindings=()
        )
        registry.events[event_ref.path] = EventIR(
            event_ref.path,
            "sales",
            name,
            path,
            (f"{path}.occurrence_id",),
            f"{path}.occurred_at",
            (EventParticipantIR("buyer", (relationship,), "one"),),
            "all_rows",
            AiContextIR(),
            name,
            location,
            body.body_ast_hash,
        )
        bodies[event_ref] = body
        references.add(event_ref)
    registry.freeze()
    return registry, CompiledExpressionSidecar(
        bodies=bodies, field_owners=owners, catalog_refs=frozenset(references)
    )


def make_event_sources(
    *,
    database: Path = Path("/nonexistent/event-contract.duckdb"),
    session_id: str = "session-event",
    store_id: str = "store-event",
    action_port: ObservationActionPort | None = None,
) -> LazySources:
    registry, sidecar = make_event_registry(database)
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort() if action_port is None else action_port,
        session_id=session_id,
        store_id=store_id,
    )
