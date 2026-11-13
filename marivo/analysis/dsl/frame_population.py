"""Private metadata-only explicit binding of Frame coordinates to Population."""

from __future__ import annotations

from ibis.common.deferred import Deferred

from marivo._definition.correspondence import coordinate_matches
from marivo._definition.population_builders import axis as field_axis
from marivo._definition.relations import FrameKeyAxis, FrameKeys, PopulationProgram
from marivo._definition.types import AxisBinding, InputContext
from marivo.analysis.binding.coordinates import bind_read, native_read
from marivo.analysis.core.frame_structure import axis_role, column_kind
from marivo.analysis.core.lineage import ColumnLineage
from marivo.analysis.core.model import reject
from marivo.analysis.dsl._frame_inputs import FrameKeyInput, merge_frame_key_inputs
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.dsl.population import Population, _read
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.observation.relationship_binding import RelationshipResolver
from marivo.refs import SemanticKind
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._consumption import bind_field_in_state, validate_context
from marivo.semantic._expression_values import FieldExpression, RowExpression
from marivo.semantic.catalog import Entity as EntityObject
from marivo.semantic.expressions import _Entity as Entity
from marivo.semantic.expressions import _Resolver, entity_exact


def _field_axis(value: Deferred, state: CompiledSemanticState, resolver: _Resolver) -> AxisBinding:
    read = _read(value)
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
    return field_axis(current)


def _is_captured_direct_field(binding: AxisBinding, axis_name: str, lineage: ColumnLineage) -> bool:
    # Lineage provides field and relationship identities, without ordered node anchors.
    assert binding.field is not None
    target = binding.field.ref
    return (
        not binding.path
        and axis_name == target.path
        and any(
            origin.kind == target.kind.value and origin.path == target.path
            for origin in lineage.value_origins
        )
        and all(origin.kind != "relationship" for origin in lineage.value_origins)
    )


def population_from(
    local: LocalFrameServices,
    state: CompiledSemanticState | None,
    frame: AnalysisFrame,
    *,
    on: tuple[tuple[FrameAxis, Deferred | Entity | EntityObject | FieldExpression], ...],
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
    targets: list[AxisBinding] = []
    identity_keys: list[tuple[str, tuple[str, ...]]] = []
    inputs = merge_frame_key_inputs((FrameKeyInput(frame._input_id, frame),), owner=local.owner)
    for axis, target in on:
        assert state is not None and resolver is not None
        if isinstance(target, EntityObject):
            validate_context(
                target._context, project_root=local.store.project_root, owner=local.owner
            )
            if target._ref.path not in state.registry.entities:
                reject(
                    "an Entity declared in the receiving Catalog",
                    target.id,
                    "Obtain an Entity from the receiving Session's loaded scope.",
                    "frame.population",
                )
            target = resolver.entity(target._ref.path)
        elif isinstance(target, RowExpression):
            target = bind_field_in_state(
                target, state, project_root=local.store.project_root, owner=local.owner
            )
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
                entity_exact(target).ref.kind is not SemanticKind.ENTITY
                or entity_exact(target).ref.path not in state.registry.entities
            ):
                reject(
                    "an Entity declared in the receiving Catalog",
                    entity_exact(target).ref.key,
                    "Resolve an Entity from this Session's Catalog.",
                    "frame.population",
                )
            entity = resolver.entity(entity_exact(target).ref.path)
            if len(captured.columns) != len(entity.primary_key):
                reject(
                    "all components of the target Entity primary key",
                    repr(tuple(column.name for column in captured.columns)),
                    "Map a complete identity axis with the target primary-key width.",
                    "frame.population",
                )
            exact = entity_exact(entity)
            binding = AxisBinding(
                exact,
                None,
                (),
                "identity",
                versioned=state.registry.entities[exact.ref.path].versioning is not None,
            )
            identity_keys.append((exact.ref.key, entity.primary_key))
        elif role == "field" and isinstance(target, Deferred):
            binding = _field_axis(target, state, resolver)
            assert binding.field is not None
            if (
                binding.versioned
                and binding.at is None
                and (
                    captured.at is not None
                    or not _is_captured_direct_field(
                        binding, captured.name, frame.lineage.axes[axis.token]
                    )
                )
            ):
                reject(
                    "a matching captured unanchored field or an explicit version for the target",
                    f"{captured.name} at {captured.at!r} -> {binding.field.ref.key}",
                    "Bind the target with field.at(session.instant(...)) to declare its version.",
                    "frame.population",
                    canonical_id="session.population_from",
                )
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
        if any(coordinate_matches(binding, previous) for previous in targets):
            reject(
                "each target coordinate once",
                repr(binding),
                "Remove the repeated target coordinate from on.",
                "frame.population",
            )
        targets.append(binding)
        bindings.append(FrameKeyAxis(axis.token, axis.key_names, binding))
    definition = PopulationProgram(
        FrameKeys(frame._input_id, tuple(bindings)),
        local.owner,
        tuple(binding.target for binding in bindings),
    )
    return Population(
        definition,
        inputs,
        _context=InputContext(local.store.project_root, local.owner),
        _identity_keys=tuple(identity_keys),
    )
