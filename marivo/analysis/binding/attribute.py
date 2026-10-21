"""Bind explicit default investigations without evaluating an intermediate Frame."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from ibis.common.deferred import Deferred

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import EntityCount
from marivo._definition.relations import AttributeKeys, ObserveQuery, PopulationProgram
from marivo._definition.types import AxisBinding, CalendarCoordinate, IntervalCoordinate
from marivo._grouping import Grouping
from marivo._temporal import TimeScope
from marivo.analysis.core.attribute import attribute_schema
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    OperationFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.core.frame_expression import AttributeFrameValues
from marivo.analysis.core.frame_structure import axis_role
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundQuantity, BoundSignature, reject
from marivo.analysis.dsl._frame_inputs import FrameKeyInput
from marivo.analysis.dsl.attribute import AttributeEndpoint
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.dsl.frame_population import population_from
from marivo.analysis.dsl.frame_sources import _capture, observe
from marivo.analysis.dsl.population import (
    Population,
    _axes,
    in_population,
    product,
)
from marivo.analysis.dsl.temporal import CalendarAxis
from marivo.semantic.expressions import Entity

if TYPE_CHECKING:
    from marivo.analysis.binding.attribute_rules import AttributeRule
    from marivo.analysis.session.core import Session
    from marivo.semantic._deferred_semantics import MetricFacts


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


def _metric_roles(facts: MetricFacts) -> tuple[object, ...]:
    """Compare consumed roles/policies while ignoring definition versions and coefficients."""
    from marivo._definition.expressions import (
        MemberCount,
        MetricWindow,
        PerReduce,
        PerWeightedMean,
        SourceAggregate,
        SourceWeightedMean,
    )
    from marivo._definition.graph import walk

    roles: list[object] = [tuple(root.ref.key for root in facts.roots)]
    for node in walk((facts.node,), resolve=dict(facts._declared).get).order:
        if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            time = node.time
            roles.append(
                (
                    type(node).__name__,
                    (time.field.ref.key, tuple(r.ref.key for r in time.path), time.at)
                    if time is not None
                    else None,
                )
            )
        if isinstance(node, SourceAggregate):
            roles.append((node.reducer, node.nulls, node.empty, node.q, node.rule_version))
        elif isinstance(node, SourceWeightedMean):
            roles.append((node.nulls, node.zero_denominator, node.rule_version))
        elif isinstance(node, (PerReduce, PerWeightedMean)):
            samples = node.samples
            sample_axes = samples if isinstance(samples, tuple) else samples.axes
            roles.append(
                (
                    type(node).__name__,
                    tuple(
                        (
                            a.entity.ref.key,
                            a.field.ref.key if a.field is not None else None,
                            tuple(r.ref.key for r in a.path),
                            a.role,
                            a.at,
                        )
                        if isinstance(a, AxisBinding)
                        else _axis_identity(a)
                        for a in sample_axes
                    ),
                )
            )
            roles.append((node.nulls, node.rule_version))
            if isinstance(node, PerReduce):
                roles.append((node.reducer, node.q, node.empty, node.include_null))
        elif isinstance(node, MemberCount):
            roles.append(
                (
                    "member_count",
                    tuple(_axis_identity(axis) for axis in node.population.axes),
                )
            )
        elif isinstance(node, MetricWindow):
            roles.append(("window", node.rule))
    return tuple(roles)


def _domain(frame: AnalysisFrame) -> AnalysisFrame:
    """Keep actual parent keys, without evaluating an unrequested parent formula."""
    assert isinstance(frame._backing, ExpressionFrameBacking)
    binding = frame._backing.root
    assert isinstance(binding, SourceFrameBinding)
    graph = binding.graph
    observation = graph.signature.observation
    assert observation is not None
    contributions = tuple(
        replace(
            c,
            occurrence=(i,),
            logical_occurrence=None,
            definition=EntityCount(c.root, c.time),
            coordinates=c.coordinates[: c.output_axis_count],
            sample_axes=(),
            memberships=observation.coordinates.memberships,
            coverage=(),
            calendars=tuple(
                (slot, axis) for slot, axis in c.calendars if axis in graph.signature.axes
            ),
        )
        for i, c in enumerate(observation.contributions)
    )
    if not contributions:
        return frame
    quantities = tuple(
        BoundQuantity(f"parent_{i}", c.definition, "1") for i, c in enumerate(contributions)
    )
    metrics = tuple((q.name, q.definition) for q in quantities)
    root = ObserveQuery(
        metrics,
        graph.signature.axes,
        graph.signature.owner,
        count_populations=graph.root.count_populations
        if isinstance(graph.root, ObserveQuery)
        else (),
    )
    safe = BoundGraph(
        root,
        BoundSignature(
            graph.signature.owner,
            quantities,
            graph.signature.axes,
            observation=replace(observation, contributions=contributions),
        ),
    )
    return _capture(frame._local, safe, binding.state, binding.connection, binding.frame_key_inputs)


def _attribute(
    session: Session,
    left: AttributeEndpoint,
    right: AttributeEndpoint,
    *,
    by: tuple[Deferred | Grouping, ...] | Population,
    parents: Population | AnalysisFrame | None,
    parent_on: tuple[tuple[FrameAxis, Deferred | Entity, Deferred | Entity], ...],
    parent_how: Literal["exact", "outer"],
    contribution: bool,
    prepare_inputs: bool = False,
) -> AnalysisFrame | AttributeInputRequest:
    location = "session.attribute"

    def fail(expected: str, received: str, repair: str) -> None:
        reject(expected, received, repair, location)

    if type(contribution) is not bool:
        fail(
            "a strict boolean contribution", repr(contribution), "Pass contribution=False or True."
        )
    if contribution and not prepare_inputs:
        from marivo.analysis.core.attribute import pending_contribution_schema
        from marivo.analysis.core.frame_expression import AttributeContributionValues

        prepared = _attribute(
            session,
            left,
            right,
            by=by,
            parents=parents,
            parent_on=parent_on,
            parent_how=parent_how,
            contribution=True,
            prepare_inputs=True,
        )
        assert isinstance(prepared, AttributeInputRequest)
        contribution_operation = AttributeContributionValues(prepared.operation, prepared.rules)
        schema = pending_contribution_schema(
            prepared.endpoints[0]._schema,
            prepared.endpoints[1]._schema,
            prepared.parent._schema,
            contribution_operation,
        )
        return AnalysisFrame._from_expression(
            session._runtime.local,
            schema,
            OperationFrameBinding(contribution_operation, (prepared.parent, *prepared.endpoints)),
        )
    if type(left) is not AttributeEndpoint or type(right) is not AttributeEndpoint:
        fail(
            "two explicit AttributeEndpoint records",
            repr((type(left).__name__, type(right).__name__)),
            "Declare each endpoint with its own metrics and scopes.",
        )
    for endpoint in (left, right):
        if endpoint.during is not None and not isinstance(
            endpoint.during, (TimeScope, CalendarAxis)
        ):
            fail(
                "an explicit endpoint TimeScope, CalendarAxis or None",
                repr(endpoint.during),
                "Construct the endpoint period with session.time_scope(...).",
            )
    if set(left.metrics) != set(right.metrics):
        fail(
            "equal nonempty endpoint metric name sets",
            repr((tuple(left.metrics), tuple(right.metrics))),
            "Name the same Metrics on both endpoints.",
        )
    if parent_how not in ("exact", "outer"):
        fail(
            "parent_how='exact' or 'outer'",
            repr(parent_how),
            "Choose an explicit parent correspondence policy.",
        )
    if not isinstance(by, (tuple, Population)) or not (
        by.axes if isinstance(by, Population) else by
    ):
        fail(
            "a nonempty classification tuple or one complete Population",
            repr(by),
            "Provide new joint business classifications.",
        )
    local, state = session._runtime.local, session.catalog._state
    from marivo.semantic._deferred_semantics import consume_metric_in_state

    for name in left.metrics:
        metrics = (left.metrics[name], right.metrics[name])
        if not all(isinstance(metric, Deferred) for metric in metrics):
            fail(
                "resolved numeric Metric expressions",
                repr(tuple(type(metric).__name__ for metric in metrics)),
                "Resolve each endpoint Metric from the receiving Session.",
            )
        first, second = (consume_metric_in_state(metric, state) for metric in metrics)
        if first.unit != second.unit or _metric_roles(first) != _metric_roles(second):
            fail(
                f"compatible root roles and units for {name}",
                repr(
                    (
                        _metric_roles(first),
                        first.unit,
                        _metric_roles(second),
                        second.unit,
                    )
                ),
                "Provide same-role endpoint Metrics with equal units.",
            )
    if parents is not None and not isinstance(parents, (Population, AnalysisFrame)):
        fail(
            "a Population, AnalysisFrame or None parent domain",
            type(parents).__name__,
            "Provide an explicit selected parent domain.",
        )
    if type(parent_on) is not tuple or any(type(p) is not tuple or len(p) != 3 for p in parent_on):
        fail(
            "a tuple of (FrameAxis, left_target, right_target)",
            repr(parent_on),
            "Map each ordinary parent axis once.",
        )
    population_parent = parents if isinstance(parents, Population) else None
    if not isinstance(parents, AnalysisFrame) and parent_on:
        fail(
            "parent_on=() for Population or None",
            repr(parent_on),
            "Use the Population's existing binding.",
        )
    if isinstance(parents, AnalysisFrame):
        parent = parents
    elif isinstance(parents, Population):
        parent = session.frame(parents)
    else:
        parent = session.values(by=(), columns={"parent": "1"}, rows=(((), {"parent": 1}),))
    if parent._local.owner != local.owner:
        fail(
            "parents owned by the receiving Session",
            repr(parent._local.owner),
            "Use the parent domain's owning Session.",
        )
    from marivo.analysis.core.data_schema import IntervalDataAxis, PairedDataAxis

    if any(
        isinstance(axis, IntervalDataAxis)
        or (
            isinstance(axis, PairedDataAxis)
            and (
                isinstance(axis.left, IntervalDataAxis) or isinstance(axis.right, IntervalDataAxis)
            )
        )
        for axis in parent._schema.axes
    ):
        fail(
            "ordinary or finite calendar parent coordinates",
            "an interval or paired-interval parent axis",
            "Reduce the interval axis before selecting parents, or use an explicit CalendarAxis parent and endpoint scopes.",
        )
    ordinary = tuple(
        i for i, a in enumerate(parent._schema.axes) if axis_role(a) in ("field", "identity")
    )
    calendars = tuple(
        i for i, a in enumerate(parent._schema.axes) if axis_role(a) in ("calendar", "paired")
    )
    if len(calendars) > 1:
        fail(
            "at most one calendar-bearing parent axis",
            repr(calendars),
            "Select a single explicit parent calendar.",
        )
    if isinstance(parents, AnalysisFrame):
        mapped = tuple(parent._axis_index(p[0]) for p in parent_on)
        if len(set(mapped)) != len(mapped) or set(mapped) != set(ordinary):
            fail(
                "every complete ordinary parent axis exactly once",
                repr(mapped),
                "Map all ordinary axes and omit calendar axes.",
            )
    else:
        mapped = ordinary
    endpoints: list[AnalysisFrame] = []
    domains: list[AnalysisFrame] = []
    parent_positions: list[tuple[int, ...]] = []
    projection_ids: list[str] = []
    projections: list[Population] = []
    fine_identities_by_side: list[tuple[tuple[object, ...], ...]] = []
    sides: tuple[tuple[Literal["left", "right"], AttributeEndpoint], ...] = (
        ("left", left),
        ("right", right),
    )

    def capture(
        endpoint: AttributeEndpoint,
        projected: Population,
        classifications: tuple[Deferred | Grouping, ...] | Population,
        *,
        complete_calendar: bool = True,
        parent_only: bool = False,
    ) -> AnalysisFrame:
        rules = endpoint.memberships
        if parent_only:
            from marivo._definition.relations import MembershipBinding

            domain_axes = (
                classifications._definition.axes
                if isinstance(classifications, Population)
                else _axes(classifications)
            )
            rules = tuple(
                rule
                for rule in rules
                if isinstance(rule._binding, MembershipBinding)
                and all(
                    AxisBinding(read.entity, read.field, read.path, "field", read.at) in domain_axes
                    for read in rule._binding.fields
                )
            )
        return observe(
            local,
            state,
            session._runtime.connection_config,
            {name: endpoint.metrics[name] for name in left.metrics},
            by=classifications,
            where=endpoint.where,
            memberships=rules,
            timezone=session.timezone,
            calendar_snapshots=session._sources().period_calendar_snapshots,
            _parent_filter=in_population(projected),
            _complete_calendar=complete_calendar,
            _endpoint_scope=endpoint.during if isinstance(endpoint.during, TimeScope) else None,
        )

    for side, endpoint in sides:
        if calendars and not isinstance(endpoint.during, CalendarAxis):
            fail(
                "an explicit CalendarAxis for each calendar parent endpoint",
                repr(endpoint.during),
                "Supply the saved endpoint's complete finite calendar.",
            )
        if not calendars and isinstance(endpoint.during, CalendarAxis):
            fail(
                "TimeScope or None without a calendar parent",
                "standalone CalendarAxis",
                "Select a calendar-bearing parent first.",
            )
        if isinstance(parents, AnalysisFrame):
            targets = {
                parent._axis_index(p[0]): p[1] if side == "left" else p[2] for p in parent_on
            }
            base = population_from(
                local, state, parent, on=tuple((parent.axes[i], targets[i]) for i in ordinary)
            )
        elif population_parent is not None:
            # Calendar axes are projected only by Attribute; ordinary target
            # bindings still come from the Population's original authority.
            ordinary_axes = tuple(
                a for a in population_parent._definition.axes if isinstance(a, AxisBinding)
            )
            from marivo._definition.relations import FrameKeyAxis

            base = Population(
                PopulationProgram(
                    AttributeKeys(
                        parent._input_id,
                        tuple(
                            FrameKeyAxis(
                                parent._schema.axes[i].token,
                                tuple(c.name for c in parent._schema.axes[i].columns),
                                axis,
                            )
                            for i, axis in zip(ordinary, ordinary_axes, strict=True)
                        ),
                    ),
                    local.owner,
                    ordinary_axes,
                ),
                (FrameKeyInput(parent._input_id, parent),),
            )
        else:
            base = population_from(local, state, parent, on=())
        assert isinstance(base._definition, PopulationProgram)
        keys = base._definition.root
        from marivo._definition.relations import FrameKeys

        assert isinstance(keys, (FrameKeys, AttributeKeys))
        for binding in keys.bindings:
            source_axis = next(axis for axis in parent._schema.axes if axis.token == binding.token)
            if source_axis.at != binding.target.at:
                fail(
                    "ordinary parent targets with the captured time anchor",
                    repr((source_axis.at, binding.target.at)),
                    "Map the selected axis to a target with the same explicit time interpretation.",
                )
        calendar: CalendarCoordinate | None = None
        if calendars:
            from marivo.analysis.dsl.population import _calendar_population

            assert isinstance(endpoint.during, CalendarAxis)
            calendar_axis = _calendar_population(endpoint.during)._definition.axes[0]
            assert isinstance(calendar_axis, CalendarCoordinate)
            calendar = calendar_axis
        projected = Population(
            PopulationProgram(
                AttributeKeys(
                    parent._input_id,
                    keys.bindings,
                    parent._schema.axes[calendars[0]].token if calendars else None,
                    calendar,
                    side,
                    parent_how,
                ),
                local.owner,
                (*base._definition.axes, *((calendar,) if calendar else ())),
            ),
            (FrameKeyInput(parent._input_id, parent),),
        )
        from marivo._definition.graph import computation_fingerprint

        projection_ids.append(computation_fingerprint(projected._definition))
        projections.append(projected)
        fine_axes = by._definition.axes if isinstance(by, Population) else _axes(by)
        if any(isinstance(axis, CalendarCoordinate) for axis in fine_axes):
            fail(
                "new business classifications without calendar refinement",
                repr(fine_axes),
                "Keep calendars in the explicit parent domain and during inputs.",
            )
        fine_identities = tuple(_axis_identity(axis) for axis in fine_axes)
        parent_identities = {_axis_identity(axis) for axis in projected._definition.axes}
        if len(set(fine_identities)) != len(fine_identities) or parent_identities.intersection(
            fine_identities
        ):
            fail(
                "distinct new business axes after the parent coordinates",
                repr(fine_axes),
                "Remove duplicate parent or by classifications.",
            )
        # Parent grouping is explicit and independent of endpoint where/members.
        parent_by = projected if population_parent is not None else tuple(projected.axes)
        fine_by = product(projected, by) if isinstance(by, Population) else ((*projected.axes, *by))

        # The complete parent candidate policy does not manufacture a product
        # for an ordinary tuple fine axis: only observed fine combinations exist.
        fine = capture(endpoint, projected, fine_by, complete_calendar=isinstance(by, Population))
        if not prepare_inputs:
            domains.append(_domain(capture(endpoint, projected, parent_by, parent_only=True)))
        assert isinstance(fine._backing, ExpressionFrameBacking)
        fine_binding = fine._backing.root
        assert isinstance(fine_binding, SourceFrameBinding)
        axes = fine_binding.graph.signature.axes
        positions = tuple(axes.index(a) for a in projected._definition.axes)
        positions = tuple(
            positions[ordinary.index(i)] if i in ordinary else positions[-1]
            for i in range(len(parent._schema.axes))
        )
        # Reject a by coordinate which already owns a parent slot.
        if len(axes) != len(projected._definition.axes) + len(fine_axes):
            fail(
                "distinct new business axes after the parent coordinates",
                repr(axes),
                "Remove duplicate parent or by classifications.",
            )
        fine_identities_by_side.append(
            tuple(_axis_identity(axis) for i, axis in enumerate(axes) if i not in positions)
        )
        endpoints.append(fine)
        parent_positions.append(positions)
    if fine_identities_by_side[0] != fine_identities_by_side[1]:
        fail(
            "compatible complete semantic fine-axis identities on both endpoints",
            repr(tuple(fine_identities_by_side)),
            "Use the same resolved identity root, classification path and time role; different cohorts of that root are allowed.",
        )
    operation = AttributeFrameValues(
        parent_positions[0], parent_positions[1], parent_how, projection_ids[0], projection_ids[1]
    )
    if prepare_inputs:
        from marivo.analysis.binding.attribute_rules import (
            build_attribute_rule,
            require_rules_match,
        )

        named_rules: list[tuple[tuple[str, AttributeRule], ...]] = []
        for fine, positions in zip(endpoints, parent_positions, strict=True):
            assert isinstance(fine._backing, ExpressionFrameBacking)
            fine_source = fine._backing.root
            assert isinstance(fine_source, SourceFrameBinding)
            graph = fine_source.graph
            observation = graph.signature.observation
            assert observation is not None
            assert all(
                isinstance(quantity, BoundQuantity) for quantity in graph.signature.quantities
            )
            quantities = tuple(
                quantity
                for quantity in graph.signature.quantities
                if isinstance(quantity, BoundQuantity)
            )
            rules = tuple(
                (
                    quantity.name,
                    build_attribute_rule(
                        quantity.definition,
                        dict(graph.signature.declared_metrics),
                        observation.contributions,
                        occurrence=(index,),
                        by=tuple(
                            axis
                            for i, axis in enumerate(graph.signature.axes)
                            if i not in positions
                        ),
                        metric=quantity.name,
                    ),
                )
                for index, quantity in enumerate(quantities)
            )
            named_rules.append(rules)
        for (name, first_rule), (_, second_rule) in zip(*named_rules, strict=True):
            require_rules_match(first_rule, second_rule, metric=name)
        return AttributeInputRequest(
            parent,
            (endpoints[0], endpoints[1]),
            (projections[0], projections[1]),
            operation,
            (named_rules[0], named_rules[1]),
        )
    schema = attribute_schema(endpoints[0]._schema, endpoints[1]._schema, parent._schema, operation)
    return AnalysisFrame._from_expression(
        local,
        schema,
        OperationFrameBinding(
            operation, (parent, domains[0], domains[1], endpoints[0], endpoints[1])
        ),
    )


@dataclass(frozen=True, slots=True)
class AttributeInputRequest:
    """Private explicit preparation request; this is not an Attribute result."""

    parent: AnalysisFrame
    endpoints: tuple[AnalysisFrame, AnalysisFrame]
    projections: tuple[Population, Population]
    operation: AttributeFrameValues
    rules: tuple[tuple[tuple[str, AttributeRule], ...], tuple[tuple[str, AttributeRule], ...]]


def attribute(
    session: Session,
    left: AttributeEndpoint,
    right: AttributeEndpoint,
    *,
    by: tuple[Deferred | Grouping, ...] | Population,
    parents: Population | AnalysisFrame | None,
    parent_on: tuple[tuple[FrameAxis, Deferred | Entity, Deferred | Entity], ...],
    parent_how: Literal["exact", "outer"],
    contribution: bool,
) -> AnalysisFrame:
    result = _attribute(
        session,
        left,
        right,
        by=by,
        parents=parents,
        parent_on=parent_on,
        parent_how=parent_how,
        contribution=contribution,
    )
    assert isinstance(result, AnalysisFrame)
    return result


def attribute_input_request(
    session: Session,
    left: AttributeEndpoint,
    right: AttributeEndpoint,
    *,
    by: tuple[Deferred | Grouping, ...] | Population,
    parents: Population | AnalysisFrame | None = None,
    parent_on: tuple[tuple[FrameAxis, Deferred | Entity, Deferred | Entity], ...] = (),
    parent_how: Literal["exact", "outer"] = "exact",
) -> AttributeInputRequest:
    """Capture only the explicit common-input preparation required by P3."""
    result = _attribute(
        session,
        left,
        right,
        by=by,
        parents=parents,
        parent_on=parent_on,
        parent_how=parent_how,
        contribution=True,
        prepare_inputs=True,
    )
    assert isinstance(result, AttributeInputRequest)
    return result
