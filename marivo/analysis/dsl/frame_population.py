"""Private metadata-only explicit binding of Frame coordinates to Population."""

from __future__ import annotations

from ibis.common.deferred import Deferred

from marivo._definition.relations import FrameKeyAxis, FrameKeys, PopulationProgram
from marivo._definition.types import AxisBinding, FieldRead
from marivo._temporal import anchor_from_datetime
from marivo.analysis.binding.coordinates import bind_read, native_read
from marivo.analysis.core.frame_key_inputs import FrameKeyInput, merge_frame_key_inputs
from marivo.analysis.core.frame_structure import axis_role, column_kind
from marivo.analysis.core.model import reject
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.dsl.population import Population
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.observation.relationship_binding import RelationshipResolver
from marivo.refs import SemanticKind
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._deferred_semantics import consume_field
from marivo.semantic.expressions import Entity, _Resolver


def _field_axis(value: Deferred, state: CompiledSemanticState, resolver: _Resolver) -> AxisBinding:
    field = consume_field(value)
    read = field.field_read or FieldRead(field.reference, field.root)
    root = read.entity
    if read.path:
        first = state.registry.relationships.get(read.path[0].ref.path)
        if first is None:
            reject(
                "a directed field path in the receiving Catalog",
                read.path[0].ref.key,
                "Resolve the explicit relationship path from this Session's Catalog.",
                "frame.population",
            )
        root = resolver.entity_ref(first.from_entity)
    current = bind_read(
        read, root, state.registry, resolver, RelationshipResolver.build(state.registry)
    )
    return AxisBinding(current.entity, current.field, current.path, "field", current.at)


def population_from(
    local: LocalFrameServices,
    state: CompiledSemanticState | None,
    frame: AnalysisFrame,
    *,
    on: tuple[tuple[FrameAxis, Deferred | Entity], ...],
) -> Population:
    """Capture explicit joint coordinate bindings without preparing or reading data."""
    if not isinstance(frame, AnalysisFrame) or frame._local.owner != local.owner:
        reject(
            "an AnalysisFrame from the receiving Store and Session",
            repr(frame),
            "Build the Population in the selected Frame's owning Session.",
            "frame.population",
        )
    if type(on) is not tuple or any(type(pair) is not tuple or len(pair) != 2 for pair in on):
        reject(
            "an explicit tuple of (FrameAxis, resolved Field or Entity) pairs",
            repr(on),
            "Pass on=((frame.axes[0], field),); use on=() for the empty joint key.",
            "frame.population",
        )
    resolver = _Resolver(state) if state is not None else None
    bindings: list[FrameKeyAxis] = []
    selected: set[int] = set()
    targets: set[AxisBinding] = set()
    inputs = merge_frame_key_inputs((FrameKeyInput(frame._input_id, frame),), owner=local.owner)
    for axis, target in on:
        assert state is not None and resolver is not None
        index = frame._axis_index(axis)
        inputs = merge_frame_key_inputs(
            inputs, (FrameKeyInput(axis._input_id, axis._frame),), owner=local.owner
        )
        if index in selected:
            reject(
                "each selected Frame axis once",
                axis.token,
                "Remove the repeated input axis from on.",
                "frame.population",
            )
        selected.add(index)
        captured = frame._schema.axes[index]
        role = axis_role(captured)
        if role == "identity" and isinstance(target, Entity):
            if (
                target.ref.ref.kind is not SemanticKind.ENTITY
                or target.ref.ref.path not in state.registry.entities
            ):
                reject(
                    "an Entity declared in the receiving Catalog",
                    target.ref.ref.key,
                    "Resolve an Entity from this Session's Catalog.",
                    "frame.population",
                )
            entity = resolver.entity(target.ref.ref.path)
            if len(captured.columns) != len(entity.primary_key):
                reject(
                    "all components of the target Entity primary key",
                    repr(tuple(column.name for column in captured.columns)),
                    "Map a complete identity axis with the target primary-key width.",
                    "frame.population",
                )
            at = anchor_from_datetime(captured.at) if captured.at is not None else None
            if at is not None and state.registry.entities[entity.ref.ref.path].versioning is None:
                reject(
                    "a versioned target Entity for the captured identity anchor",
                    entity.ref.ref.key,
                    "Select a compatible versioned Entity or an unanchored identity axis.",
                    "frame.population",
                )
            binding = AxisBinding(entity.ref, None, (), "identity", at)
        elif role == "field" and isinstance(target, Deferred):
            binding = _field_axis(target, state, resolver)
            assert binding.field is not None
            native = native_read(binding.field, state.registry, resolver)
            unit = (
                resolver.measure(native.field.ref.path).unit
                if native.field.ref.kind is SemanticKind.MEASURE
                else None
            )
            if len(captured.columns) != 1 or (
                captured.columns[0].unit is not None and captured.columns[0].unit != unit
            ):
                reject(
                    "one scalar field key with a compatible declared unit",
                    repr(tuple(column.unit for column in captured.columns)),
                    "Map the complete field axis to a Field with the same unit.",
                    "frame.population",
                )
            if native.field.ref.kind is SemanticKind.TIME_DIMENSION and column_kind(
                captured.columns[0]
            ) not in ("date", "timestamp", "unresolved"):
                reject(
                    "a date or timestamp key for a TimeField target",
                    column_kind(captured.columns[0]),
                    "Select a time-valued ordinary field axis.",
                    "frame.population",
                )
        else:
            reject(
                "an ordinary field axis mapped to a resolved Field, or a complete identity axis mapped to an Entity",
                f"{role} axis and {type(target).__name__}",
                "Use complete ordinary coordinate handles and explicitly resolved Catalog targets.",
                "frame.population",
            )
        if binding in targets:
            reject(
                "each target coordinate once",
                repr(binding),
                "Remove the repeated target coordinate from on.",
                "frame.population",
            )
        targets.add(binding)
        bindings.append(FrameKeyAxis(axis.token, axis.key_names, binding))
    definition = PopulationProgram(
        FrameKeys(frame._input_id, tuple(bindings)),
        local.owner,
        tuple(binding.target for binding in bindings),
    )
    return Population(definition, inputs)
