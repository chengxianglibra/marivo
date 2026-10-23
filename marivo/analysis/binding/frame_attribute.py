"""Rewrite captured source declarations over a selected complete parent domain."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from ibis.common.deferred import Deferred

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.relations import (
    AttributeKeys,
    FrameKeyAxis,
    MembershipBinding,
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
from marivo.analysis.binding.attribute_rules import build_attribute_rule, require_rules_match
from marivo.analysis.binding.frame_attribute_inputs import AttributeInputRequest
from marivo.analysis.binding.frame_attribute_sources import (
    SourceGroupKey,
    SourceMetricGroup,
    collect_source_groups,
    endpoint_source_side,
    source_group_key,
)
from marivo.analysis.binding.observe import bind_observation
from marivo.analysis.core.data_schema import CalendarDataAxis, IntervalDataAxis, PairedDataAxis
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
    NamedFrameComparison,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundQuantity, BoundSignature, reject
from marivo.analysis.dsl._frame_inputs import FrameKeyInput, merge_frame_key_inputs
from marivo.analysis.dsl.frame_sources import _capture
from marivo.analysis.dsl.population import Population, _axes, product

if TYPE_CHECKING:
    from marivo.analysis.dsl.frame import AnalysisFrame


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


def source_inputs(
    parent: AnalysisFrame,
    target: DerivedOutput,
    by: tuple[Deferred | Grouping, ...] | Population,
    cache: dict[SourceGroupKey, AnalysisFrame] | None = None,
    groups: dict[SourceGroupKey, SourceMetricGroup] | None = None,
) -> AttributeInputRequest:
    """Bind from captured state/config; no Catalog or executable endpoint reconstruction."""
    fine_memberships = (
        tuple(
            item._binding
            for item in by
            if isinstance(item, Grouping) and isinstance(item._binding, MembershipBinding)
        )
        if isinstance(by, tuple)
        else ()
    )
    fine_axes = (
        by._definition.axes
        if isinstance(by, Population)
        else tuple(
            axis
            for item in by
            for axis in (
                tuple(
                    AxisBinding(read.entity, read.field, read.path, "field", read.at)
                    for read in item._binding.fields
                )
                if isinstance(item, Grouping) and isinstance(item._binding, MembershipBinding)
                else _axes((item,))
            )
        )
    )
    if not fine_axes or any(not isinstance(axis, AxisBinding) for axis in fine_axes):
        reject(
            "nonempty business classifications",
            repr(fine_axes),
            "Pass new semantic fields or one complete Population.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
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
    all_rules = []
    source_indices = []
    source_sides = []
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
            _axis_identity(axis) in {_axis_identity(other) for other in source_axes}
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
        source_sides.append(0 if projection_side == "left" else 1)
        comparison_alias = (
            target.target.pairing.alias
            if isinstance(target.target.pairing, NamedFrameComparison)
            and (side == "right" or target.target.mode == "level")
            else None
        )
        keys = AttributeKeys(
            parent._input_id,
            bindings,
            parent._schema.axes[mappings[temporal[0][0]]].token if temporal else None,
            temporal_axis,
            projection_side,
            "outer",
            comparison_alias,
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
        assert fine_by is None or isinstance(fine_by, Population)
        axes = (*source_axes, *fine_axes)
        reads = tuple(
            axis
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
            else FieldRead(axis.field, axis.entity, axis.path, axis.at)
            if axis.field is not None
            else IdentityRead(axis.entity, axis.path, axis.at)
            for axis in axes
        )
        # Qualified source bundling uses the complete joint parent_scope, including
        # temporal coordinates. Source predicates retain only the original condition.
        condition = observation.condition
        if ordinary:
            import operator

            from ibis.common.deferred import BinaryOperator, Variable

            from marivo._definition.expressions import PredicateExpression
            from marivo.analysis.dsl.population import in_population
            from marivo.semantic._deferred_semantics import consume_predicate

            ordinary_scope = projection.project(by=projection.axes[: len(ordinary)])
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
        from marivo._definition.windows import ToDate

        snapshots = {
            axis.snapshot.snapshot_digest: axis.snapshot
            for contribution in observation.contributions
            for _, axis in contribution.calendars
            if isinstance(axis, CalendarCoordinate) and axis.snapshot is not None
        }
        for contribution in observation.contributions:
            window = contribution.window_rule
            if isinstance(window, ToDate) and window.grain._snapshot is not None:
                snapshot = window.grain._snapshot
                snapshots[snapshot.snapshot_digest] = snapshot
        group = None if groups is None else groups[source_group_key(metric, side, target.target)]
        quantities: tuple[BoundQuantity, ...]
        if group is None:
            quantity = next(
                q for q in original.signature.quantities if q.name == ".".join(metric.path)
            )
            assert isinstance(quantity, BoundQuantity)
            quantities = (replace(quantity, name="target"),)
            source_index = 0
        else:
            quantities = group.quantities
            source_index = group.index(metric.path)
        source_indices.append(source_index)
        bound = bind_observation(
            tuple((quantity.name, quantity.definition) for quantity in quantities),
            dict(original.signature.declared_metrics),
            by=reads,
            where=condition,
            timezone=observation.period.timezone if observation.period is not None else "UTC",
            state=source.state,
            memberships=(*observation.coordinates.memberships, *fine_memberships),
            calendar_snapshots=tuple(snapshots.values()),
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
                tuple((quantity.name, quantity.definition) for quantity in quantities),
                axes,
                parent._local.owner,
                population=bound.coordinates.population,
                count_populations=tuple(domain for _, domain in bound.count_populations),
            ),
            BoundSignature(
                parent._local.owner,
                quantities,
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
        cache_key = source_group_key(metric, side, target.target)
        if cache is not None:
            fine = cache.setdefault(cache_key, fine)
        assert isinstance(fine._backing, ExpressionFrameBacking) and isinstance(
            fine._backing.root, SourceFrameBinding
        )
        parent_positions = tuple(
            next(i for i, receiver in metric.axis_positions if receiver == position)
            for position in range(len(parent._schema.axes))
        )
        source_rules = tuple(
            (
                quantity.name,
                build_attribute_rule(
                    quantity.definition,
                    dict(graph.signature.declared_metrics),
                    bound.contributions,
                    occurrence=(index,),
                    by=fine_axes,
                    metric=".".join(target.path) if index == source_index else quantity.name,
                ),
            )
            for index, quantity in enumerate(quantities)
        )
        selected_rule = source_rules[source_index][1]
        from marivo._definition.graph import computation_fingerprint

        projection_ids.append(computation_fingerprint(projection._definition))
        endpoints.append(fine)
        projections.append(projection)
        positions.append(parent_positions)
        rules.append((("target", selected_rule),))
        all_rules.append(source_rules)
    if len(rules) == 1:
        endpoints.append(endpoints[0])
        projections.append(projections[0])
        positions.append(positions[0])
        rules.append(rules[0])
        all_rules.append(all_rules[0])
        source_indices.append(source_indices[0])
        source_sides.append(source_sides[0])
        projection_ids.append(projection_ids[0])
    require_rules_match(rules[0][0][1], rules[1][0][1], metric=".".join(target.path))
    operation = AttributeFrameValues(
        positions[0], positions[1], "outer", projection_ids[0], projection_ids[1]
    )
    return AttributeInputRequest(
        parent,
        (endpoints[0], endpoints[1]),
        (projections[0], projections[1]),
        operation,
        (rules[0], rules[1]),
        (all_rules[0], all_rules[1]) if groups is not None else None,
        (source_indices[0], source_indices[1]) if groups is not None else None,
        (source_sides[0], source_sides[1]) if groups is not None else None,
    )


def _single(
    parent: AnalysisFrame,
    target: DerivedOutput,
    by: tuple[Deferred | Grouping, ...] | Population,
    cache: dict[SourceGroupKey, AnalysisFrame],
    groups: dict[SourceGroupKey, SourceMetricGroup],
) -> AnalysisFrame:
    """Private graph-backed contribution entry used by the Frame API."""
    from marivo.analysis.core.attribute import pending_contribution_schema
    from marivo.analysis.dsl.frame import AnalysisFrame

    request = source_inputs(parent, target, by, cache, groups)
    operation = AttributeContributionValues(
        request.operation,
        request.rules,
        FrameAttributeTarget(
            target.path,
            target.target.mode,
            parent._schema.value_paths.index(target.path),
        ),
        target.target.mode == "level",
        request.source_rules,
        request.source_indices,
        request.source_sides,
    )
    from marivo.analysis.core.attribute import select_attribute_source_schema

    left_schema, right_schema = request.endpoints[0]._schema, request.endpoints[1]._schema
    if request.source_indices is not None:
        left_schema = select_attribute_source_schema(left_schema, request.source_indices[0])
        right_schema = select_attribute_source_schema(right_schema, request.source_indices[1])
    schema = pending_contribution_schema(left_schema, right_schema, parent._schema, operation)
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
    if not isinstance(by, (tuple, Population)) or (isinstance(by, tuple) and not by):
        reject(
            "a nonempty classification tuple or complete Population",
            repr(by),
            "Pass by=(field,) or one complete Population.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    if isinstance(by, Population):
        from marivo.analysis.dsl.population import _owner

        if _owner(by._definition) not in (None, parent._local.owner):
            reject(
                "a Population from this Session",
                repr(_owner(by._definition)),
                "Build the fine Population in the receiver's owning Session.",
                "frame.attribute",
                canonical_id="analysis_frame.attribute",
            )
    declaration = parent._derivation
    if declaration is None:
        ordinary_axes = tuple(
            (axis.token, axis.name)
            for axis in parent._schema.axes
            if not isinstance(axis, (CalendarDataAxis, IntervalDataAxis, PairedDataAxis))
        )
        reject(
            "a retained source declaration on this Frame instance",
            repr((parent._schema.value_paths, ordinary_axes)),
            "Use the same-process compute/persist return value, or supply the original Metric, where, membership rules, time scopes and comparison. Bridge the saved complete ordinary keys "
            + repr(ordinary_axes)
            + " with session.population_from(saved_frame, on=...) and in_population before observe/compare. Reconstruct temporal periods and their original correspondence explicitly from saved coordinate facts.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    failed = tuple(
        (path, dict(declaration.rejections).get(path, "unsupported source recipe"))
        for path in parent._schema.value_paths
        if path not in {item.path for item in declaration.outputs}
    )
    if failed:
        reject(
            "direct captured Metric or comparison outputs",
            repr(failed),
            "Select direct supported source outputs, or investigate these targets explicitly with population_from, in_population and a finer observe/compare.",
            "frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
    cache: dict[SourceGroupKey, AnalysisFrame] = {}
    groups = collect_source_groups(declaration)
    reports = tuple(_single(parent, target, by, cache, groups) for target in declaration.outputs)
    if len(reports) == 1:
        return reports[0]
    from marivo.analysis.dsl.frame_attribute_merge import merge_attribute_reports

    return merge_attribute_reports(reports)
