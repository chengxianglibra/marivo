"""Rewrite captured source declarations over a selected complete parent domain."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from ibis.common.deferred import Deferred

from marivo._definition.relations import (
    AttributeKeys,
    FrameKeyAxis,
    ObserveQuery,
    PopulationProgram,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    FieldRead,
    IdentityRead,
    IntervalCoordinate,
)
from marivo._grouping import Grouping
from marivo.analysis.binding.attribute import AttributeInputRequest, _axis_identity
from marivo.analysis.binding.attribute_rules import build_attribute_rule, require_rules_match
from marivo.analysis.binding.observe import bind_observation
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    OperationFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.core.frame_derivation import DerivedOutput
from marivo.analysis.core.frame_expression import (
    AttributeContributionValues,
    AttributeFrameValues,
    FrameAttributeTarget,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundQuantity, BoundSignature, reject
from marivo.analysis.dsl._frame_inputs import FrameKeyInput, merge_frame_key_inputs
from marivo.analysis.dsl.frame_sources import _capture
from marivo.analysis.dsl.population import Population, _axes, in_population, product

if TYPE_CHECKING:
    from marivo.analysis.dsl.frame import AnalysisFrame


def source_inputs(
    parent: AnalysisFrame,
    target: DerivedOutput,
    by: tuple[Deferred | Grouping, ...] | Population,
    cache: dict[tuple[int, tuple[str, ...], tuple[tuple[int, int], ...], str], AnalysisFrame]
    | None = None,
) -> AttributeInputRequest:
    """Bind from captured state/config; no Catalog or executable endpoint reconstruction."""
    fine_axes = by._definition.axes if isinstance(by, Population) else _axes(by)
    if not fine_axes or any(not isinstance(axis, AxisBinding) for axis in fine_axes):
        reject(
            "nonempty business classifications",
            repr(fine_axes),
            "Pass new semantic fields or one complete Population.",
            "frame.attribute",
        )
    endpoint_metrics = (
        (target.target.current,)
        if target.target.mode == "level"
        else (target.target.current, target.target.baseline)
    )
    endpoints = []
    projections = []
    positions = []
    rules = []
    projection_ids = []
    sides: tuple[Literal["left", "right"], ...] = ("left", "right")
    for side, metric in zip(sides[: len(endpoint_metrics)], endpoint_metrics, strict=True):
        assert metric is not None
        source = metric.binding
        original = source.graph
        observation = original.signature.observation
        assert observation is not None and isinstance(original.root, ObserveQuery)
        source_axes = original.signature.axes
        mappings = dict(metric.axis_positions)
        if set(mappings) != set(range(len(source_axes))) or set(mappings.values()) != set(
            range(len(parent._schema.axes))
        ):
            reject(
                "complete original parent coordinates",
                repr(metric.axis_positions),
                "Retain each original parent axis.",
                "frame.attribute",
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
            _axis_identity(axis) in {_axis_identity(other) for other in source_axes}
            for axis in fine_axes
        ):
            reject(
                "new distinct fine coordinates",
                repr(fine_axes),
                "Remove parent axes from by.",
                "frame.attribute",
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
        keys = AttributeKeys(
            parent._input_id,
            bindings,
            parent._schema.axes[mappings[temporal[0][0]]].token if temporal else None,
            temporal_axis,
            side,
            "outer",
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
        )
        fine_by = product(projection, by) if isinstance(by, Population) else None
        axes = (*source_axes, *fine_axes)
        reads = tuple(
            axis
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
            else FieldRead(axis.field, axis.entity, axis.path, axis.at)
            if axis.field is not None
            else IdentityRead(axis.entity, axis.path, axis.at)
            for axis in axes
        )
        from marivo.semantic._deferred_semantics import consume_predicate

        condition = consume_predicate(in_population(projection)).node
        if observation.condition is not None:
            import operator

            from ibis.common.deferred import BinaryOperator, Variable

            from marivo._definition.expressions import PredicateExpression

            original_condition = observation.condition
            condition = PredicateExpression(
                BinaryOperator(
                    operator.and_,
                    original_condition.recipe,
                    Variable(f"slot_{len(original_condition.inputs)}"),
                ),
                (*original_condition.inputs, *condition.inputs),
            )
        quantity = next(q for q in original.signature.quantities if q.name == ".".join(metric.path))
        assert isinstance(quantity, BoundQuantity)
        quantity = replace(quantity, name="target")
        bound = bind_observation(
            ((quantity.name, quantity.definition),),
            dict(original.signature.declared_metrics),
            by=reads,
            where=condition,
            timezone=observation.period.timezone if observation.period is not None else "UTC",
            state=source.state,
            memberships=observation.coordinates.memberships,
            population=fine_by._definition if fine_by is not None else None,
        )
        bound = replace(
            bound,
            coordinates=replace(
                bound.coordinates,
                parent_scope=projection._definition,
                complete_calendar=isinstance(by, Population),
            ),
        )
        graph = BoundGraph(
            ObserveQuery(
                ((quantity.name, quantity.definition),),
                axes,
                parent._local.owner,
                population=bound.coordinates.population,
                count_populations=tuple(domain for _, domain in bound.count_populations),
            ),
            BoundSignature(
                parent._local.owner,
                (quantity,),
                axes,
                original.signature.declared_metrics,
                observation=bound,
            ),
        )
        fine = _capture(
            parent._local,
            graph,
            source.state,
            source.connection,
            merge_frame_key_inputs(
                source.frame_key_inputs,
                projection._frame_inputs,
                by._frame_inputs if isinstance(by, Population) else (),
                owner=parent._local.owner,
            ),
        )
        cache_key = (id(source.graph), metric.path, metric.axis_positions, side)
        if cache is not None:
            fine = cache.setdefault(cache_key, fine)
        assert isinstance(fine._backing, ExpressionFrameBacking) and isinstance(
            fine._backing.root, SourceFrameBinding
        )
        parent_positions = tuple(
            next(i for i, receiver in metric.axis_positions if receiver == position)
            for position in range(len(parent._schema.axes))
        )
        rule = build_attribute_rule(
            quantity.definition,
            dict(graph.signature.declared_metrics),
            bound.contributions,
            occurrence=(0,),
            by=fine_axes,
            metric=quantity.name,
        )
        from marivo._definition.graph import computation_fingerprint

        projection_ids.append(computation_fingerprint(projection._definition))
        endpoints.append(fine)
        projections.append(projection)
        positions.append(parent_positions)
        rules.append(((quantity.name, rule),))
    if len(rules) == 1:
        endpoints.append(endpoints[0])
        projections.append(projections[0])
        positions.append(positions[0])
        rules.append(rules[0])
        projection_ids.append(projection_ids[0])
    require_rules_match(rules[0][0][1], rules[1][0][1], metric="target")
    operation = AttributeFrameValues(
        positions[0], positions[1], "outer", projection_ids[0], projection_ids[1]
    )
    return AttributeInputRequest(
        parent,
        (endpoints[0], endpoints[1]),
        (projections[0], projections[1]),
        operation,
        (rules[0], rules[1]),
    )


def _single(
    parent: AnalysisFrame,
    target: DerivedOutput,
    by: tuple[Deferred | Grouping, ...] | Population,
    cache: dict[tuple[int, tuple[str, ...], tuple[tuple[int, int], ...], str], AnalysisFrame],
) -> AnalysisFrame:
    """Private graph-backed contribution entry used by the Frame API."""
    from marivo.analysis.core.attribute import pending_contribution_schema
    from marivo.analysis.dsl.frame import AnalysisFrame

    request = source_inputs(parent, target, by, cache)
    operation = AttributeContributionValues(
        request.operation,
        request.rules,
        FrameAttributeTarget(
            target.path,
            target.target.mode,
            parent._schema.value_paths.index(target.path),
        ),
        target.target.mode == "level",
    )
    schema = pending_contribution_schema(
        request.endpoints[0]._schema, request.endpoints[1]._schema, parent._schema, operation
    )
    return AnalysisFrame._from_expression(
        parent._local,
        schema,
        OperationFrameBinding(
            operation,
            (
                parent,
                *(
                    request.endpoints[:1]
                    if operation.target is not None and operation.target.mode == "level"
                    else request.endpoints
                ),
            ),
        ),
    )


def attribute(
    parent: AnalysisFrame, *, by: tuple[Deferred | Grouping, ...] | Population
) -> AnalysisFrame:
    """Build the ordered closed targets retained by this exact receiver."""
    declaration = parent._derivation
    if (
        declaration is None
        or tuple(item.path for item in declaration.outputs) != parent._schema.value_paths
    ):
        reject(
            "direct captured Metric or comparison outputs",
            repr(parent._schema.value_paths),
            "Select direct source outputs, rebuild an observation, or investigate with population_from and observe.",
            "frame.attribute",
        )
    cache: dict[tuple[int, tuple[str, ...], tuple[tuple[int, int], ...], str], AnalysisFrame] = {}
    reports = tuple(_single(parent, target, by, cache) for target in declaration.outputs)
    if len(reports) == 1:
        return reports[0]
    from marivo.analysis.dsl.frame_attribute_merge import merge_attribute_reports

    return merge_attribute_reports(reports)
