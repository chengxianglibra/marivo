"""Bind attribution endpoints to their captured complete parent axes."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Literal

from ibis.common.deferred import BinaryOperator, Variable

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import PredicateExpression
from marivo._definition.relations import (
    AttributeKeys,
    FrameKeyAxis,
    LiteralPopulation,
    ObserveQuery,
    PopulationProgram,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    InputContext,
    IntervalCoordinate,
    LiteralValue,
)
from marivo.analysis.binding.windows import require_finite_attribution_scope
from marivo.analysis.core.frame_data import (
    DerivedOutput,
    FrameNode,
    SourceMetric,
)
from marivo.analysis.core.frame_operations import (
    AlignFrameValues,
    NamedFrameComparison,
)
from marivo.analysis.dsl.frame_attribute_sources import (
    endpoint_source_side,
)
from marivo.analysis.dsl.population import Population, in_population, product
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_contracts import FrameKeyInput
from marivo.semantic.spi import (
    consume_predicate,
)


def _axis_identity(axis: ComparisonAxis) -> tuple[object, ...]:
    if isinstance(axis, AxisBinding):
        return (
            axis.entity.ref.key,
            axis.field.ref.key if axis.field is not None else None,
            tuple(r.ref.key for r in axis.path),
            axis.role,
            axis.at,
        )
    if isinstance(axis, IntervalCoordinate):
        return ("interval", axis.start, axis.end, axis.timezone)
    return ("calendar", axis.identity, axis.grain, axis.timezone)


def _scope_condition(
    projection: Population,
    condition: PredicateExpression | None,
    ordinary: tuple[tuple[int, AxisBinding], ...],
    fixed_keys: dict[int, tuple[LiteralValue, ...]],
    original_axes: tuple[ComparisonAxis, ...],
) -> PredicateExpression | None:
    if ordinary or fixed_keys:
        ordinary_scope = projection.project(by=projection.axes[: len(ordinary)])
        if fixed_keys:
            fixed_axes = tuple(original_axes[i] for i in fixed_keys)
            assert all(isinstance(axis, AxisBinding) for axis in fixed_axes)
            literal_axes = tuple(axis for axis in fixed_axes if isinstance(axis, AxisBinding))
            fixed_domain = Population(
                LiteralPopulation(
                    literal_axes,
                    (
                        tuple(
                            key if axis.role == "identity" else key[0]
                            for axis, key in zip(literal_axes, fixed_keys.values(), strict=True)
                        ),
                    ),
                    "coordinates-v1",
                )
            )
            ordinary_scope = product(ordinary_scope, fixed_domain)
        selected_condition = consume_predicate(in_population(ordinary_scope)).node
        condition = (
            selected_condition
            if condition is None
            else PredicateExpression(
                BinaryOperator(
                    operator.and_, condition.recipe, Variable(f"slot_{len(condition.inputs)}")
                ),
                (*condition.inputs, *selected_condition.inputs),
            )
        )

    return condition


@dataclass(frozen=True, slots=True)
class AttributeParentProjection:
    population: Population
    source_axes: tuple[ComparisonAxis, ...]
    positions: tuple[int, ...]
    condition: PredicateExpression | None
    side: Literal["left", "right"]


def project_attribute_parent(
    parent: FrameNode,
    target: DerivedOutput,
    metric: SourceMetric,
    side: Literal["left", "right"],
    fine_axes: tuple[ComparisonAxis, ...],
) -> AttributeParentProjection:
    source = metric.binding
    original = source.graph
    require_finite_attribution_scope(original)
    observation = original.signature.observation
    assert observation is not None and isinstance(original.root, ObserveQuery)
    original_axes = original.signature.axes
    fixed_keys = dict(metric.fixed_axes)
    retained = tuple(i for i in range(len(original_axes)) if i not in fixed_keys)
    source_axes = tuple(original_axes[i] for i in retained)
    mappings = {retained.index(i): position for i, position in metric.axis_positions}
    if set(mappings) != set(range(len(source_axes))) or set(mappings.values()) != set(
        range(len(parent._schema.axes))
    ):
        reject(
            "complete original parent coordinates",
            repr(metric.axis_positions),
            "Retain each original parent axis.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    ordinary = tuple(
        (i, axis) for i, axis in enumerate(source_axes) if isinstance(axis, AxisBinding)
    )
    temporal = tuple(
        (i, axis)
        for i, axis in enumerate(source_axes)
        if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
    )
    if any(
        _axis_identity(axis) in {_axis_identity(other) for other in original_axes}
        for axis in fine_axes
    ):
        reject(
            "new distinct fine coordinates",
            repr(fine_axes),
            "Remove parent axes from by.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    bindings = tuple(
        FrameKeyAxis(
            parent._schema.axes[mappings[i]].token,
            tuple(c.name for c in parent._schema.axes[mappings[i]].columns),
            axis,
        )
        for i, axis in ordinary
    )
    temporal_axis = temporal[0][1] if temporal else None
    projection_side = endpoint_source_side(target.target, side)
    comparison_alias = (
        target.target.pairing.alias
        if isinstance(target.target.pairing, NamedFrameComparison)
        and (side == "right" or target.target.mode == "level")
        else None
    )
    presence = None
    if metric.fixed_axes:
        for fact in parent._schema.comparisons:
            for binding in fact.values:
                if binding.value_path == target.path:
                    endpoint = (
                        fact.baseline_presence
                        if (
                            binding.role == "baseline"
                            or (binding.role in ("absolute", "relative") and side == "right")
                        )
                        else fact.current_presence
                    )
                    presence = endpoint.path if endpoint is not None else None
        if isinstance(target.target.pairing, AlignFrameValues):
            presence = (f"{projection_side}_present",)
    keys = AttributeKeys(
        parent._input_id,
        bindings,
        parent._schema.axes[mappings[temporal[0][0]]].token if temporal else None,
        temporal_axis,
        projection_side,
        "outer",
        comparison_alias,
        tuple(
            (axis, key)
            for i, key in metric.fixed_axes
            for axis in (original_axes[i],)
            if isinstance(axis, AxisBinding)
        ),
        presence,
    )
    projection = Population(
        PopulationProgram(
            keys,
            parent._local.owner,
            (
                *tuple(axis for _, axis in ordinary),
                *((temporal_axis,) if temporal_axis else ()),
            ),
        ),
        (FrameKeyInput(parent._input_id, parent),),
        _context=InputContext(source.connection.project_root, parent._local.owner),
    )
    # Fixed coordinates restrict their original complete membership domain
    # without becoming new output axes.
    return AttributeParentProjection(
        projection,
        source_axes,
        tuple(
            next(i for i, receiver in mappings.items() if receiver == position)
            for position in range(len(parent._schema.axes))
        ),
        _scope_condition(projection, observation.condition, ordinary, fixed_keys, original_axes),
        projection_side,
    )
